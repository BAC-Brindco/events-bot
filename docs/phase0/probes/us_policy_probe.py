"""Phase 0 probe: US policy & fiscal sources (Fed, Treasury, Fed Register, WH/USTR, Congress, CBO, TIC).

Run:  uv run --project "Z:\\Data Pipelines\\events_bot" python docs/phase0/probes/us_policy_probe.py [stage ...]

Stages: fed fedlag regional treasury treasury2 treasury3 fedreg wh congress cbo tic extras shutdown
        regional_pw blocked_pw (Playwright, reachability only). Default: all. NB congress+shutdown use DEMO_KEY (10 req/hr).
Writes raw samples (first ~50KB) to docs/phase0/raw/us_policy/ and a JSON results log
(docs/phase0/raw/us_policy/_results_<stage>.json). Polite: >=1.1 s between requests to the same host.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "us_policy"
RAW.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
HDRS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_last_hit: dict[str, float] = {}
client = httpx.Client(headers=HDRS, follow_redirects=True, timeout=45)
RESULTS: list[dict] = []
KEEP_HDRS = (
    "content-type", "etag", "last-modified", "cache-control", "date", "server", "age",
    "x-ratelimit-limit", "x-ratelimit-remaining", "retry-after", "content-length", "expires",
    "x-cache", "akamai-grn", "x-akamai-transformed", "via",
)


def polite(host: str) -> None:
    dt = time.time() - _last_hit.get(host, 0)
    if dt < 1.1:
        time.sleep(1.1 - dt)
    _last_hit[host] = time.time()


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]


def fetch(name: str, url: str, *, save: bool = True, method: str = "GET", cond: bool = False,
          headers: dict | None = None) -> httpx.Response | None:
    """Fetch url, record metadata, save first 50KB. If cond, replay with If-None-Match/If-Modified-Since."""
    host = urlparse(url).netloc
    polite(host)
    rec: dict = {"name": name, "url": url, "method": method, "fetched_utc": datetime.now(timezone.utc).isoformat()}
    try:
        r = client.request(method, url, headers=headers)
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
        RESULTS.append(rec)
        print(f"[ERR] {name}: {rec['error']}")
        return None
    rec["status"] = r.status_code
    rec["final_url"] = str(r.url)
    rec["headers"] = {k: v for k, v in r.headers.items() if k.lower() in KEEP_HDRS}
    rec["bytes"] = len(r.content)
    if save and method == "GET":
        ext = guess_ext(r.headers.get("content-type", ""))
        p = RAW / f"{slug(name)}{ext}"
        p.write_bytes(r.content[:50_000])
        rec["sample"] = p.name
    if cond and r.status_code == 200:
        ch = {}
        if r.headers.get("etag"):
            ch["If-None-Match"] = r.headers["etag"]
        if r.headers.get("last-modified"):
            ch["If-Modified-Since"] = r.headers["last-modified"]
        if ch:
            polite(host)
            try:
                r2 = client.get(url, headers=ch)
                rec["conditional_get"] = {"sent": ch, "status": r2.status_code, "bytes": len(r2.content)}
            except Exception as e:  # noqa: BLE001
                rec["conditional_get"] = {"error": str(e)}
        else:
            rec["conditional_get"] = "no validators returned"
    RESULTS.append(rec)
    print(f"[{r.status_code}] {name} {rec['headers'].get('content-type','')} {rec['bytes']}B "
          f"LM={rec['headers'].get('last-modified')} ETag={'y' if 'etag' in rec['headers'] else 'n'} "
          f"cond={rec.get('conditional_get',{}).get('status') if isinstance(rec.get('conditional_get'),dict) else rec.get('conditional_get')}")
    return r


def guess_ext(ct: str) -> str:
    ct = ct.lower()
    for k, v in (("json", ".json"), ("rss", ".xml"), ("atom", ".xml"), ("xml", ".xml"), ("html", ".html"),
                 ("pdf", ".pdf"), ("calendar", ".ics"), ("csv", ".csv"), ("text/plain", ".txt")):
        if k in ct:
            return v
    return ".bin"


def feed_summary(name: str, r: httpx.Response | None, n: int = 5) -> dict | None:
    if r is None or r.status_code != 200:
        return None
    f = feedparser.parse(r.content)
    ents = f.entries
    dates = [e.get("published") or e.get("updated") for e in ents]
    parsed = [e.get("published_parsed") or e.get("updated_parsed") for e in ents]
    parsed = [time.strftime("%Y-%m-%d %H:%M:%S", p) for p in parsed if p]
    out = {
        "feed_title": f.feed.get("title"),
        "bozo": bool(f.bozo),
        "n_entries": len(ents),
        "newest": max(parsed) if parsed else None,
        "oldest": min(parsed) if parsed else None,
        "first": [{"title": e.get("title"), "link": e.get("link"), "date": d} for e, d in zip(ents[:n], dates[:n])],
    }
    RESULTS[-1]["feed"] = out
    print(f"    feed '{out['feed_title']}' n={out['n_entries']} newest={out['newest']} oldest={out['oldest']}")
    for e in out["first"][:n]:
        print(f"      - {e['date']} | {e['title']} | {e['link']}")
    return out


def links(r: httpx.Response | None, pat: str, limit: int = 40) -> list[str]:
    if r is None:
        return []
    hrefs = re.findall(r'href=["\']?([^"\'\s>]+)', r.text, flags=re.I)  # home.treasury.gov uses unquoted attrs
    out = []
    for h in hrefs:
        if re.search(pat, h, flags=re.I) and h not in out:
            out.append(h)
    RESULTS[-1].setdefault("links", {})[pat] = out[:limit]
    for h in out[:limit]:
        print("      >", h)
    return out


def head_lm(name: str, url: str) -> dict:
    """HEAD (fallback GET) to read Last-Modified of a document, no sample saved."""
    r = fetch(name, url, save=False, method="HEAD")
    if r is not None and (r.status_code >= 400 or "last-modified" not in r.headers):
        r = fetch(name + "_GET", url, save=False)
    lm = r.headers.get("last-modified") if r is not None else None
    return {"url": url, "status": r.status_code if r is not None else None, "last_modified": lm,
            "last_modified_et": to_et(lm)}


def to_et(httpdate: str | None) -> str | None:
    if not httpdate:
        return None
    from zoneinfo import ZoneInfo
    try:
        return parsedate_to_datetime(httpdate).astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- stages
FED = "https://www.federalreserve.gov"


def stage_fed():
    r = fetch("fed_feeds_index", f"{FED}/feeds/feeds.htm")
    links(r, r"\.xml$|\.ics$|/feeds/")
    for nm, path in [
        ("fed_rss_press_monetary", "/feeds/press_monetary.xml"),
        ("fed_rss_press_all", "/feeds/press_all.xml"),
        ("fed_rss_speeches", "/feeds/speeches.xml"),
        ("fed_rss_testimony", "/feeds/testimony.xml"),
        ("fed_rss_speeches_and_testimony", "/feeds/speeches_and_testimony.xml"),
    ]:
        r = fetch(nm, FED + path, cond=True)
        feed_summary(nm, r)
    r = fetch("fed_fomc_calendar", f"{FED}/monetarypolicy/fomccalendars.htm", cond=True)
    links(r, r"fomc|monetary2026|beigebook|\.ics|calendar", 200)
    r = fetch("fed_newsevents_calendar", f"{FED}/newsevents/calendar.htm", cond=True)
    links(r, r"\.ics|calendar|json", 60)
    r = fetch("fed_speeches_page", f"{FED}/newsevents/speeches.htm")
    links(r, r"\.ics|calendar|json|\.xml", 40)
    # The public calendar (newsevents/calendar.htm) is an Angular app; js/cms/calendar.js loads /json/calendar.json
    r = fetch("fed_calendar_json", f"{FED}/json/calendar.json", cond=True)
    if r is not None and r.status_code == 200:
        d = json.loads(r.content.decode("utf-8-sig"))
        ev = d.get("events", [])
        RESULTS[-1]["calendar_json"] = {"keys": list(d), "n_events": len(ev), "months": sorted({e.get("month") for e in ev if e.get("month")})}
        print("    keys", list(d), "n_events", len(ev), "months", sorted({e.get('month') for e in ev if e.get('month')}))
        for e in [e for e in ev if (e.get("month") or "") >= "2026-09"][:25]:
            print("     ", {k: e.get(k) for k in ("month", "days", "time", "type", "title", "link", "live")})
    fetch("fed_calendar_checkbox_json", f"{FED}/json/calendar-checkbox.json")
    r = fetch("fed_beigebook_page", f"{FED}/monetarypolicy/beige-book-default.htm", cond=True)
    links(r, r"beigebook20\d\d|beige-book", 40)


def stage_fedlag():
    """Compare scheduled release time with Last-Modified of documents for the last 2 meetings."""
    out = {}
    for d in ("20260916", "20260729"):
        docs = {
            "statement_htm": f"{FED}/newsevents/pressreleases/monetary{d}a.htm",
            "statement_pdf": f"{FED}/monetarypolicy/files/monetary{d}a1.pdf",
            "impl_note_htm": f"{FED}/newsevents/pressreleases/monetary{d}a1.htm",
            "presser_page": f"{FED}/monetarypolicy/fomcpresconf{d}.htm",
            "minutes_htm": f"{FED}/monetarypolicy/fomcminutes{d}.htm",
            "minutes_pdf": f"{FED}/monetarypolicy/files/fomcminutes{d}.pdf",
        }
        if d == "20260916":
            docs |= {"sep_htm": f"{FED}/monetarypolicy/fomcprojtabl{d}.htm",
                     "sep_pdf": f"{FED}/monetarypolicy/files/fomcprojtabl{d}.pdf",
                     "sep_press_release": f"{FED}/newsevents/pressreleases/monetary{d}b.htm"}
        res = {}
        for k, u in docs.items():
            res[k] = head_lm(f"lag_{d}_{k}", u)
        # discover transcript PDF from presser page
        r = fetch(f"fed_presser_page_{d}", docs["presser_page"])
        for h in links(r, r"\.pdf$|\.mp4|youtube"):
            if "presconf" in h.lower() and h.lower().endswith(".pdf"):
                res["presser_transcript_pdf"] = head_lm(f"lag_{d}_transcript", h if h.startswith("http") else FED + h)
        out[d] = res
    # June 2026 SEP + July minutes (released 2026-08-19) for 2nd SEP/minutes sample
    out["20260617"] = {
        "sep_htm": head_lm("lag_20260617_sep_htm", f"{FED}/monetarypolicy/fomcprojtabl20260617.htm"),
        "sep_pdf": head_lm("lag_20260617_sep_pdf", f"{FED}/monetarypolicy/files/fomcprojtabl20260617.pdf"),
        "statement_htm": head_lm("lag_20260617_statement_htm", f"{FED}/newsevents/pressreleases/monetary20260617a.htm"),
        "minutes_htm": head_lm("lag_20260617_minutes_htm", f"{FED}/monetarypolicy/fomcminutes20260617.htm"),
    }
    # Press-conference transcript PDFs: not linked from the presser page until later; probe the direct pattern
    for d in ("20260916", "20260729", "20260617", "20260429"):
        out.setdefault(d, {})["presser_transcript_pdf_direct"] = head_lm(
            f"lag_{d}_transcript_direct", f"{FED}/mediacenter/files/FOMCpresconf{d}.pdf")
    # Beige Book (2:00 pm ET); NOT in any Fed RSS feed. HTML slug uses yyyymm of the *period*, PDF uses release date.
    out["beige_20260902"] = {"pdf": head_lm("lag_beige_20260902_pdf", f"{FED}/monetarypolicy/files/BeigeBook_20260902.pdf"),
                             "htm": head_lm("lag_beige_202608_htm", f"{FED}/monetarypolicy/beigebook202608.htm")}
    out["beige_20260715"] = {"pdf": head_lm("lag_beige_20260715_pdf", f"{FED}/monetarypolicy/files/BeigeBook_20260715.pdf"),
                             "htm": head_lm("lag_beige_202607_htm", f"{FED}/monetarypolicy/beigebook202607.htm")}
    RESULTS.append({"name": "LAG_SUMMARY", "lag": out})
    print(json.dumps(out, indent=1))


REGIONAL_PAGES = {
    "nyfed": ["https://www.newyorkfed.org/newsevents/speeches", "https://www.newyorkfed.org/rss"],
    "bostonfed": ["https://www.bostonfed.org/news-and-events/speeches.aspx", "https://www.bostonfed.org/rss.aspx"],
    "philfed": ["https://www.philadelphiafed.org/", "https://www.philadelphiafed.org/rss"],
    "clevelandfed": ["https://www.clevelandfed.org/collections/speeches", "https://www.clevelandfed.org/rss"],
    "richmondfed": ["https://www.richmondfed.org/press_room/speeches", "https://www.richmondfed.org/rss"],
    "atlantafed": ["https://www.atlantafed.org/news/speeches", "https://www.atlantafed.org/rss"],
    "chicagofed": ["https://www.chicagofed.org/", "https://www.chicagofed.org/utilities/rss"],
    "stlouisfed": ["https://www.stlouisfed.org/from-the-president/speeches", "https://www.stlouisfed.org/rss"],
    "minneapolisfed": ["https://www.minneapolisfed.org/speeches", "https://www.minneapolisfed.org/rss"],
    "kcfed": ["https://www.kansascityfed.org/speeches/", "https://www.kansascityfed.org/rss/"],
    "dallasfed": ["https://www.dallasfed.org/news/speeches", "https://www.dallasfed.org/rss"],
    "sffed": ["https://www.frbsf.org/news-and-media/speeches/", "https://www.frbsf.org/feed/"],
}
RSS_PAT = r"rss|feed|\.xml|atom"


def stage_regional():
    for bank, pages in REGIONAL_PAGES.items():
        for i, u in enumerate(pages):
            r = fetch(f"regional_{bank}_{i}", u)
            if r is not None and r.status_code == 200:
                if "xml" in r.headers.get("content-type", "") or r.text.lstrip().startswith("<?xml"):
                    feed_summary(bank, r)
                else:
                    links(r, RSS_PAT, 30)
    # feeds discovered from the pages above (and chicagofed /rss via Playwright)
    for nm, u in [
        ("regfeed_boston_speeches", "https://www.bostonfed.org/feeds/rss_speeches.xml"),
        ("regfeed_richmond_speeches", "https://www.richmondfed.org/press_room/speeches?cc_view=rss"),
        ("regfeed_atlanta_speeches", "https://www.atlantafed.org/rss/speechindex"),
        ("regfeed_chicago_speeches", "https://www.chicagofed.org/forms/rss/Speeches"),
        ("regfeed_dallas_speeches", "https://www.dallasfed.org/rss/speeches.xml"),
        ("regfeed_sf_speeches", "https://www.frbsf.org/news-and-media/speeches/?feed=rss2"),
    ]:
        r = fetch(nm, u, cond=True)
        feed_summary(nm, r, n=3)


FD = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
TD = "https://www.treasurydirect.gov"


def jshow(r, path=("data",), n=3, keys=None):
    if r is None or r.status_code != 200:
        return None
    try:
        d = r.json()
    except Exception:  # noqa: BLE001
        print("    (not json)")
        return None
    x = d
    for p in path:
        x = x.get(p, x) if isinstance(x, dict) else x
    if isinstance(d, dict) and "meta" in d:
        print("    meta:", {k: d["meta"].get(k) for k in ("count", "total-count", "total-pages")})
    if isinstance(x, list):
        for row in x[:n]:
            print("     ", {k: row.get(k) for k in keys} if keys else row)
    return d


def stage_treasury():
    auc_keys = ["record_date", "auction_date", "security_type", "security_term", "cusip", "high_yield",
                "high_discnt_rate", "bid_to_cover_ratio", "offering_amt", "issue_date", "closing_time_comp"]
    r = fetch("fiscaldata_auctions_query", f"{FD}/v1/accounting/od/auctions_query?sort=-auction_date&page[size]=10", cond=True)
    d = jshow(r, n=6, keys=auc_keys)
    if d:
        RESULTS[-1]["fields"] = list(d["data"][0].keys())
        print("    fields:", list(d["data"][0].keys()))
    r = fetch("fiscaldata_auctions_query_oldest", f"{FD}/v1/accounting/od/auctions_query?sort=auction_date&page[size]=1")
    jshow(r, n=1, keys=["record_date", "auction_date", "security_type", "security_term"])
    r = fetch("fiscaldata_upcoming_auctions", f"{FD}/v1/accounting/od/upcoming_auctions?page[size]=20", cond=True)
    jshow(r, n=20)
    r = fetch("fiscaldata_record_setting_auction", f"{FD}/v2/accounting/od/record_setting_auction?page[size]=5")
    jshow(r, n=3)
    # TreasuryDirect TA_WS
    for nm, u in [
        ("td_auctioned_json", f"{TD}/TA_WS/securities/auctioned?format=json&days=7"),
        ("td_announced_json", f"{TD}/TA_WS/securities/announced?format=json&days=7"),
        ("td_upcoming_json", f"{TD}/TA_WS/securities/upcoming?format=json"),
        ("td_auctioned_xml", f"{TD}/TA_WS/securities/auctioned?format=xml&days=3"),
    ]:
        r = fetch(nm, u, cond=True)
        if r is not None and r.status_code == 200 and "json" in nm:
            try:
                rows = r.json()
                print(f"    rows={len(rows)}")
                for row in rows[:6]:
                    print("     ", {k: row.get(k) for k in ("cusip", "securityType", "securityTerm", "auctionDate",
                          "announcementDate", "issueDate", "closingTimeCompetitive", "highYield", "highInvestmentRate",
                          "bidToCoverRatio", "xmlFilenameAnnouncement", "xmlFilenameCompetitiveResults",
                          "pdfFilenameCompetitiveResults", "updatedTimestamp")})
                if rows:
                    RESULTS[-1]["fields"] = list(rows[0].keys())
            except Exception as e:  # noqa: BLE001
                print("    json err", e)
    r = fetch("td_auctions_upcoming_page", f"{TD}/auctions/upcoming/")
    links(r, r"\.pdf|\.xml|schedule|tentative", 30)
    # Treasury press releases + RSS discovery
    r = fetch("treasury_press_releases_page", "https://home.treasury.gov/news/press-releases")
    links(r, RSS_PAT, 30)
    r = fetch("treasury_rss_index", "https://home.treasury.gov/news/rss-feeds") if r is not None else None
    links(r, RSS_PAT, 30)
    r = fetch("treasury_quarterly_refunding", "https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding", cond=True)
    links(r, r"refunding|\.pdf", 60)


def stage_treasury2():
    r = fetch("fiscaldata_upcoming_auctions_latest", f"{FD}/v1/accounting/od/upcoming_auctions?sort=-record_date,auction_date&page[size]=15", cond=True)
    jshow(r, n=15)
    # TreasuryDirect result/announcement files: filenames come from TA_WS JSON (xmlFilename*/pdfFilename*)
    r = fetch("td_rss_index", f"{TD}/rss/")
    links(r, r"\.xml|rss", 30)
    lag = {}
    for fn, close in [("R_20260930_1", "2026-09-30 11:30 ET"), ("R_20260929_1", "2026-09-29 11:30 ET"),
                      ("R_20260924_3", "2026-09-24 13:00 ET")]:
        for kind, url in [("xml", f"{TD}/xml/{fn}.xml"),
                          ("pdf", f"{TD}/instit/annceresult/press/preanre/2026/{fn}.pdf")]:
            lag[f"{fn}.{kind}"] = head_lm(f"td_{fn}_{kind}", url) | {"comp_close": close}
    r = fetch("td_result_xml_sample", f"{TD}/xml/R_20260924_3.xml")
    RESULTS.append({"name": "TD_LAG", "lag": lag})
    print(json.dumps(lag, indent=1))
    # GovDelivery topic feed for Treasury press releases (topic id from the press-releases page subscribe link)
    r = fetch("treasury_govdelivery_press_rss", "https://public.govdelivery.com/topics/USTREAS_49/feed.rss", cond=True)
    feed_summary("treasury_govdelivery_press_rss", r)
    for nm, u in [("ta_ws_announced_rss", f"{TD}/TA_WS/securities/announced/rss"),
                  ("ta_ws_auctioned_rss", f"{TD}/TA_WS/securities/auctioned/rss")]:
        r = fetch(nm, u, cond=True)
        feed_summary(nm, r, n=3)
    QR = "https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding"
    r = fetch("treasury_qr_most_recent_docs", f"{QR}/most-recent-quarterly-refunding-documents", cond=True)
    links(r, r"\.pdf|\.xls|press-releases/|news/", 60)
    r = fetch("treasury_qr_archives", f"{QR}/quarterly-refunding-archives")
    links(r, r"archives/|\.pdf|20\d\d", 20)


def page_title_lm(name, url):
    r = fetch(name, url, save=False)
    if r is None:
        return {}
    m = re.search(r"<title>(.*?)</title>", r.text, re.S | re.I)
    d = re.search(r'<time[^>]*datetime="([^"]+)"', r.text)
    out = {"url": url, "status": r.status_code, "title": (m.group(1).strip()[:140] if m else None),
           "time_datetime_attr": d.group(1) if d else None,
           "last_modified": r.headers.get("last-modified"), "last_modified_et": to_et(r.headers.get("last-modified"))}
    print("    ", out)
    return out


def stage_treasury3():
    """Quarterly refunding press releases (Aug 2026 refunding) + their timestamps."""
    out = {}
    for sb in ("sb0584", "sb0585", "sb0590", "sb0591", "sb0592"):
        out[sb] = page_title_lm(f"treasury_pr_{sb}", f"https://home.treasury.gov/news/press-releases/{sb}")
    out["tentative_auction_schedule_pdf"] = head_lm("treasury_tentative_sched", "https://home.treasury.gov/system/files/221/TentativeAuctionScheduleQ32026.pdf")
    RESULTS.append({"name": "QR_LAG", "lag": out})


FR = "https://www.federalregister.gov/api/v1"


def stage_fedreg():
    flds = "&".join(f"fields[]={f}" for f in ("document_number", "title", "type", "subtype", "signing_date",
                    "publication_date", "executive_order_number", "html_url", "pdf_url", "agencies", "action"))
    r = fetch("fedreg_eo_latest",
              f"{FR}/documents.json?conditions[type][]=PRESDOCU&conditions[presidential_document_type]=executive_order"
              f"&order=newest&per_page=5&{flds}", cond=True)
    d = r.json() if r is not None and r.status_code == 200 else {}
    print("    count", d.get("count"))
    eos = d.get("results", [])
    for e in eos:
        print("     ", {k: e.get(k) for k in ("document_number", "executive_order_number", "signing_date", "publication_date", "title")})
    r = fetch("fedreg_eo_oldest", f"{FR}/documents.json?conditions[presidential_document_type]=executive_order&order=oldest&per_page=1&{flds}")
    if r is not None and r.status_code == 200:
        print("     oldest", r.json()["results"][0].get("publication_date"), r.json()["results"][0].get("executive_order_number"))
    r = fetch("fedreg_bis_latest", f"{FR}/documents.json?conditions[agencies][]=industry-and-security-bureau&order=newest&per_page=8&{flds}", cond=True)
    d = r.json() if r is not None and r.status_code == 200 else {}
    print("    count", d.get("count"))
    bis = d.get("results", [])
    for e in bis:
        print("     ", {k: e.get(k) for k in ("document_number", "type", "publication_date", "action", "title")})
    # Public inspection (posted the business day(s) before publication)
    r = fetch("fedreg_pi_current", f"{FR}/public-inspection-documents/current.json", cond=True)
    if r is not None and r.status_code == 200:
        pj = r.json()
        res = pj.get("results", [])
        print("    PI current count", pj.get("count"), "first filed_at", [x.get("filed_at") for x in res[:3]])
        print("    PI fields", list(res[0].keys()) if res else None)
    r = fetch("fedreg_pi_bis", f"{FR}/public-inspection-documents.json?conditions[agencies][]=industry-and-security-bureau&per_page=5")
    if r is not None and r.status_code == 200:
        for x in r.json().get("results", [])[:5]:
            print("      PI-BIS", x.get("document_number"), x.get("filed_at"), x.get("publication_date"), (x.get("title") or "")[:80])
    # Lag evidence: signing -> public-inspection filed_at -> FR publication for 2 latest EOs and 2 latest BIS rules
    lag = {}
    for e in eos[:2] + bis[:2]:
        dn = e["document_number"]
        r = fetch(f"fedreg_pi_{dn}", f"{FR}/public-inspection-documents/{dn}.json", save=False)
        pi = r.json() if r is not None and r.status_code == 200 else {}
        lag[dn] = {"title": e.get("title", "")[:90], "signing_date": e.get("signing_date"),
                   "pi_filed_at": pi.get("filed_at"), "pi_status": r.status_code if r is not None else None,
                   "publication_date": e.get("publication_date"), "pi_special_filing": pi.get("special_filing")}
    RESULTS.append({"name": "FR_LAG", "lag": lag})
    print(json.dumps(lag, indent=1))
    # quirks: PI list is blanked 00:00-08:45 ET (see meta.pil_unavailability_message in fedreg_pi_bis.json);
    # agency filter appears ignored when combined with available_on -> filter client-side
    r = fetch("fedreg_pi_bis_rule_2026-19537", f"{FR}/public-inspection-documents/2026-19537.json")
    if r is not None and r.status_code == 200:
        x = r.json()
        print("     BIS polysilicon rule PI:", x.get("filed_at"), x.get("filing_type"), "pub", x.get("publication_date"))
    r = fetch("fedreg_pi_available_on_bis_filter", f"{FR}/public-inspection-documents.json?conditions[agencies][]=industry-and-security-bureau&conditions[available_on]=2026-09-23")
    if r is not None and r.status_code == 200:
        print("     agency filter honoured?", {a for x in r.json().get("results", []) for a in x.get("agency_names", [])} <= {"Industry and Security Bureau"})
    fetch("fedreg_api_docs", "https://www.federalregister.gov/developers/documentation/api/v1")


def stage_wh():
    for nm, u in [("wh_feed_all", "https://www.whitehouse.gov/feed/"),
                  ("wh_feed_presidential_actions", "https://www.whitehouse.gov/presidential-actions/feed/"),
                  ("wh_feed_executive_orders", "https://www.whitehouse.gov/presidential-actions/executive-orders/feed/"),
                  ("wh_feed_proclamations", "https://www.whitehouse.gov/presidential-actions/proclamations/feed/")]:
        r = fetch(nm, u, cond=True)
        feed_summary(nm, r, n=5)
    r = fetch("wh_presidential_actions_page", "https://www.whitehouse.gov/presidential-actions/")
    links(r, RSS_PAT, 10)
    r = fetch("ustr_press_releases_page", "https://ustr.gov/about-us/policy-offices/press-office/press-releases")
    links(r, RSS_PAT + r"|/press-releases/20", 30)
    r = fetch("ustr_home", "https://ustr.gov/")
    links(r, RSS_PAT, 20)


def stage_congress():
    C = "https://api.congress.gov/v3"
    for nm, u in [("congress_bill", f"{C}/bill?api_key=DEMO_KEY&format=json&limit=3&sort=updateDate+desc"),
                  ("congress_committee_meeting", f"{C}/committee-meeting/119?api_key=DEMO_KEY&format=json&limit=3"),
                  ("congress_committee", f"{C}/committee?api_key=DEMO_KEY&format=json&limit=2"),
                  ("congress_house_vote", f"{C}/house-vote/119?api_key=DEMO_KEY&format=json&limit=2"),
                  ("congress_daily_record", f"{C}/daily-congressional-record?api_key=DEMO_KEY&format=json&limit=2")]:
        r = fetch(nm, u)
        if r is not None:
            print("    ", r.text[:400].replace("\n", " "))
    r = fetch("congress_gov_approps_table", "https://www.congress.gov/crs-appropriations-status-table")
    r = fetch("treasury_debt_limit_page", "https://home.treasury.gov/policy-issues/financial-markets-financial-institutions-and-fiscal-service/debt-limit", cond=True)
    links(r, r"\.pdf|letter|debt-limit", 30)
    # Fiscal Data: debt to the penny (daily) - context for debt-limit headroom
    r = fetch("fiscaldata_debt_to_penny", f"{FD}/v2/accounting/od/debt_to_penny?sort=-record_date&page[size]=3")
    jshow(r, n=3)


def stage_cbo():
    r = fetch("cbo_home", "https://www.cbo.gov/")
    links(r, RSS_PAT, 20)
    r = fetch("cbo_about_rss", "https://www.cbo.gov/about/rss")
    links(r, RSS_PAT, 30)


def stage_tic():
    r = fetch("tic_home", "https://home.treasury.gov/data/treasury-international-capital-tic-system", cond=True)
    links(r, r"tic|\.txt|\.csv|schedule|press", 60)
    r = fetch("tic_release_dates", "https://home.treasury.gov/data/treasury-international-capital-tic-system-home-page/release-dates-of-tic-data-0", cond=True)
    if r is not None and r.status_code == 200:
        import html as _h
        s = re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(script|style).*?</\1>", "", r.text))))
        i = s.find("Release Date")
        RESULTS[-1]["text_excerpt"] = s[i:i + 1500]
        print("    ", s[i:i + 1500])
    TICD = "https://ticdata.treasury.gov/resource-center/data-chart-center/tic/Documents"
    for nm, f in [("tic_mfh_txt", "mfh.txt"), ("tic_cslt_zip", "cslt.zip")]:
        r = fetch(nm, f"{TICD}/{f}", save=(f.endswith(".txt")), cond=True)
        if r is not None and f == "mfh.txt" and r.status_code == 200:
            print("    ", r.text[:600].replace("\n", " | "))
    # TIC press releases arrive via the Treasury GovDelivery feed (topic USTREAS_49): pull TIC items + pubDates
    r = fetch("treasury_govdelivery_press_rss_tic", "https://public.govdelivery.com/topics/USTREAS_49/feed.rss", save=False)
    if r is not None and r.status_code == 200:
        f = feedparser.parse(r.content)
        tic = [{"title": e.title, "published": e.get("published"), "link": e.link} for e in f.entries
               if "TIC" in e.title or "International Capital" in e.title]
        RESULTS[-1]["tic_items"] = tic
        print("     TIC items:", tic)


def stage_extras():
    fetch("ustr_press_releases_cond", "https://ustr.gov/about-us/policy-offices/press-office/press-releases", save=False, cond=True)
    fetch("treasury_press_releases_cond", "https://home.treasury.gov/news/press-releases", save=False, cond=True)
    fetch("wh_presidential_actions_page_cond", "https://www.whitehouse.gov/presidential-actions/", save=False, cond=True)
    fetch("nyfed_speeches_cond", "https://www.newyorkfed.org/newsevents/speeches", save=False, cond=True)
    fetch("fed_speeches_rss_cond", f"{FED}/feeds/speeches.xml", save=False, cond=True)


def stage_shutdown():
    """Shutdown / appropriations signals that are reachable without congress.gov web (403)."""
    r = fetch("opm_operating_status_json", "https://www.opm.gov/json/operatingstatus.json", cond=True)
    if r is not None:
        print("    ", r.text[:500])
    r = fetch("congress_law_119", "https://api.congress.gov/v3/law/119?api_key=DEMO_KEY&format=json&limit=5")
    if r is not None:
        print("    ", r.headers.get("x-ratelimit-limit"), r.headers.get("x-ratelimit-remaining"), r.text[:800].replace("\n", " "))


PW_PAGES = {
    # pages that time out / are JS-rendered for plain httpx; reachability + feed-link discovery only
    "nyfed_rss": "https://www.newyorkfed.org/rss",
    "stlouisfed_speeches": "https://www.stlouisfed.org/from-the-president/speeches",
    "kcfed_speeches": "https://www.kansascityfed.org/speeches/",
    "philfed_home": "https://www.philadelphiafed.org/",
    "minneapolisfed_speeches": "https://www.minneapolisfed.org/speeches",
    "chicagofed_rss": "https://www.chicagofed.org/rss",
    "clevelandfed_speeches": "https://www.clevelandfed.org/collections/speeches",
}


def pw_render(pages: dict[str, str], pat: str = RSS_PAT) -> dict:
    from playwright.sync_api import sync_playwright
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(user_agent=UA)
        for name, u in pages.items():
            pg = ctx.new_page()
            reqs: list[str] = []
            pg.on("request", lambda req, reqs=reqs: reqs.append(req.url))
            rec = {"name": f"pw_{name}", "url": u, "via": "playwright"}
            try:
                resp = pg.goto(u, wait_until="domcontentloaded", timeout=60000)
                pg.wait_for_timeout(6000)
                rec["status"] = resp.status if resp else None
                hrefs = pg.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                rec["feed_links"] = sorted({h for h in hrefs if re.search(pat, h, re.I)})[:40]
                rec["xhr_json"] = sorted({q for q in reqs if re.search(r"json|/api/|\.xml|rss", q, re.I)})[:40]
                (RAW / f"pw_{slug(name)}.html").write_text(pg.content()[:50_000], encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                rec["error"] = f"{type(e).__name__}: {e}"[:300]
            RESULTS.append(rec)
            print(json.dumps(rec, indent=1))
            pg.close()
            time.sleep(1.2)
        b.close()
    return out


def stage_regional_pw():
    pw_render(PW_PAGES)


def stage_blocked_pw():
    """CBO (DataDome) and congress.gov (Cloudflare) return 403 to httpx -- reachability check only."""
    pw_render({"cbo_home": "https://www.cbo.gov/",
               "cbo_about_rss": "https://www.cbo.gov/about/rss",
               "congress_gov_approps_table": "https://www.congress.gov/crs-appropriations-status-table"})


def main(stages):
    for s in stages:
        print(f"\n==================== {s}")
        globals()[f"stage_{s}"]()
        (RAW / f"_results_{s}.json").write_text(json.dumps(RESULTS, indent=1, default=str), encoding="utf-8")
        RESULTS.clear()


if __name__ == "__main__":
    ALL = [n[6:] for n in list(globals()) if n.startswith("stage_")]
    main(sys.argv[1:] or ALL)
