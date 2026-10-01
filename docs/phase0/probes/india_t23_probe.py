"""Phase 0 feed probe: India Tier 2 (EOD digest) + Tier 3 (weekly digest) sources.

Fetches every candidate endpoint once (plain httpx, desktop Chrome UA, <=1 req/s
per host), records transport facts (status, content-type, ETag, Last-Modified,
TLS behaviour, size), extracts item dates with a per-endpoint regex/JSON rule
purely to MEASURE cadence/archive depth, and saves the first ~50KB of each body
to docs/phase0/raw/india_t23/ as evidence. Endpoints that block httpx are
re-tested for reachability with headless Chrome (Playwright, channel="chrome").

This is a measurement script, not a scraper: it does not follow pagination,
submit forms, or log in.

Run:
  uv run --project "Z:\\Data Pipelines\\events_bot" python docs/phase0/probes/india_t23_probe.py
Writes:
  docs/phase0/raw/india_t23/<id>.<ext>       (first 50KB of each response)
  docs/phase0/raw/india_t23/_summary.json    (one record per endpoint)

PIB echo / lag measurement is a separate script (PRID window scan):
  docs/phase0/probes/india_t23_pib_scan.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "india_t23"
RAW.mkdir(parents=True, exist_ok=True)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TODAY = dt.datetime.now(IST).date()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA,
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}
SAMPLE_BYTES = 50_000

MON = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ---------------------------------------------------------------- date rules
def d_dmy(sep):  # dd<sep>mm<sep>yyyy
    rx = re.compile(r"\b(\d{2})" + re.escape(sep) + r"(\d{2})" + re.escape(sep) + r"(20\d{2})\b")
    return lambda text: [dt.date(int(y), int(m), int(d)) for d, m, y in rx.findall(text)
                         if 1 <= int(m) <= 12 and 1 <= int(d) <= 31]


def d_ymon_d(text):  # 2026-Sep-18 (CDSCO)
    return [dt.date(int(y), MON[m.lower()], int(d))
            for y, m, d in re.findall(r"\b(20\d{2})-([A-Z][a-z]{2})-(\d{2})\b", text)]


def d_d_mon_y(text):  # 18-Sep-2026 (SIAM)
    return [dt.date(int(y), MON[m.lower()], int(d))
            for d, m, y in re.findall(r"\b(\d{2})-([A-Z][a-z]{2})-(20\d{2})\b", text)]


def d_month_y(text):  # "September, 2026" (FADA, month granularity -> 1st of month)
    out = []
    for m, y in re.findall(r"\b(January|February|March|April|May|June|July|August|September|"
                           r"October|November|December), (20\d{2})\b", text):
        out.append(dt.date(int(y), MON[m[:3].lower()], 1))
    return out


def d_epoch_filename(text):  # PPAC download.php?file=<dir>/<unix>_name.pdf
    return [dt.datetime.fromtimestamp(int(s), IST).date()
            for s in re.findall(r"download\.php\?file=[a-z_]+/(\d{10})_", text)]


def d_iso(text):  # 2026-09-28 / 2026-09-28 04:05:56 / 2026-09-01T11:34
    return [dt.date(int(y), int(m), int(d))
            for y, m, d in re.findall(r"\b(20\d{2})-(\d{2})-(\d{2})[ T\"]", text)]


def d_wp_json(text):
    j = json.loads(text)
    return [dt.date.fromisoformat(p["post_date_gmt"][:10]) for p in j.get("posts", [])]


def d_eci(text):
    j = json.loads(text)
    out = []
    for d in j["results"]["data"]:
        m = re.search(r"(\d{1,2}) ([A-Z][a-z]{2}) (20\d{2})", d.get("date_of_creation", ""))
        if m:
            out.append(dt.date(int(m.group(3)), MON[m.group(2).lower()], int(m.group(1))))
    return out


def d_rss(text):
    f = feedparser.parse(text)
    return [dt.date(*e.published_parsed[:3]) for e in f.entries if e.get("published_parsed")]


def d_none(_):
    return []


# ---------------------------------------------------------------- endpoints
# (id, source, tier, kind, url, date_rule, note)
EP = [
    # TRAI
    ("trai_rss", "TRAI", 2, "rss", "https://www.trai.gov.in/rss.xml", d_rss, "site-wide Drupal feed"),
    ("trai_press", "TRAI", 2, "html", "https://www.trai.gov.in/notifications/press-release", d_dmy("/"), "Date of Publication"),
    ("trai_consult", "TRAI", 2, "html", "https://www.trai.gov.in/release-publication/consultation", d_dmy("/"), "Release Date"),
    # IRDAI (Liferay)
    ("irdai_press", "IRDAI", 2, "html", "https://irdai.gov.in/press-releases", d_dmy("-"), ""),
    ("irdai_circulars", "IRDAI", 2, "html", "https://irdai.gov.in/circulars", d_dmy("-"), ""),
    ("irdai_mbf", "IRDAI", 2, "html", "https://irdai.gov.in/monthly-business-figures1", d_dmy("-"), "monthly business figures"),
    # PFRDA (Liferay)
    ("pfrda_circulars", "PFRDA", 2, "html", "https://www.pfrda.org.in/regulatory-framework/circulars/active-circulars", d_dmy("-"), "Issue Date"),
    ("pfrda_press", "PFRDA", 2, "html", "https://www.pfrda.org.in/media/press-releases", d_dmy("-"), "Issue Date"),
    # NPCI (Akamai + F5 TSPD; httpx expected 403)
    ("npci_home", "NPCI", 2, "html", "https://www.npci.org.in/", d_none, "httpx blocked?"),
    ("npci_upi_stats_api", "NPCI", 2, "api",
     "https://www.npci.org.in/api/product-statistic/tab/detail?product_name=upi&tab_name=product-statistics-upi"
     "&year_range=2026-27&excel_type=monthly&page_no=1&page_size=10&locale=en", d_iso, "JSON behind SPA"),
    ("npci_upi_circ_api", "NPCI", 2, "api",
     "https://www.npci.org.in/api/circulars/upi?pageNum=1&year=2026&sort=desc&size=10&locale=en", d_none, "JSON behind SPA"),
    # CERC (IIS static HTML; flaky TLS)
    ("cerc_orders_2026", "CERC", 2, "html", "https://cercind.gov.in/recent_orders.html", d_dmy("."), "order date + upload date"),
    ("cerc_draft_regs", "CERC", 2, "html", "https://cercind.gov.in/Draft_reg.html", d_dmy("."), ""),
    ("cerc_whatsnew", "CERC", 2, "html", "https://cercind.gov.in/viewall.html", d_none, ""),
    ("cerc_press", "CERC", 2, "html", "https://cercind.gov.in/press_releases.html", d_none, "stale?"),
    # Ministry of Power (Next.js + headless WordPress JSON)
    ("power_whatsnew_json", "MoP", 2, "api", "https://www.powermin.gov.in/cms/wp-json/post-page/whats_new", d_wp_json, "undocumented WP JSON"),
    ("power_docs_json", "MoP", 2, "api", "https://www.powermin.gov.in/cms/wp-json/post-page/documents?sort=acf&limit=10&page=1", d_wp_json, ""),
    ("power_wp_feed", "MoP", 2, "rss", "https://www.powermin.gov.in/cms/feed/", d_rss, "WP feed path test"),
    # PNGRB
    ("pngrb_whatsnew_js", "PNGRB", 2, "api", "https://www.pngrb.gov.in/eng-web/whatsnew/whats-new-json.js", d_iso, "JS-wrapped JSON array"),
    # PPAC
    ("ppac_home", "PPAC", 2, "html", "https://ppac.gov.in/", d_epoch_filename, "upload epoch embedded in file names"),
    # DoT
    ("dot_docs_json", "DoT", 2, "api", "https://www.dot.gov.in/cms/wp-json/post-page/documents?sort=acf&limit=10&page=1", d_wp_json, "slow (~45s)"),
    ("dot_whatsnew_json", "DoT", 2, "api", "https://www.dot.gov.in/cms/wp-json/post-page/whats_new", d_wp_json, ""),
    # MeitY
    ("meity_docs_json", "MeitY", 2, "api", "https://www.meity.gov.in/cms/wp-json/post-page/documents?sort=acf&limit=10&page=1", d_wp_json, ""),
    ("meity_whatsnew_json", "MeitY", 2, "api", "https://www.meity.gov.in/cms/wp-json/post-page/whats_new", d_wp_json, ""),
    # CDSCO
    ("cdsco_alerts", "CDSCO", 2, "html", "https://cdsco.gov.in/opencms/opencms/en/Notifications/Alerts/", d_ymon_d, ""),
    ("cdsco_public_notices", "CDSCO", 2, "html", "https://cdsco.gov.in/opencms/opencms/en/Notifications/Public-Notices/", d_ymon_d, ""),
    ("cdsco_nsq_json", "CDSCO", 2, "api", "https://cdscoonline.gov.in/CDSCO/publicNsqDrugTable", d_none, "latest-month NSQ table (JSON)"),
    # SIAM / FADA
    ("siam_press", "SIAM", 2, "html", "https://www.siam.in/news-&-updates/press-releases", d_d_mon_y, ""),
    ("fada_press", "FADA", 2, "html", "https://www.fada.in/press-release-list.php", d_month_y, "month granularity only"),
    # Tier 3
    ("niti_rss", "NITI Aayog", 3, "rss", "https://www.niti.gov.in/rss.xml", d_rss, "site-wide Drupal feed"),
    ("niti_reports", "NITI Aayog", 3, "html", "https://www.niti.gov.in/publications/division-reports", d_none, ""),
    ("prs_billtrack", "PRS", 3, "html", "https://prsindia.org/billtrack", d_none, "status per bill in listing"),
    ("prs_bill_page", "PRS", 3, "html",
     "https://prsindia.org/billtrack/the-foreign-contribution-regulation-amendment-bill-2026", d_none, "per-bill stage timeline"),
    ("prs_rss", "PRS", 3, "rss", "https://prsindia.org/rss.xml", d_rss, "feed path test"),
    ("eci_press_api", "ECI", 3, "api",
     "https://www.eci.gov.in/eci-backend/public/api/get-event?categories=m5rXCuu1vgBmEBgVdj4HZQ%3D%3D"
     "&limit=wL6Vckpru9LxFAvqEyedJg%3D%3D", d_eci, "opaque encrypted params captured from homepage XHR"),
    ("fincom_16fc", "Finance Commission", 3, "html", "https://fincomindia.nic.in/commission-reports-sixteenth", d_none, ""),
    ("fincom_notif", "Finance Commission", 3, "html", "https://fincomindia.nic.in/notification-and-media", d_none, ""),
    # PIB (secondary channel)
    ("pib_rss_pr", "PIB", 1, "rss", "https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", d_none, "Lang=1 requested"),
    ("pib_allrel", "PIB", 1, "html", "https://www.pib.gov.in/allRel.aspx?reg=3&lang=1", d_none, "today's releases grouped by ministry"),
]


def ext_for(ct: str) -> str:
    ct = (ct or "").lower()
    for k, e in (("json", "json"), ("rss", "xml"), ("xml", "xml"), ("javascript", "js"), ("pdf", "pdf")):
        if k in ct:
            return e
    return "html"


def fetch(client: httpx.Client, url: str):
    """GET with one retry; on TLS failure record it and retry with verify=False."""
    tls = "ok"
    for attempt in range(2):
        try:
            t0 = time.time()
            r = client.get(url)
            return r, tls, time.time() - t0, None
        except httpx.ConnectError as e:
            if "SSL" in str(e) or "CERTIFICATE" in str(e).upper():
                tls = f"TLS error attempt {attempt + 1}: {str(e)[:100]}"
                continue
            return None, tls, 0, f"{type(e).__name__}: {e}"
        except Exception as e:  # timeouts etc.
            return None, tls, 0, f"{type(e).__name__}: {e}"
    try:  # last resort: no verification, to separate cert problems from transport problems
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=90, verify=False) as c2:
            t0 = time.time()
            r = c2.get(url)
            return r, tls + " | verify=False OK", time.time() - t0, None
    except Exception as e:
        return None, tls, 0, f"{type(e).__name__}: {e}"


def summarise_dates(dates):
    dates = sorted({d for d in dates if d <= TODAY + dt.timedelta(days=1)})
    if not dates:
        return {}
    last30 = [d for d in dates if (TODAY - d).days <= 30]
    last90 = [d for d in dates if (TODAY - d).days <= 90]
    return {"newest": dates[-1].isoformat(), "oldest_on_page": dates[0].isoformat(),
            "distinct_dates": len(dates), "dates_last30d": len(last30), "dates_last90d": len(last90)}


def run_httpx():
    out = []
    last_hit = defaultdict(float)
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=90) as client:
        for eid, src, tier, kind, url, rule, note in EP:
            host = urlparse(url).hostname
            wait = 1.1 - (time.time() - last_hit[host])
            if wait > 0:
                time.sleep(wait)
            r, tls, secs, err = fetch(client, url)
            last_hit[host] = time.time()
            rec = {"id": eid, "source": src, "tier": tier, "kind": kind, "url": url, "note": note,
                   "fetched_at_ist": dt.datetime.now(IST).isoformat(timespec="seconds"), "tls": tls}
            if err:
                rec["error"] = err
                print(f"[{eid}] ERROR {err[:120]}")
                out.append(rec)
                continue
            body = r.content
            ct = r.headers.get("content-type", "")
            rec.update({
                "status": r.status_code, "final_url": str(r.url), "content_type": ct,
                "bytes": len(body), "seconds": round(secs, 1),
                "etag": r.headers.get("etag"), "last_modified": r.headers.get("last-modified"),
                "server": r.headers.get("server"), "cache_control": r.headers.get("cache-control"),
                "redirects": [str(h.url) for h in r.history],
            })
            sample = RAW / f"{eid}.{ext_for(ct)}"
            sample.write_bytes(body[:SAMPLE_BYTES])
            rec["sample"] = str(sample.relative_to(ROOT.parent.parent)).replace("\\", "/")
            text = r.text
            try:
                rec["dates"] = summarise_dates(rule(text)) if r.status_code == 200 else {}
            except Exception as e:
                rec["dates_error"] = f"{type(e).__name__}: {e}"
            # endpoint-specific facts
            if kind == "rss" and r.status_code == 200:
                f = feedparser.parse(body)
                rec["rss_items"] = len(f.entries)
                rec["rss_bozo"] = bool(f.bozo)
                rec["rss_language_hint"] = (f.entries[0].title[:60] if f.entries else None)
            if eid == "cdsco_nsq_json" and r.status_code == 200:
                j = json.loads(text)
                rec["nsq_rows"] = j.get("iTotalRecords")
                rec["nsq_months"] = sorted({x.get("dt_reporting_month_year") for x in j.get("aaData", [])})
            if eid == "pngrb_whatsnew_js" and r.status_code == 200:
                rec["items"] = len(re.findall(r'"created_date"', text))
            if eid == "eci_press_api" and r.status_code == 200:
                rec["total_records"] = json.loads(text)["results"]["total_records"]
            if eid.endswith("_json") and "wp-json" in url and r.status_code == 200:
                rec["total_items"] = json.loads(text).get("total_items")
            print(f"[{eid}] {r.status_code} {ct[:30]} {len(body)}B {secs:.1f}s "
                  f"etag={bool(rec['etag'])} lm={rec['last_modified']} tls={tls[:40]} {rec.get('dates', {})}")
            out.append(rec)
    return out


async def run_playwright(urls):
    """Reachability only: load page, report status + same-host XHR/JSON endpoints seen."""
    from playwright.async_api import async_playwright
    res = {}
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, channel="chrome")
        for label, ua in (("headless-default-UA", None), ("desktop-UA", UA)):
            ctx = await b.new_context(locale="en-US", user_agent=ua) if ua else await b.new_context(locale="en-US")
            pg = await ctx.new_page()
            for u in urls:
                xhr = []
                host = urlparse(u).hostname
                pg.on("response", lambda r, xhr=xhr, host=host: xhr.append((r.status, r.url))
                      if r.request.resource_type in ("xhr", "fetch") and urlparse(r.url).hostname == host
                      and "TSPD" not in r.url else None)
                try:
                    r = await pg.goto(u, wait_until="networkidle", timeout=60000)
                    await pg.wait_for_timeout(2000)
                    res[f"{label}:{u}"] = {"status": r.status if r else None, "final_url": pg.url,
                                           "title": await pg.title(), "xhr": sorted({x[1] for x in xhr})[:25]}
                except Exception as e:
                    res[f"{label}:{u}"] = {"error": f"{type(e).__name__}: {e}"}
                print("[pw]", label, u, res[f"{label}:{u}"].get("status"), res[f"{label}:{u}"].get("title"))
                await asyncio.sleep(1.5)
            await ctx.close()
        await b.close()
    return res


if __name__ == "__main__":
    summary = {"run_at_ist": dt.datetime.now(IST).isoformat(timespec="seconds"), "endpoints": run_httpx()}
    pw_targets = ["https://www.npci.org.in/product/upi/product-statistics",
                  "https://www.npci.org.in/circulars/upi",
                  "https://www.dot.gov.in/", "https://www.meity.gov.in/", "https://www.powermin.gov.in/"]
    summary["playwright"] = asyncio.run(run_playwright(pw_targets))
    (RAW / "_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", RAW / "_summary.json")
