"""Phase 0 feed probe: US data agencies (BLS, BEA, Census, DOL, EIA, FDA, SEC) + US Tier 2.

Reproducible, polite (<=1 req/s per host), read-only. No keys, no sign-ups, no forms.

Usage (from repo root):
    uv run --project . python docs/phase0/probes/us_data_probe.py            # all groups
    uv run --project . python docs/phase0/probes/us_data_probe.py bls sec    # selected groups
    uv run --project . python docs/phase0/probes/us_data_probe.py --pw URL [URL...]  # headless-Chrome reachability
    uv run --project . python docs/phase0/probes/us_data_probe.py --analyze  # lag/calendar evidence from saved raws

Writes:
    docs/phase0/raw/us_data/<probe_id>.<ext>      first ~50KB of each response body
    docs/phase0/raw/us_data/_results_<group>.json status / headers / conditional-GET result per probe

NOTE: BLS keyless API (v1-equivalent limits) allows only ~25 queries/day per IP; the BLS
group spends 2 of them.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "us_data"
RAW.mkdir(parents=True, exist_ok=True)
MAX_SAVE = 50_000

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
# Header profiles. "browser" = full Chrome-like header set (what Akamai wants to see).
PROFILES = {
    "plain": {},  # httpx default UA (python-httpx/x.y)
    "ua_only": {"User-Agent": CHROME_UA},
    "browser": {
        "User-Agent": CHROME_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Sec-Ch-Ua": '"Chromium";v="140", "Google Chrome";v="140", "Not.A/Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    },
    # SEC fair-access policy: declared UA with contact. Used for sec.gov ONLY.
    "sec": {
        "User-Agent": "RAAS Research Capital events-bot (contact via repo)",
        "Accept-Encoding": "gzip, deflate",
    },
}

BLS_SERIES = [
    "CES0000000001",  # NFP total nonfarm SA
    "LNS14000000",  # unemployment rate
    "CES0500000003",  # AHE total private
    "CUSR0000SA0",  # CPI-U all items SA
    "CUSR0000SA0L1E",  # CPI-U core SA
    "WPSFD4",  # PPI final demand SA
    "JTS000000000000000JOL",  # JOLTS openings
]

def P(pid, url, profile="browser", method="GET", cond=False, ext=None, **kw):
    return dict(id=pid, url=url, profile=profile, method=method, cond=cond, ext=ext, kw=kw)

GROUPS: dict[str, list[dict]] = {
    # ---------------------------------------------------------------- BLS
    "bls_akamai": [  # same URL, escalating header realism -> which profile gets through Akamai
        P("bls_ics_plain", "https://www.bls.gov/schedule/news_release/bls.ics", "plain", ext="txt"),
        P("bls_ics_ua_only", "https://www.bls.gov/schedule/news_release/bls.ics", "ua_only", ext="txt"),
        P("bls_ics_browser", "https://www.bls.gov/schedule/news_release/bls.ics", "browser", cond=True, ext="ics"),
    ],
    "bls": [
        P("bls_api_v2_post", "https://api.bls.gov/publicAPI/v2/timeseries/data/", "ua_only", "POST",
          ext="json", json={"seriesid": BLS_SERIES, "startyear": "2025", "endyear": "2026"}),
        P("bls_api_v2_get_cpi", "https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0",
          "ua_only", cond=True, ext="json"),
        P("bls_feed_index", "https://www.bls.gov/feed/", ext="html"),
        P("bls_rss_latest", "https://www.bls.gov/feed/bls_latest.rss", cond=True, ext="xml"),
        P("bls_rss_empsit", "https://www.bls.gov/feed/empsit.rss", cond=True, ext="xml"),
        P("bls_rss_cpi", "https://www.bls.gov/feed/cpi.rss", ext="xml"),
        P("bls_rss_ppi", "https://www.bls.gov/feed/ppi.rss", ext="xml"),
        P("bls_rss_jolts", "https://www.bls.gov/feed/jolts.rss", ext="xml"),
        P("bls_empsit_nr0", "https://www.bls.gov/news.release/empsit.nr0.htm", cond=True, ext="html"),
        P("bls_cpi_nr0", "https://www.bls.gov/news.release/cpi.nr0.htm", cond=True, ext="html"),
        P("bls_ppi_nr0", "https://www.bls.gov/news.release/ppi.nr0.htm", cond=True, ext="html"),
        P("bls_jolts_nr0", "https://www.bls.gov/news.release/jolts.nr0.htm", cond=True, ext="html"),
        P("bls_empsit_pdf", "https://www.bls.gov/news.release/pdf/empsit.pdf", ext="pdf"),
        P("bls_empsit_archive_idx", "https://www.bls.gov/bls/news-release/empsit.htm", ext="html"),
        P("bls_cpi_archive_idx", "https://www.bls.gov/bls/news-release/cpi.htm", ext="html"),
    ],
    "bls_lag": [  # archived copies of the last 2 releases: do any headers carry a publish time?
        P("bls_arch_empsit_0904", "https://www.bls.gov/news.release/archives/empsit_09042026.htm", ext="html"),
        P("bls_arch_empsit_0807", "https://www.bls.gov/news.release/archives/empsit_08072026.htm", ext="html"),
        P("bls_arch_empsit_0904_pdf", "https://www.bls.gov/news.release/archives/empsit_09042026.pdf",
          ext="pdf"),
        P("bls_arch_empsit_0807_pdf", "https://www.bls.gov/news.release/archives/empsit_08072026.pdf",
          ext="pdf"),
        P("bls_arch_cpi_0911_pdf", "https://www.bls.gov/news.release/archives/cpi_09112026.pdf", ext="pdf"),
        P("bls_arch_cpi_0812_pdf", "https://www.bls.gov/news.release/archives/cpi_08122026.pdf", ext="pdf"),
        P("bls_cpi_pdf", "https://www.bls.gov/news.release/pdf/cpi.pdf", ext="pdf"),
        P("bls_ppi_pdf", "https://www.bls.gov/news.release/pdf/ppi.pdf", ext="pdf"),
        P("bls_jolts_pdf", "https://www.bls.gov/news.release/pdf/jolts.pdf", ext="pdf"),
        P("bls_future_slot", "https://www.bls.gov/news.release/archives/empsit_10022026.htm", ext="html"),
    ],
    # ---------------------------------------------------------------- BEA
    "bea": [
        P("bea_api_nokey", "https://apps.bea.gov/api/data", "ua_only", ext="json",
          params={"UserID": "", "method": "GETDATASETLIST", "ResultFormat": "JSON"}),
        P("bea_api_badkey", "https://apps.bea.gov/api/data", "ua_only", ext="json",
          params={"UserID": "00000000-0000-0000-0000-000000000000", "method": "GETDATASETLIST",
                  "ResultFormat": "JSON"}),
        P("bea_schedule", "https://www.bea.gov/news/schedule", cond=True, ext="html"),
        P("bea_rss", "https://apps.bea.gov/rss/rss.xml", cond=True, ext="xml"),
        P("bea_current_releases", "https://www.bea.gov/news/current-releases", ext="html"),
        P("bea_schedule_ics", "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics",
          cond=True, ext="ics"),
        P("bea_release_dates_json", "https://apps.bea.gov/API/signup/release_dates.json", cond=True,
          ext="json"),
        P("bea_pio_aug2026", "https://www.bea.gov/news/2026/personal-income-and-outlays-august-2026",
          ext="html"),
        P("bea_pio_jul2026", "https://www.bea.gov/news/2026/personal-income-and-outlays-july-2026",
          ext="html"),
    ],
    # ---------------------------------------------------------------- Census
    "census": [
        P("census_eits_marts", "https://api.census.gov/data/timeseries/eits/marts", "ua_only", ext="json",
          params={"get": "cell_value,data_type_code,time_slot_id,category_code,seasonally_adj",
                  "for": "us:*", "time": "from 2026-05"}),
        P("census_eits_advm3", "https://api.census.gov/data/timeseries/eits/advm3", "ua_only", ext="json",
          params={"get": "cell_value,data_type_code,time_slot_id,category_code,seasonally_adj",
                  "for": "us:*", "time": "from 2026-05"}),
        P("census_eits_resconst", "https://api.census.gov/data/timeseries/eits/resconst", "ua_only",
          ext="json", cond=True,
          params={"get": "cell_value,data_type_code,time_slot_id,category_code,seasonally_adj",
                  "for": "us:*", "time": "from 2026-05"}),
        P("census_eits_marts_vars", "https://api.census.gov/data/timeseries/eits/marts/variables.json",
          "ua_only", ext="json"),
        P("census_econ_ind", "https://www.census.gov/economic-indicators/", cond=True, ext="html"),
        P("census_econ_calendar", "https://www.census.gov/economic-indicators/calendar-listview.html",
          ext="html"),
        P("census_indicator_xml", "https://www.census.gov/economic-indicators/indicator.xml", cond=True,
          ext="xml"),
        P("census_marts_current_pdf", "https://www.census.gov/retail/marts/www/marts_current.pdf",
          cond=True, ext="pdf"),
        P("census_m3_adv_pdf", "https://www.census.gov/manufacturing/m3/adv/pdf/durgd.pdf", cond=True,
          ext="pdf"),
        P("census_nrc_pdf", "https://www.census.gov/construction/nrc/pdf/newresconst.pdf", cond=True,
          ext="pdf"),
        P("census_glance_pdf", "https://www.census.gov/economic-indicators/econcards/assets/pdf/"
          "censusreleaseglance_2026.pdf", ext="pdf"),
    ],
    # ---------------------------------------------------------------- DOL
    "dol": [
        P("dol_ui_data_pdf", "https://www.dol.gov/ui/data.pdf", cond=True, ext="pdf"),
        P("dol_claims_asp", "https://oui.doleta.gov/unemploy/claims.asp", ext="html"),
        P("dol_ar539_csv", "https://oui.doleta.gov/unemploy/csv/ar539.csv", cond=True, ext="csv"),
        P("dol_newsroom_rss_idx", "https://www.dol.gov/rss", ext="html"),
        P("dol_rss_releases", "https://www.dol.gov/rss/releases.xml", ext="xml"),
        P("dol_claims_release_0924", "https://www.dol.gov/newsroom/releases/eta/eta20260924", ext="pdf"),
        P("dol_claims_release_0917", "https://www.dol.gov/newsroom/releases/eta/eta20260917", ext="pdf"),
    ],
    # ---------------------------------------------------------------- EIA
    "eia": [
        P("eia_api_nokey", "https://api.eia.gov/v2/petroleum/stoc/wstk/data/", "ua_only", ext="json"),
        P("eia_api_demokey", "https://api.eia.gov/v2/petroleum/stoc/wstk/data/", "ua_only", ext="json",
          params={"api_key": "DEMO_KEY"}),
        P("eia_api_demokey_crude", "https://api.eia.gov/v2/petroleum/stoc/wstk/data/", "ua_only", ext="json",
          params={"api_key": "DEMO_KEY", "frequency": "weekly", "data[0]": "value",
                  "facets[series][]": "WCESTUS1", "sort[0][column]": "period",
                  "sort[0][direction]": "desc", "length": "3"}),
        P("eia_wpsr_summary_pdf", "https://ir.eia.gov/wpsr/wpsrsummary.pdf", cond=True, ext="pdf"),
        P("eia_wpsr_table1_csv", "https://ir.eia.gov/wpsr/table1.csv", cond=True, ext="csv"),
        P("eia_wpsr_page", "https://www.eia.gov/petroleum/supply/weekly/", ext="html"),
        P("eia_wpsr_schedule", "https://www.eia.gov/petroleum/supply/weekly/schedule.php", ext="html"),
        P("eia_rss_idx", "https://www.eia.gov/tools/rssfeeds/", ext="html"),
        P("eia_rss_twip", "https://www.eia.gov/petroleum/weekly/includes/week_in_petroleum_rss.xml",
          cond=True, ext="xml"),
        P("eia_rss_press", "https://www.eia.gov/rss/press_rss.xml", ext="xml"),
        P("eia_wpsr_highlights_pdf", "https://www.eia.gov/petroleum/supply/weekly/pdf/highlights.pdf",
          cond=True, ext="pdf"),
    ],
    # ---------------------------------------------------------------- FDA
    "fda": [
        P("fda_openfda_drugsfda", "https://api.fda.gov/drug/drugsfda.json", "ua_only", ext="json",
          params={"search": "submissions.submission_status_date:[20260801 TO 20261001]"
                            " AND submissions.submission_type:ORIG", "limit": "3"}),
        P("fda_openfda_enf_india", "https://api.fda.gov/drug/enforcement.json", "ua_only", ext="json",
          params={"search": 'country:"India"', "sort": "report_date:desc", "limit": "3"}),
        P("fda_warning_letters", "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-"
          "investigations/compliance-actions-and-activities/warning-letters", ext="html"),
        P("fda_rss_idx", "https://www.fda.gov/about-fda/contact-fda/subscribe-podcasts-and-news-feeds",
          ext="html"),
        P("fda_rss_press", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", cond=True, ext="xml"),
        P("fda_rss_drugs", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml", cond=True, ext="xml"),
        P("fda_wl_xlsx", "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/"
          "compliance-actions-and-activities/warning-letters/datatables-data?page&_format=xlsx", ext="xlsx"),
        P("fda_wl_sample_letter", "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-"
          "investigations/warning-letters/babikian-healthcare-products-cjsc-730513-09212026", ext="html"),
        P("fda_wl_sample_letter2", "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-"
          "investigations/warning-letters/stream2sea-llc-718983-02022026", ext="html"),
        P("fda_openfda_enf_meta", "https://api.fda.gov/drug/enforcement.json", "ua_only", ext="json",
          params={"limit": "1"}),
    ],
    # ---------------------------------------------------------------- SEC (declared UA only)
    "sec": [
        P("sec_rss_idx", "https://www.sec.gov/about/rss-feeds", "sec", ext="html"),
        P("sec_press_rss", "https://www.sec.gov/news/pressreleases.rss", "sec", cond=True, ext="xml"),
        P("sec_litigation_rss_legacy", "https://www.sec.gov/rss/litigation/litreleases.xml", "sec", ext="xml"),
        P("sec_proposed_rss_legacy", "https://www.sec.gov/rss/rules/proposed.xml", "sec", ext="xml"),
        P("sec_final_rss_legacy", "https://www.sec.gov/rss/rules/final.xml", "sec", ext="xml"),
        P("sec_litigation_rss", "https://www.sec.gov/enforcement-litigation/litigation-releases/rss", "sec",
          cond=True, ext="xml"),
        P("sec_admin_proc_rss", "https://www.sec.gov/enforcement-litigation/administrative-proceedings/rss",
          "sec", ext="xml"),
        P("sec_trading_susp_rss", "https://www.sec.gov/enforcement-litigation/trading-suspensions/rss", "sec",
          ext="xml"),
        P("sec_statements_rss", "https://www.sec.gov/news/statements.rss", "sec", ext="xml"),
        P("sec_speeches_rss", "https://www.sec.gov/news/speeches-statements.rss", "sec", ext="xml"),
        P("sec_item_lr26662", "https://www.sec.gov/enforcement-litigation/litigation-releases/lr-26662", "sec",
          ext="html"),
        P("sec_item_pr2026_96", "https://www.sec.gov/newsroom/press-releases/2026-96-sec-proposes-amendments-"
          "expand-responsible-retailization-private-markets", "sec", ext="html"),
        # rulemaking: no sec.gov rules RSS listed on /about/rss-feeds -> Federal Register agency feed (SEC=466)
        P("fedreg_sec_rss", "https://www.federalregister.gov/api/v1/documents.rss", "ua_only", ext="xml",
          params={"conditions[agencies][]": "securities-and-exchange-commission", "order": "newest"}),
        P("fedreg_sec_api", "https://www.federalregister.gov/api/v1/documents.json", "ua_only", ext="json",
          params={"conditions[agencies][]": "securities-and-exchange-commission",
                  "conditions[type][]": ["RULE", "PRORULE"], "order": "newest", "per_page": "5"}),
    ],
    # ---------------------------------------------------------------- Tier 2
    "tier2": [
        P("ism_rob", "https://www.ismworld.org/supply-management-news-and-reports/reports/"
          "ism-report-on-business/", ext="html"),
        P("umich_home", "https://www.sca.isr.umich.edu/", ext="html"),
        P("umich_tbmics_csv", "https://www.sca.isr.umich.edu/files/tbmics.csv", cond=True, ext="csv"),
        P("confboard_cci", "https://www.conference-board.org/topics/consumer-confidence", ext="html"),
        P("adp_ner", "https://adpemploymentreport.com/", ext="html"),
        P("nahb_hmi", "https://www.nahb.org/news-and-economics/housing-economics/indices/"
          "housing-market-index", ext="html"),
        P("spdji_case_shiller", "https://www.spglobal.com/spdji/en/index-family/indicators/"
          "sp-corelogic-case-shiller/", ext="html"),
        P("fred_csv_payems", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PAYEMS", "browser",
          cond=True, ext="csv", timeout=90),
        P("fred_csv_cpiaucsl", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL", "browser",
          ext="csv", timeout=90),
        P("fred_csv_pcepi", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PCEPI", "browser",
          ext="csv", timeout=90),
        P("fred_csv_icsa", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=ICSA", "browser",
          ext="csv", timeout=90),
        P("fred_csv_caseshiller", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CSUSHPISA", "browser",
          ext="csv", timeout=90),
        P("umich_tbcics_csv", "https://www.sca.isr.umich.edu/files/tbcics.csv", ext="csv"),
        P("nahb_release_dates", "https://www.nahb.org/news-and-economics/housing-economics/indices/"
          "nahb-wells-fargo-housing-market-index-release-dates", ext="html"),
        P("fred_api_nokey", "https://api.stlouisfed.org/fred/series", "ua_only", ext="json",
          params={"series_id": "PAYEMS", "file_type": "json"}),
    ],
}

_last_hit: dict[str, float] = {}


def polite(host: str) -> None:
    gap = time.monotonic() - _last_hit.get(host, 0)
    if gap < 1.1:
        time.sleep(1.1 - gap)
    _last_hit[host] = time.monotonic()


def akamai_block(resp: httpx.Response) -> bool:
    body = resp.content[:4000].lower()
    return resp.status_code in (403, 429) and (
        b"access denied" in body or b"akamai" in body or "akamai" in resp.headers.get("server", "").lower()
        or b"reference #" in body)


def run_probe(client: httpx.Client, p: dict, extra: dict | None = None) -> dict:
    host = urlparse(p["url"]).netloc
    headers = dict(PROFILES[p["profile"]])
    if extra:
        headers.update(extra)
    polite(host)
    t0 = time.monotonic()
    try:
        r = client.request(p["method"], p["url"], headers=headers, **p["kw"])
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)}
    h = r.headers
    return {
        "status": r.status_code,
        "final_url": str(r.url),
        "elapsed_s": round(time.monotonic() - t0, 2),
        "content_type": h.get("content-type"),
        "bytes": len(r.content),
        "etag": h.get("etag"),
        "last_modified": h.get("last-modified"),
        "cache_control": h.get("cache-control"),
        "expires": h.get("expires"),
        "server": h.get("server"),
        "date": h.get("date"),
        "ratelimit": {k: v for k, v in h.items() if "ratelimit" in k.lower() or "x-api" in k.lower()},
        "akamai_block": akamai_block(r),
        "_resp": r,
    }


def main(argv: list[str]) -> None:
    if argv and argv[0] == "--pw":
        return playwright_check(argv[1:])
    if argv and argv[0] == "--analyze":
        return analyze()
    groups = argv or list(GROUPS)
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        for g in groups:
            out = {}
            for p in GROUPS[g]:
                res = run_probe(client, p)
                resp = res.pop("_resp", None)
                if resp is not None:
                    (RAW / f"{p['id']}.{p['ext'] or 'bin'}").write_bytes(resp.content[:MAX_SAVE])
                    if p["cond"] and resp.status_code == 200:
                        cond_h = {}
                        if res["etag"]:
                            cond_h["If-None-Match"] = res["etag"]
                        if res["last_modified"]:
                            cond_h["If-Modified-Since"] = res["last_modified"]
                        if cond_h:
                            c = run_probe(client, p, cond_h)
                            c.pop("_resp", None)
                            res["conditional_get_status"] = c.get("status")
                res["url"] = p["url"]
                res["profile"] = p["profile"]
                out[p["id"]] = res
                print(f"[{g}] {p['id']:28s} {res.get('status')} {res.get('content_type')} "
                      f"ETag={bool(res.get('etag'))} LM={res.get('last_modified')} "
                      f"304={res.get('conditional_get_status')} akamai={res.get('akamai_block')} "
                      f"{res.get('error', '')}")
            (RAW / f"_results_{g}.json").write_text(json.dumps(out, indent=2))


def playwright_check(urls: list[str]) -> None:
    """Reachability only (no logins/forms): load page in headless Chrome, report status + title."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, channel="chrome")
        pg = b.new_page()
        res = {}
        for u in urls:
            try:
                r = pg.goto(u, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(3000)
                res[u] = {"status": r.status if r else None, "title": pg.title(),
                          "html_len": len(pg.content())}
                name = "pw_" + urlparse(u).netloc.replace(".", "_") + ".html"
                (RAW / name).write_text(pg.content()[:MAX_SAVE], encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                res[u] = {"error": repr(e)}
            print(u, res[u])
            time.sleep(1.5)
        b.close()
        (RAW / "_results_playwright.json").write_text(json.dumps(res, indent=2))


def analyze() -> None:
    """Print lag/calendar evidence. Re-fetches the two ICS files in full (raw copies are truncated)."""
    import datetime as dt
    from email.utils import parsedate_to_datetime

    import feedparser
    import icalendar

    print("== RSS item pubDates (saved raws)")
    for f in ["bls_rss_empsit", "bls_rss_cpi", "bls_rss_ppi", "bls_rss_jolts", "bea_rss",
              "census_indicator_xml", "dol_rss_releases", "sec_press_rss", "fda_rss_press"]:
        fp = RAW / f"{f}.xml"
        if fp.exists():
            d = feedparser.parse(fp.read_bytes())
            for e in d.entries[:3]:
                print(f"  {f:22s} {e.get('published')} | {e.get('title', '')[:60]}")
    print("== Last-Modified of release artefacts (from _results_*.json)")
    for rf in sorted(RAW.glob("_results_*.json")):
        for pid, r in json.loads(rf.read_text()).items():
            if r.get("last_modified"):
                lm = parsedate_to_datetime(r["last_modified"]).astimezone(
                    dt.timezone(dt.timedelta(hours=-4)))  # EDT
                print(f"  {pid:28s} LM={lm:%Y-%m-%d %H:%M:%S} ET")
    print("== Calendars (full ICS)")
    with httpx.Client(follow_redirects=True, timeout=60) as c:
        for name, u in [("BLS", "https://www.bls.gov/schedule/news_release/bls.ics"),
                        ("BEA", "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics")]:
            polite(urlparse(u).netloc)
            cal = icalendar.Calendar.from_ical(c.get(u, headers=PROFILES["browser"]).content)
            ev = sorted((str(e.decoded("dtstart")), str(e.get("summary"))) for e in cal.walk("VEVENT"))
            print(f"  {name}: {len(ev)} events {ev[0][0]} .. {ev[-1][0]}")
            for d, sname in ev:
                if "2026-09-01" <= d[:10] <= "2026-11-10" and any(
                        k in sname for k in ("Employment Situation", "Consumer Price", "Producer Price",
                                             "Job Openings", "GDP", "Personal Income")):
                    print(f"    {d} {sname[:70]}")


if __name__ == "__main__":
    main(sys.argv[1:])
