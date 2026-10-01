"""Phase 0 feed probe -- India Tier 1 (PIB, RBI, SEBI, GST Council, MoF/DEA, CBDT/CBIC, eGazette,
DGFT, MoSPI, OEA, CGA, IMD, NSE/BSE/NSE Indices).

Reproduces every endpoint check reported in docs/phase0/india_t1.md:
  * GETs each endpoint (polite: >=1.1 s per host, real browser UA)
  * records status / content-type / ETag / Last-Modified, tests conditional GET (304) where validators exist
  * saves first ~50 KB of each body into docs/phase0/raw/india_t1/ as evidence
  * prints a one-line verdict per endpoint and writes raw/india_t1/_probe_summary.json

Run:   uv run --project "Z:\\Data Pipelines\\events_bot" python docs/phase0/probes/india_t1_probe.py [group ...]
Groups: pib rbi sebi gst mof dgft mospi oea cga imd nse bse     (default: all)
BSE's api.bseindia.com rejects httpx's TLS fingerprint; the bse group needs curl_cffi:
       uv run --project ... --with curl_cffi python docs/phase0/probes/india_t1_probe.py bse
Lag measurement for PIB/RBI/SEBI RSS is in india_t1_lag_watch.py (long-running poller).

Notes on hosts with broken TLS chains (dea.gov.in, cbic.gov.in, taxinformation.cbic.gov.in,
egazette.gov.in): the probe first tries with verification ON and records the failure, then retries
with verify=False ONLY to establish reachability. Production must fix the chain (truststore /
bundled intermediates), never disable verification.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _probe_common import RAW, client, cond_check, fetch  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
RESULTS: list[dict] = []


def rec(group: str, name: str, r: dict, ok: bool, note: str = "") -> None:
    row = {"group": group, "name": name, "url": r.get("url"), "status": r.get("status"),
           "ctype": r.get("ctype"), "etag": r.get("etag"), "last_modified": r.get("last_modified"),
           "bytes": r.get("bytes"), "error": r.get("error"), "ok": ok, "note": note}
    RESULTS.append(row)
    print(f"[{'OK ' if ok else 'BAD'}] {group:6} {name:38} {r.get('status')} {r.get('ctype')} {note}")


def rss(group, name, c, url, min_items=1, **kw):
    r = fetch(c, url, name=name, **kw)
    if r.get("status") != 200:
        rec(group, name, r, False, r.get("error") or "")
        return None
    f = feedparser.parse(r["_resp"].content)
    pubs = [e.get("published") for e in f.entries]
    note = f"items={len(f.entries)} newest={pubs[0] if pubs else None} oldest={pubs[-1] if pubs else None}; {cond_check(c, r)}"
    rec(group, name, r, len(f.entries) >= min_items, note)
    return f


def page(group, name, c, url, must: str | None = None, **kw):
    r = fetch(c, url, name=name, **kw)
    ok = r.get("status") == 200 and (must is None or re.search(must, r["_resp"].text or "", re.I) is not None)
    rec(group, name, r, ok, r.get("error") or ("" if ok else f"pattern {must!r} not found"))
    return r


def pdf(group, name, c, url, **kw):
    r = fetch(c, url, name=name, **kw)
    ok = r.get("status") == 200 and r["_resp"].content[:5] == b"%PDF-"
    lm = r.get("last_modified")
    note = ""
    if lm:
        note = "LM IST " + datetime.strptime(lm, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    if r.get("status") == 200 and not ok:
        note += " NOT A PDF: " + r["_resp"].text[:80].replace("\n", " ")
    rec(group, name, r, ok, note)
    return r


# ----------------------------------------------------------------------------------------------
def g_pib(c):
    # Advertised URL (ViewRss.aspx) 302s to the HINDI feed; &reg=3 pins English/Delhi.
    r = fetch(c, "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", name="pib_rss_advertised_url")
    rec("pib", "pib_rss_advertised_url", r, False, f"redirects to {r.get('final_url')} (Hindi)")
    rss("pib", "pib_rss_pr_en", c, "https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3&reg=3")
    page("pib", "pib_allrel_en", c, "https://www.pib.gov.in/allRel.aspx?reg=3&lang=1", must=r"font104")
    page("pib", "pib_release_2317567", c, "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2317567&reg=3&lang=1", must="PrDateTime")
    # Cabinet / CCEA decision (30 Sep 2026) and CGA monthly accounts echo (30 Sep 2026)
    page("pib", "pib_release_2316953_ccea", c, "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2316953&reg=3&lang=1", must="Cabinet Committee on Economic Affairs")
    page("pib", "pib_release_2316956_msp", c, "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2316956&reg=3&lang=1", must="Minimum Support Price")
    page("pib", "pib_release_2317034_cga_echo", c, "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2317034&reg=3&lang=1", must="Monthly Review of Accounts")
    page("pib", "pib_gst55_2086873", c, "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2086873&reg=3&lang=1", must="GST Council")


def g_rbi(c):
    for n in ["pressreleases", "notifications", "speeches", "Publication"]:
        rss("rbi", f"rbi_{n}_rss", c, f"https://rbi.org.in/{n}_rss.xml")
    page("rbi", "rbi_annualpolicy", c, "https://www.rbi.org.in/scripts/Annualpolicy.aspx", must="Meeting Schedule of the Monetary Policy Committee for 2026-2027")
    for p in [63287, 63288, 63289, 63403, 62863, 62864, 62974, 63668, 63669, 63587, 63674]:
        page("rbi", f"rbi_pr_{p}", c, f"https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={p}", must="Date :")
    page("rbi", "rbi_masterdirections", c, "https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx")
    page("rbi", "rbi_draft_rewise", c, "https://www.rbi.org.in/Scripts/BS_ViewREwiseDraftDirections.aspx")
    page("rbi", "rbi_bulletin", c, "https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx", must="Bulletin")
    ref = {"Referer": "https://www.rbi.org.in/", "Accept": "application/pdf,*/*"}
    mps_aug = "https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR80907599DE5FD164918A49085C9D6270116.PDF"
    # rbidocs intermittently answers HTTP 200 text/html "Request Rejected"/"Please enable JavaScript" (F5 bot
    # challenge) -- seen 15:29 IST on 2026-10-01 for 4/4 PDFs (and in headless Chrome), cleared by 15:30 and
    # not reproducible afterwards with or without Referer. Always validate the %PDF magic and retry.
    pdf("rbi", "rbidocs_no_referer", c, mps_aug)
    pdf("rbi", "rbi_mps_aug26", c, mps_aug, headers=ref)
    pdf("rbi", "rbi_mps_jun26", c, "https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR3855508EB4A59FF46F9B57BBA200AA250B8.PDF", headers=ref)
    pdf("rbi", "rbi_govstmt_aug26", c, "https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR810CF4E75DAF41B4EBAA08034864C55D8A9.PDF", headers=ref)
    pdf("rbi", "rbi_govstmt_jun26", c, "https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR386E3EF62B419E04A85ABE96B0EB8BAC99A.PDF", headers=ref)
    pdf("rbi", "rbi_sdrp_aug26", c, "https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR811985F030C62F347038BCF99E6A27A7CE2.PDF", headers=ref)
    pdf("rbi", "rbi_minutes_aug26", c, "https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR9257E0CB4D769F24658B0AB41428CA46F97.PDF", headers=ref)
    f = rss("rbi", "rbi_youtube_rss", c, "https://www.youtube.com/feeds/videos.xml?channel_id=UCIfCOl43tunZVNYafeC4RQA")
    if f is not None:
        print("      youtube channel title:", f.feed.get("title"))


def g_sebi(c):
    rss("sebi", "sebi_rss", c, "https://www.sebi.gov.in/sebirss.xml")
    base = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?"
    page("sebi", "sebi_list_all", c, base + "doListingAll=yes", must="News List All")
    page("sebi", "sebi_list_circulars", c, base + "doListing=yes&sid=1&ssid=7&smid=0", must="Circulars")
    page("sebi", "sebi_list_press", c, base + "doListing=yes&sid=6&ssid=23&smid=0", must="Press Releases")
    page("sebi", "sebi_list_consultation", c, base + "doListing=yes&sid=4&ssid=38&smid=35", must="Consultation")
    page("sebi", "sebi_board_pr_104725", c, "https://www.sebi.gov.in/media-and-notifications/press-releases/sep-2026/key-decisions-taken-in-the-sebi-board-meeting-dated-24th-september-2026_104725.html", must="Board Meeting")
    pdf("sebi", "sebi_board_pr_104725_pdf", c, "https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1790259036651.pdf")


def g_gst(c):
    page("gst", "gst_home", c, "https://gstcouncil.gov.in/", must="56th")
    f = rss("gst", "gst_rss", c, "https://gstcouncil.gov.in/rss.xml")
    if f is not None:
        print("      gst rss titles (staff names, not meetings):", [e.title for e in f.entries[:3]])
    page("gst", "gst_meetings", c, "https://gstcouncil.gov.in/en/gst-council-meeting", must="GST Council Meeting")
    pdf("gst", "gst_56th_pib_pdf", c, "https://gstcouncil.gov.in/sites/default/files/2025-09/press_release_press_information_bureau_0.pdf")


def g_mof(c, ci):
    for name, url in [("dea_home", "https://dea.gov.in/"), ("cbic_home", "https://www.cbic.gov.in/"),
                      ("cbic_taxinfo", "https://taxinformation.cbic.gov.in/"), ("egazette_home", "https://egazette.gov.in/")]:
        r = fetch(c, url, name=name + "_verify")
        rec("mof", name + "_verify", r, r.get("status") == 200, r.get("error") or "")
        page("mof", name + "_noverify", ci, url)
    page("mof", "dea_press_release_list", ci, "https://dea.gov.in/index.php/documents-press-release")
    pdf("mof", "dea_c_borrowing_h2", ci, "https://dea.gov.in/files/press_release_documents/c.pdf")
    pdf("mof", "dea_b_issuance_calendar", ci, "https://dea.gov.in/files/press_release_documents/b.pdf")
    page("mof", "indiabudget_home", c, "https://www.indiabudget.gov.in/")
    r = fetch(c, "https://incometaxindia.gov.in/", name="cbdt_home")
    rec("mof", "cbdt_home", r, r.get("status") == 200, "httpx blocked (403); reachable in headless Chrome")


def g_dgft(c):
    for opt in ["notification", "public-notice", "trade-notice"]:
        page("dgft", f"dgft_{opt}", c, f"https://www.dgft.gov.in/CP/?opt={opt}", must="CRT DT")
    pdf("dgft", "dgft_notif_41_rodtep", c, "https://content.dgft.gov.in/Website/dgftprod/be0b7ef2-e1e3-48a1-adc8-529ba4de48c5/RODTEP NOTIFICATION.pdf")


def g_mospi(c):
    page("mospi", "mospi_home_spa_shell", c, "https://mospi.gov.in/")
    r = fetch(c, "https://api.mospi.gov.in/", name="mospi_api_host")
    rec("mospi", "mospi_api_host", r, r.get("status") == 200, r.get("error") or "")
    j = {"Accept": "application/json"}
    page("mospi", "mospi_api_home_en", c, "https://www.mospi.gov.in/api/main-site/get-home-main-site-data?lang=en", must="latestReleasesData", headers=j)
    page("mospi", "mospi_api_latest_arc", c, "https://www.mospi.gov.in/api/documents/get-latest-release-calender", must="releaseCalender", headers=j)
    r = fetch(c, "https://www.mospi.gov.in/api/latest-release/get-web-latest-release-list", name="mospi_api_latest_release_GET", headers=j)
    rec("mospi", "mospi_api_latest_release_GET", r, False, "GET -> 403; endpoint is POST-only (see below)")
    # Read-only JSON listing used by https://www.mospi.gov.in/latest-releases (no auth/token).
    body = {"page_no": 1, "page_size": 60, "search_term": "", "sort_field": "published_year", "sort_order": "DESC",
            "from_date": "", "to_date": "", "lang": "en", "data_source": "web"}
    hp = {"Accept": "application/json", "Origin": "https://www.mospi.gov.in", "Referer": "https://www.mospi.gov.in/latest-releases"}
    rp = c.post("https://www.mospi.gov.in/api/latest-release/get-web-latest-release-list", json=body, headers=hp)
    (RAW / "mospi_api_latest_release_list_POST_en.json").write_bytes(rp.content[:50_000])
    rows = rp.json().get("data", []) if rp.status_code == 200 else []
    rec("mospi", "mospi_api_latest_release_POST", {"url": "POST get-web-latest-release-list", "status": rp.status_code,
        "ctype": rp.headers.get("content-type"), "etag": rp.headers.get("etag"), "bytes": len(rp.content)}, len(rows) > 0, f"rows={len(rows)}")
    for d in rows:
        if re.search(r"(?i)\bCPI\b|Industrial Production \(IIP\)|Gross Domestic Product", d.get("title", "")):
            p = (d.get("file_one") or {}).get("path", "")
            m = re.search(r"(\d{13})", p)
            up = datetime.fromtimestamp(int(m.group(1)) / 1000, IST).strftime("%Y-%m-%d %H:%M") if m else None
            print(f"      {d.get('published_year')} upload(file-epoch IST)={up} {d['title'][:70]}")
    B = "https://www.mospi.gov.in/uploads/latestReleases/"
    for n, f in [("mospi_cpi_aug26", "latest_release_1789381904344_6c792dcf-8a9f-4fca-93d3-99d833bdb358_Press_Release_of_CPI_for_August_2026.pdf"),
                 ("mospi_cpi_jul26", "latest_release_1786529680747_3113661d-1a2b-4b9a-af06-b340193ef9a0_Press_Release_CPI_July_2026.pdf"),
                 ("mospi_iip_aug26", "latest_release_1790590696008_1b346687-0a5f-445a-83a2-28283c586094_IIP_Press_Release_August_2026.pdf"),
                 ("mospi_iip_jul26", "latest_release_1787912132242_3d2fc197-d8a9-48a0-840f-d0cdc12c6835_IIP_Press_Release_July_2026.pdf"),
                 ("mospi_gdp_q1fy27", "latest_release_1788172583113_d65a77cf-240e-4491-82ee-59f78618fa41_Press_Note_on_GDP_Estimates_for_Q1_2026-27.pdf"),
                 ("mospi_gdp_q4fy26", "latest_release_1780655857536_5ac01869-ca4a-422d-b7a7-57b81da60932_Press_Note_on_GDP_Estimates_for_Q4_2025-26_and_PE_FY_2025-26_F.pdf")]:
        pdf("mospi", n, c, B + f)
    page("mospi", "esankhyiki_llms_txt", c, "https://esankhyiki.mospi.gov.in/llms.txt", must="Consumer Price Index")


def g_oea(c):
    B = "https://eaindustry.nic.in/"
    page("oea", "oea_home", c, B, must="press_release_")
    pdf("oea", "oea_wpi_202608", c, B + "press_release/press_release_202608.pdf")
    pdf("oea", "oea_wpi_202606_archive", c, B + "archive_data/wpi_press_release_202223/press_release_202606.pdf")
    pdf("oea", "oea_ici_20260921", c, B + "eight_core_infra/Press_Release_ICI_20260921.pdf")
    pdf("oea", "oea_ici_IPR_2026_07", c, B + "archive_data/ici_press_release/IPR_2026_07.pdf")
    pdf("oea", "oea_wpi_arc", c, B + "uploaded_files/wpi/Advance_Release_Calander_WPI_OPPI_IPPI.pdf")
    pdf("oea", "oea_ici_arc", c, B + "uploaded_files/Advance_Release_Calander_ICI.pdf")


def g_cga(c):
    page("cga", "cga_home", c, "https://cga.nic.in/", must="Release of Union Government Accounts")
    page("cga", "cga_release_aug26_10549", c, "https://cga.nic.in/Circular/Published/10549.aspx")
    page("cga", "cga_arc", c, "https://cga.nic.in/Page/Advance-Release-Calendar.aspx", must="Calendar")


def g_imd(c):
    page("imd", "imd_mausam_home", c, "https://mausam.imd.gov.in/", must="press_release|marquee_data")
    page("imd", "imd_press_release_list", c, "https://internal.imd.gov.in/pages/press_release_mausam.php", must=r"press_release/\d{8}_pr_\d+\.pdf")
    B = "https://internal.imd.gov.in/press_release/"
    pdf("imd", "imd_lrf_stage1_20260415_pr_4893", c, B + "20260415_pr_4893.pdf")
    pdf("imd", "imd_lrf_stage2_20260529_pr_5028", c, B + "20260529_pr_5028.pdf")


def g_nse(c):
    n = client()
    for u in ["https://www.nseindia.com/", "https://www.nseindia.com/all-reports"]:
        fetch(n, u, save=False)
    have = sorted(n.cookies.keys())
    print("      NSE cookies after bootstrap:", have, "(need _abck + nsit)")
    H = {"Referer": "https://www.nseindia.com/all-reports", "Accept": "application/json, text/plain, */*"}
    page("nse", "nse_api_circulars", n, "https://www.nseindia.com/api/circulars", must="circNumber", headers=H)
    page("nse", "nse_api_circulars_daterange", n, "https://www.nseindia.com/api/circulars?fromDate=01-09-2026&toDate=05-09-2026", must="circNumber", headers=H)
    page("nse", "nse_api_reportASM", n, "https://www.nseindia.com/api/reportASM", must="longterm", headers=H)
    page("nse", "nse_api_reportGSM", n, "https://www.nseindia.com/api/reportGSM", must="gsmStage", headers=H)
    pdf("nse", "nse_circ_CML76667", c, "https://nsearchives.nseindia.com/content/circulars/CML76667.pdf")
    page("nse", "niftyindices_press", c, "https://www.niftyindices.com/press-release", must="Press_Release/ind_prs")
    pdf("nse", "niftyindices_ind_prs15092026", c, "https://www.niftyindices.com/Press_Release/ind_prs15092026.pdf")
    pdf("nse", "niftyindices_ind_prs25092026", c, "https://www.niftyindices.com/Press_Release/ind_prs25092026.pdf")


def g_bse(c):
    H = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com", "Accept": "application/json, text/plain, */*"}
    u = "https://api.bseindia.com/BseIndiaAPI/api/getCurrPreNextNoticesData_New/w?flag="
    r = fetch(c, u, name="bse_api_notices_httpx", headers=H)
    rec("bse", "bse_api_notices_httpx", r, r.get("status") == 200, "Akamai 'Access Denied' for httpx TLS fingerprint" if r.get("status") == 403 else "")
    try:
        from curl_cffi import requests as cr
    except ImportError:
        print("      curl_cffi not installed -> rerun with: uv run --with curl_cffi ... bse")
        return
    s = cr.Session(impersonate="chrome124")
    rr = s.get(u, headers=H, timeout=30)
    (RAW / "bse_api_getCurrPreNextNoticesData_New_cffi.json").write_bytes(rr.content[:50_000])
    rows = rr.json().get("Table", []) if rr.status_code == 200 else []
    rec("bse", "bse_api_notices_curl_cffi", {"url": u, "status": rr.status_code, "ctype": rr.headers.get("content-type"),
        "bytes": len(rr.content)}, len(rows) > 0, f"rows={len(rows)}")
    pdf("bse", "bse_notice_20261001-8", c, "https://www.bseindia.com/downloads/UploadDocs/Notices/20261001-8/20261001-8.pdf", headers={"Referer": "https://www.bseindia.com/"})


GROUPS = {"pib": g_pib, "rbi": g_rbi, "sebi": g_sebi, "gst": g_gst, "mof": None, "dgft": g_dgft,
          "mospi": g_mospi, "oea": g_oea, "cga": g_cga, "imd": g_imd, "nse": g_nse, "bse": g_bse}


def main() -> None:
    sel = sys.argv[1:] or list(GROUPS)
    c = client()
    ci = client(verify=False)  # reachability only, see module docstring
    for g in sel:
        print(f"== {g}")
        try:
            if g == "mof":
                g_mof(c, ci)
            else:
                GROUPS[g](c)
        except Exception as e:  # noqa: BLE001
            print(f"   group {g} crashed: {type(e).__name__}: {e}")
    out = RAW / "_probe_summary.json"
    out.write_text(json.dumps({"run_at": datetime.now(IST).isoformat(), "results": RESULTS}, indent=1), encoding="utf-8")
    bad = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS)} checks, {len(bad)} not-OK (some are expected negatives) -> {out}")


if __name__ == "__main__":
    main()
