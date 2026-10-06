"""Can this machine reach every source? Run locally and on a GitHub runner, then compare.

Each URL is fetched with httpx (the production client) and, if that fails, with
curl_cffi impersonating Chrome (F-06 fallback). A 200 that looks like a bot or
challenge page counts as blocked. Writes JSON + a Markdown table.

    uv run python harness/reachability.py --out out/reach_local
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import truststore

truststore.inject_into_ssl()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

# (group, url) -- concrete forms of the FEED_MAP endpoints.
URLS = [
    # India Tier 1
    ("IN-T1", "https://rbi.org.in/pressreleases_rss.xml"),
    ("IN-T1", "https://rbi.org.in/notifications_rss.xml"),
    ("IN-T1", "https://www.rbi.org.in/scripts/Annualpolicy.aspx"),
    ("IN-T1", "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63719"),
    ("IN-T1", "https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3&reg=3"),
    ("IN-T1", "https://www.pib.gov.in/allRel.aspx"),
    ("IN-T1", "https://www.mospi.gov.in/"),
    ("IN-T1", "https://www.mospi.gov.in/api/latest-release/get-web-latest-release-list"),
    ("IN-T1", "https://eaindustry.nic.in/"),
    ("IN-T1", "https://www.dgft.gov.in/CP/?opt=notification"),
    ("IN-T1", "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=6&ssid=23&smid=0"),
    ("IN-T1", "https://www.incometaxindia.gov.in/"),
    ("IN-T1", "https://www.cbic.gov.in/"),
    ("IN-T1", "https://www.indiabudget.gov.in/"),
    ("IN-T1", "https://www.nseindia.com/api/circulars"),
    ("IN-T1", "https://api.bseindia.com/BseIndiaAPI/api/getCurrPreNextNoticesData_New/w?flag="),
    ("IN-T1", "https://www.youtube.com/feeds/videos.xml?channel_id=UCIfCOl43tunZVNYafeC4RQA"),
    # India Tier 2/3
    ("IN-T23", "https://internal.imd.gov.in/pages/press_release_mausam.php"),
    ("IN-T23", "https://irdai.gov.in/press-releases"),
    ("IN-T23", "https://www.pfrda.org.in/regulatory-framework/circulars/active-circulars"),
    ("IN-T23", "https://cercind.gov.in/recent_orders.html"),
    ("IN-T23", "https://ppac.gov.in/"),
    ("IN-T23", "https://www.pngrb.gov.in/eng-web/whatsnew/whats-new-json.js"),
    ("IN-T23", "https://www.powermin.gov.in/cms/wp-json/post-page/whats_new"),
    ("IN-T23", "https://www.dot.gov.in/cms/wp-json/post-page/documents?sort=acf&limit=10&page=1"),
    ("IN-T23", "https://www.trai.gov.in/rss.xml"),
    ("IN-T23", "https://www.niti.gov.in/rss.xml"),
    ("IN-T23", "https://fincomindia.nic.in/commission-reports-sixteenth"),
    ("IN-T23", "https://cdscoonline.gov.in/CDSCO/publicNsqDrugTable"),
    ("IN-T23", "https://www.fada.in/press-release-list.php"),
    ("IN-T23", "https://www.siam.in/news-&-updates/press-releases"),
    ("IN-T23", "https://www.niftyindices.com/press-release"),
    ("IN-T23", "https://prsindia.org/billtrack"),
    ("IN-T23", "https://www.eci.gov.in/"),
    ("IN-T23", "https://www.npci.org.in/what-we-do/upi/product-statistics"),
    # US policy / fiscal
    ("US-pol", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("US-pol", "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml"),
    ("US-pol", "https://www.federalreserve.gov/json/calendar.json"),
    ("US-pol", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"),
    ("US-pol", "https://www.newyorkfed.org/newsevents/speeches"),
    ("US-pol", "https://www.dallasfed.org/rss/speeches.xml"),
    ("US-pol", "https://www.richmondfed.org/press_room/speeches?cc_view=rss"),
    ("US-pol", "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=7"),
    ("US-pol", "https://home.treasury.gov/data/treasury-international-capital-tic-system/release-dates-of-tic-data"),
    ("US-pol", "https://public.govdelivery.com/topics/USTREAS_49/feed.rss"),
    ("US-pol", "https://www.whitehouse.gov/presidential-actions/feed/"),
    ("US-pol", "https://www.federalregister.gov/api/v1/documents.json?per_page=1"),
    ("US-pol", "https://ustr.gov/about-us/policy-offices/press-office/press-releases"),
    ("US-pol", "https://www.cbo.gov/"),
    ("US-pol", "https://www.sec.gov/news/pressreleases.rss"),
    ("US-pol", "https://www.opm.gov/json/operatingstatus.json"),
    # US data
    ("US-data", "https://www.bls.gov/schedule/news_release/bls.ics"),
    ("US-data", "https://www.bls.gov/news.release/cpi.nr0.htm"),
    ("US-data", "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"),
    ("US-data", "https://apps.bea.gov/rss/rss.xml"),
    ("US-data", "https://www.census.gov/economic-indicators/indicator.xml"),
    ("US-data", "https://www.census.gov/economic-indicators/calendar-listview.html"),
    ("US-data", "https://ir.eia.gov/wpsr/table1.csv"),
    ("US-data", "https://www.dol.gov/ui/data.pdf"),
    ("US-data", "https://adpemploymentreport.com/"),
    ("US-data", "https://www.ismworld.org/"),
    ("US-data", "https://www.conference-board.org/topics/consumer-confidence"),
    ("US-data", "https://www.sca.isr.umich.edu/"),
    ("US-data", "https://www.nahb.org/news-and-economics/housing-economics/indices/housing-market-index"),
    ("US-data", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"),
]

BOT_MARKERS = re.compile(
    r"(cf-chl|challenge-platform|Just a moment\.\.\.|Access Denied|Request Rejected|"
    r"captcha|_Incapsula_|Pardon Our Interruption|bot protection|akamai.*reference)", re.I)


def classify(status: int | None, body: bytes, err: str | None) -> str:
    if err:
        return "ERROR"
    if status is None:
        return "ERROR"
    if status >= 400:
        return f"HTTP{status}"
    head = body[:20000].decode("utf-8", "ignore")
    if BOT_MARKERS.search(head) and len(body) < 60000:
        return "BOTPAGE"
    return "OK"


def try_httpx(c: httpx.Client, url: str) -> dict:
    t0 = time.time()
    try:
        r = c.get(url)
        return {"status": r.status_code, "bytes": len(r.content), "ms": int((time.time() - t0) * 1000),
                "result": classify(r.status_code, r.content, None), "final_url": str(r.url)}
    except Exception as e:  # noqa: BLE001 - we want every failure recorded
        return {"status": None, "bytes": 0, "ms": int((time.time() - t0) * 1000),
                "result": "ERROR", "error": f"{type(e).__name__}: {e}"[:200]}


def try_cffi(url: str) -> dict:
    from curl_cffi import requests as creq
    t0 = time.time()
    try:
        r = creq.get(url, impersonate="chrome", timeout=30, allow_redirects=True)
        return {"status": r.status_code, "bytes": len(r.content), "ms": int((time.time() - t0) * 1000),
                "result": classify(r.status_code, r.content, None)}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "bytes": 0, "ms": int((time.time() - t0) * 1000),
                "result": "ERROR", "error": f"{type(e).__name__}: {e}"[:200]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/reach")
    ap.add_argument("--label", default="local")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, last = [], {}
    with httpx.Client(headers=HEADERS, follow_redirects=True,
                      timeout=httpx.Timeout(30.0, connect=15.0)) as c:
        for group, url in URLS:
            host = urlparse(url).netloc
            gap = time.time() - last.get(host, 0)
            if gap < 1.5:
                time.sleep(1.5 - gap)
            h = try_httpx(c, url)
            last[host] = time.time()
            row = {"group": group, "url": url, "httpx": h}
            if h["result"] != "OK":
                row["cffi"] = try_cffi(url)
            row["reachable"] = h["result"] == "OK" or row.get("cffi", {}).get("result") == "OK"
            rows.append(row)
            print(f"{'OK ' if row['reachable'] else 'NO '} {group:7} {h['result']:8} "
                  f"{row.get('cffi', {}).get('result', ''):8} {url}", flush=True)
    (out / "reach.json").write_text(json.dumps({"label": a.label, "rows": rows}, indent=1), encoding="utf-8")
    ok = sum(r["reachable"] for r in rows)
    md = [f"# Reachability ({a.label}): {ok}/{len(rows)} reachable", "",
          "| group | httpx | curl_cffi | url |", "|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['group']} | {r['httpx']['result']} {r['httpx'].get('error', '')[:60]} | "
                  f"{r.get('cffi', {}).get('result', '')} | {r['url']} |")
    (out / "reach.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n{ok}/{len(rows)} reachable")


if __name__ == "__main__":
    main()
