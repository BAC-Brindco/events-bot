# Phase 0 feed verification: India Tier 1

Probed 2026-10-01, 15:24 to 17:30 IST, from an Indian IP (Windows workstation). Client was httpx 0.28 with a Chrome 140 UA, at most 1 request per second per host. Playwright (headless `channel="chrome"`) was used only where httpx was blocked or the page is a JS app. Evidence is in `docs/phase0/raw/india_t1/` (first ~50 KB of each body, plus `_fetch_log.jsonl` with every request's status and headers). To reproduce:

```
uv run --project "Z:\Data Pipelines\events_bot" --with curl_cffi python docs/phase0/probes/india_t1_probe.py     # 101 checks
uv run --project "Z:\Data Pipelines\events_bot" python docs/phase0/probes/india_t1_lag_watch.py 120 pib       # RSS first-seen vs official time
```

How to read the table:

- `verified: yes` means fetched successfully in this session with sensible content.
- `partial` means reachable, but only with a workaround (no TLS verification, Playwright, or curl_cffi) or with content that is not usable as-is.
- All times are IST.
- "LM" means the HTTP `Last-Modified` header converted to IST.

| source | country | tier | kind | feed type | URL | verified | lag | notes |
|---|---|---|---|---|---|---|---|---|
| PIB press releases (English, Delhi) | IN | 1 | stream | rss | `https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3&reg=3` | yes | First seen in RSS **median 5.7 min** (range 2.0-8.9) after "Posted On". n=30, 2-min poll. One outlier at 59.5 min | **The URL advertised on ViewRss.aspx (`...Regid=3` without `&reg=3`) 302-redirects to the HINDI feed (`Lang=2&reg=48`).** Feed has only 20 items. Each item has title + link only: no pubDate, no description, no ministry. Fake `ETag: XXXXXXXX` and no 304. Item links point to `PressReleaseIframePage.aspx`, which also redirects to `lang=2`. Rewrite links to `PressReleasePage.aspx?PRID=N&reg=3&lang=1`. Volume: 59 English Delhi releases by 17:30 on a light day (eve of a holiday). That is about 30 new items in 95 min in the afternoon, so 20 items cover only about 1 h. The PRID sequence covers all 14 languages and regional offices (about 215 PRIDs between 07:56 and 16:00 today), which is where "100+/day" comes from. Photos (ModId=8) and Features (ModId=18) feeds are listed on the page but were not used. No per-ministry RSS exists: ViewRss.aspx lists only these 3 feeds. |
| PIB all releases today, grouped by ministry | IN | 1 | stream | html | `https://www.pib.gov.in/allRel.aspx?reg=3&lang=1` | yes | n/a (live page) | Lists every English Delhi release for today, grouped under an `<h3 class='font104'>` ministry heading, with `PressReleaseDetail.aspx?PRID=` links. This gives **ministry tags with no per-item fetch**, and it is the backstop if more than 20 items land between RSS polls. Previous days need an ASP.NET postback (date dropdowns), which was not attempted. |
| PIB release page (ministry + timestamp) | IN | 1 | stream | html | `https://www.pib.gov.in/PressReleasePage.aspx?PRID={PRID}&reg=3&lang=1` | yes | "Posted On" has minute resolution | Stable selectors are `#MinistryName`, `#Titleh2` and `#PrDateTime` ("Posted On: 01 OCT 2026 2:31PM by PIB Delhi"). The ministry tag is only on the release page or allRel, never in the RSS. **Cabinet/CCEA:** `#MinistryName` = `Cabinet` or `Cabinet Committee on Economic Affairs (CCEA)`, with titles "Cabinet approves ...". The same decision is often re-posted under the line ministry, e.g. Rabi MSP under Agriculture (PRID 2316956, 15:19) next to CCEA (PRID 2316953, 15:15). Dedupe on title. |
| RBI press releases | IN | 1 | both | rss | `https://rbi.org.in/pressreleases_rss.xml` | yes | pubDate = RBI's own release stamp, rounded to 5 min. First seen **≤ 2 min** after pubDate (n=2: 17:00 seen 17:01:27; 17:20 seen 17:21:43) | **Only 10 items**, about 1 day at about 8 PRs/day. `pubDate` has **no timezone** ("Thu, 01 Oct 2026 14:35:00", which is IST), so feedparser will read it as UTC and be 5h30m off. Weak ETag and LM, conditional GET returns **304**, `max-age=20`. Covers MPC statements, borrowing-calendar echoes, draft-direction calls for comments ("RBI invites comments on the Draft ...", prid 63587) and the monthly Bulletin PR (prid 63674). Links go to `BS_PressReleaseDisplay.aspx?prid=N` (sequential). |
| RBI notifications (circulars, master directions/circulars) | IN | 1 | stream | rss | `https://rbi.org.in/notifications_rss.xml` | yes | First seen ≤ 2 min after pubDate (n=4, all 17:20, seen 17:21:43) | 10 items (about 9 days). Same naive-IST pubDate. 304 supported. Links go to `NotificationUser.aspx?Id=N&Mode=0`. |
| RBI speeches | IN | 1 | stream | rss | `https://rbi.org.in/speeches_rss.xml` | yes | n/m | 10 items (about 6 weeks). 304 supported. |
| RBI publications | IN | 1 | stream | rss | `https://rbi.org.in/Publication_rss.xml` | yes | n/m | 10 items (about 8 weeks). The Bulletin is announced via the press-release RSS, not this feed. `AnnualReportMain_rss.xml` is not sorted by date (stale). |
| RBI MPC schedule + MPC document index | IN | 1 | scheduled | html | `https://www.rbi.org.in/scripts/Annualpolicy.aspx` | yes | n/a | Holds the FY2026-27 schedule (posted Mar 23, 2026): Apr 6-8, Jun 3-5, Aug 3-5, **Oct 5-7 2026**, Dec 2-4 2026, Feb 3-5 2027. It also links the MPS/Resolution, Governor's Statement, SDRP and Minutes for each meeting. The MPS text also states "next meeting ... October 5 to 7, 2026". |
| RBI press-release page (MPS / Governor's Statement / SDRP / Minutes, full text) | IN | 1 | scheduled | html | `https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={prid}` | yes | page shows date only ("Date : Aug 05, 2026"), no time | Full statement text is **in the HTML**, so Stage 1 can parse it without the PDF. Aug 2026: MPS 63287, Gov. Statement 63288, SDRP 63289 (consecutive), Minutes 63403. Jun 2026: MPS 62863, Gov. Statement 62864, Minutes 62974. There was no separate June SDRP: none is listed and prids 62861-62868 hold none. |
| RBI document PDFs (rbidocs) | IN | 1 | scheduled | pdf | `https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR{n}{hash}.PDF` | yes (intermittent block) | MPS PDF LM 10:24 (Jun 5) and **11:20 (Aug 5)** vs 10:00 decision | **Intermittent F5 bot challenge:** HTTP 200 `text/html` "Please enable JavaScript / Request Rejected" (also seen in headless Chrome) at 15:29. It cleared within minutes and could not be reproduced later. Always check the `%PDF` magic bytes and retry, and send `Referer: https://www.rbi.org.in/`. Strong ETag/LM. PDFs can be replaced later (Aug Gov. Statement LM is Aug 10). |
| RBI master directions | IN | 1 | stream | html | `https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx` | yes | n/m | 795 KB page. New or amended MDs also arrive via the notifications RSS, so this page is reference only. |
| RBI draft directions (RE-wise) | IN | 1 | stream | html | `https://www.rbi.org.in/Scripts/BS_ViewREwiseDraftDirections.aspx` | yes | n/m | Also `DraftNotificationsGuildelines.aspx` (sic). Drafts are announced through the press-release RSS, so use RSS plus a title keyword ("Draft"). |
| RBI Bulletin | IN | 1 | scheduled | html | `https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx` | yes | n/m | September 2026 issue dated Sep 25, with PDFs under `rdocs/Bulletin/PDFs/`. Announced via the press-release RSS (prid 63674). |
| RBI YouTube (presser / Governor's statement video) | IN | 1 | both | rss | `https://www.youtube.com/feeds/videos.xml?channel_id=UCIfCOl43tunZVNYafeC4RQA` | yes | `Cache-Control: max-age=900`, so up to 15 min stale | The channel ID comes from the YouTube link on rbi.org.in, and the feed title is "Reserve Bank of India". 15 items, mostly awareness shorts, so the Aug 5 presser has already dropped off and its publish time could not be checked. No validators. |
| SEBI all-in-one RSS | IN | 1 | stream | rss | `https://www.sebi.gov.in/sebirss.xml` | yes (but lagging) | **STALE: at 17:30 the feed still had 0 of the 22 items dated 1 Oct on the SEBI listing** (including press release 104873). Feed LM was 14:00 and its newest item was 30 Sep. pubDate is date only | Single combined feed of 30 items (about 3 days), dominated by enforcement and recovery orders. Classify by URL path: `/legal/circulars/`, `/media-and-notifications/press-releases/`, `/reports-and-statistics/reports/` (CPs). LM supported, 304 OK. |
| SEBI listing: all | IN | 1 | stream | html | `https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListingAll=yes` | yes | same-day: 22 items dated 1 Oct were listed while the RSS had none | Server-rendered table with Date, Type and Title. Pagination is POST, which was not used. |
| SEBI listing: circulars | IN | 1 | stream | html | `https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0` | yes | page LM ≈ upload time (circular 104323 LM 19:18, attachment epoch 19:17) | 25 rows per page. Detail page `.../legal/circulars/{mon-yyyy}/{slug}_{id}.html`, PDF at `sebi_data/attachdocs/{mon-yyyy}/{epoch_ms}.pdf`. The epoch-ms filename is the upload timestamp. |
| SEBI listing: press releases (board outcomes) | IN | 1 | both | html | `https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=6&ssid=23&smid=0` | yes | Board meeting 24 Sep 2026: outcome PDF uploaded **19:40** the same day | 5,871 PRs on record. Board outcomes are titled "Key decisions taken in the SEBI Board Meeting dated ..." (PR 59/2026, id 104725). |
| SEBI listing: consultation papers | IN | 1 | stream | html | `https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=4&ssid=38&smid=35` | yes | CP uploads at 15:36 (Sep 15) and 14:17 (Sep 12) | Title prefix is "Consultation Paper on ...". |
| GST Council site RSS | IN | 1 | stream | rss | `https://gstcouncil.gov.in/rss.xml` | partial (fetched, content useless) | n/a | Drupal default feed of **staff-directory nodes** (people's names), not meetings or press releases. Do not use. |
| GST Council meetings page | IN | 1 | scheduled | html | `https://gstcouncil.gov.in/en/gst-council-meeting` | yes | days to weeks | Agenda and minutes only up to the 55th meeting (Dec 2024). The homepage shows the 56th (3-4 Sep 2025) as the latest. Its "press release" is a PDF **printout of the PIB page** uploaded 9 Sep 2025 12:55, which is 6 days after PIB. `/press-release` shows "No Data Found!". |
| GST Council outcome via PIB echo (Ministry of Finance) | IN | 1 | both | html | `https://www.pib.gov.in/PressReleasePage.aspx?PRID=2086873&reg=3&lang=1` | yes | PIB posted same evening: 55th at 20:23 (21 Dec 2024), 56th at 22:39 (3 Sep 2025) | **PIB is the primary channel.** Match ministry = Ministry of Finance and title ~ "Recommendations of the NNth Meeting of the GST Council". The 56th PIB PRID was not recovered, but its posted time is in the gstcouncil PDF printout. |
| DEA press releases | IN | 1 | both | html | `https://dea.gov.in/index.php/documents-press-release` | partial (TLS) | n/a | **TLS verify fails** (Let's Encrypt `YR1` intermediate, `ISRG Root YR` not in the certifi bundle). Reachable with verify off. Drupal page, LM header present. |
| DEA borrowing calendar PDFs | IN | 1 | scheduled | pdf | `https://dea.gov.in/files/press_release_documents/c.pdf` (H2 borrowing plan), `.../b.pdf` (issuance calendar) | partial (TLS) | H2 FY27 plan LM 25 Sep 17:19, calendar LM 17:21 | **File names are reused** (`a.pdf`, `b.pdf`, `c.pdf`, `E.pdf`, `H.pdf`) and served with `Cache-Control: max-age=31536000`. Content at a URL changes over time, so never key on the URL. |
| Borrowing calendar via RBI echo | IN | 1 | scheduled | rss | `https://rbi.org.in/pressreleases_rss.xml` (prid 63668 "Issuance Calendar for Marketable Dated Securities Oct 2026-Mar 2027", 63669 "Calendar for Auction of GoI Treasury Bills Q3") | yes | same day as DEA (Sep 25) | **Use this instead of DEA.** It is on the clean RSS, and the HTML press release carries the full text. |
| Union Budget documents | IN | 1 | scheduled | pdf | `https://www.indiabudget.gov.in/doc/Budget_Speech.pdf` (also `doc/Finance_Bill.pdf`, `doc/memo.pdf`, `doc/Budget_at_Glance/budget_at_a_glance.pdf`, `doc/cen/cus{NN}26.pdf`) | yes | Speech PDF LM 1 Feb 2026 06:11 (pre-staged) | Stable paths, overwritten each year. Case-insensitive (IIS). Budget-day customs notifications are under `doc/cen/`. No feed. Once a year, so handle it as a scheduled job. |
| CBDT notifications / press | IN | 1 | stream | api | `https://www.incometaxindia.gov.in/` (Liferay JSON `/o/search/v1.0/search?...` seen in browser) | partial (Playwright only) | n/m | **httpx gets 403** (bot protection). It renders fine in headless Chrome, where the app calls Liferay headless JSON APIs. Not verified via a plain client. No RSS found. |
| CBIC notifications / tax information portal | IN | 1 | stream | api | `https://www.cbic.gov.in/` (Angular SPA, `/api/getContent/News-Media`, `/api/getTickerData/Tickers`); `https://taxinformation.cbic.gov.in/` | partial (TLS + SPA) | n/m | **TLS chain incomplete** (Sectigo OV R36 intermediate not sent). The HTML is a 3 KB SPA shell and content comes from a JSON API (seen in Playwright). No RSS. |
| Gazette of India | IN | 1 | stream | html | `https://egazette.gov.in/` | partial (TLS) | n/m | **TLS chain incomplete** (LE `YR2` intermediate not sent). The ASP.NET site puts a session id in the URL (`/(S(...))/default.aspx`). Search is a postback form (not used). **No feed, no API.** |
| DGFT notifications | IN | 1 | stream | html | `https://www.dgft.gov.in/CP/?opt=notification` | yes | **"CRT DT" column gives the upload time to the second**: Notif 41/2026-27 (RoDTEP to 31 Dec) 30/09 20:28:04; Notif 37 30/09 17:20:19 | Server-rendered table with all rows back to Oct 2023 (235 rows, 291 KB). Columns: Number, Year, Description, Date, CRT DT, Attachment. Attachments on `content.dgft.gov.in/Website/dgftprod/{uuid}/{name}.pdf`, served as `application/octet-stream`, with spaces in names. Some are scanned ("Adobe Scan ..."), so OCR is needed. The RoDTEP PDF's LM is 01/10 13:48, so files get re-uploaded. |
| DGFT public notices | IN | 1 | stream | html | `https://www.dgft.gov.in/CP/?opt=public-notice` | yes | PN 31/2026-27 CRT 30/09 19:50:08 | Same table format. |
| DGFT trade notices | IN | 1 | stream | html | `https://www.dgft.gov.in/CP/?opt=trade-notice` | yes | TN 28 CRT 16/09 20:45:25; TN 25 07/09 23:01:00 | Same format. Releases are often late evening. |
| MoSPI latest releases (CPI/IIP/GDP) | IN | 1 | scheduled | api | `POST https://www.mospi.gov.in/api/latest-release/get-web-latest-release-list` body `{"page_no":1,"page_size":60,"sort_field":"published_year","sort_order":"DESC","lang":"en","data_source":"web",...}` | yes | CPI/IIP/GDP files uploaded **15:43-16:07** for 16:00 releases (see Lag evidence) | The site was rebuilt as a React SPA, so every HTML route returns the same 2.6 KB shell. This is the JSON the `/latest-releases` page calls: read-only, no auth, weak ETag. **GET returns 403**, so it is POST-only. The upload time is embedded in the file name (`latest_release_{epoch_ms}_{uuid}_{name}.pdf`). It goes back to Mar 2026 within 60 rows. |
| MoSPI home JSON (latest 4 releases) | IN | 1 | scheduled | api | `https://www.mospi.gov.in/api/main-site/get-home-main-site-data?lang=en` | yes | same as above | GET-able fallback. `latestReleasesData` holds only the newest 4 (with `created_at`), plus `marqueeNews`. |
| MoSPI advance release calendar | IN | 1 | scheduled | api | `https://www.mospi.gov.in/api/documents/get-latest-release-calender` → `uploads/documents/releaseCalender/...ARC 2026-27 updated till August 2026.pdf` | yes | n/a | Returns a pointer to the ARC PDF (created 2026-09-01). The structured calendar endpoint `.../api/release-calender/fetch-all-release-calender-Web` returns 403 on GET. |
| MoSPI release PDFs | IN | 1 | scheduled | pdf | `https://www.mospi.gov.in/uploads/latestReleases/latest_release_{epoch_ms}_{uuid}_{name}.pdf` | yes | see Lag evidence | Names are unpredictable (uuid), so they must be discovered via the JSON above. Older releases sit at `uploads/PressRelease/...` (CPI April 2026). Weak ETag, LM present. |
| MoSPI API host (`api.mospi.gov.in`) | IN | 1 | scheduled | api | `https://api.mospi.gov.in/` | no | n/a | TLS handshake fails: `UNSAFE_LEGACY_RENEGOTIATION_DISABLED`. It would need an OpenSSL legacy-renegotiation flag. Not pursued: eSankhyiki is a data API, not a release alert. |
| eSankhyiki data portal | IN | 1 | scheduled | html | `https://esankhyiki.mospi.gov.in/llms.txt` (also `/ai-catalog.json`, `/.well-known/ard.json`) | yes | n/a | Machine-readable index of datasets (`macroindicators?product=cpi/iip/nas`). Useful for verifying numbers after a release, not for the alert itself. |
| OEA WPI press release | IN | 1 | scheduled | pdf | `https://eaindustry.nic.in/press_release/press_release_{YYYYMM}.pdf` (archive: `archive_data/wpi_press_release_202223/press_release_{YYYYMM}.pdf`) | yes | LM **11:26** (Sep 14) and 11:42 (Jul 14) for the 12:00 noon embargo | **Predictable URL** from the data month. Once superseded, the file moves to `archive_data/`; July 2026 (`202607`) was missing from both locations (404). Strong ETag. |
| OEA Eight Core Industries press release | IN | 1 | scheduled | pdf | `https://eaindustry.nic.in/eight_core_infra/Press_Release_ICI_{YYYYMMDD}.pdf` (archive `archive_data/ici_press_release/IPR_{YYYY}_{MM}.pdf`) | yes | LM **17:06** (Sep 21) and 17:02 (Aug 20) for the 17:00 release | File name uses the release date, which shifts for holidays, so discover it from the homepage. The Jul-2026 archived PDF page 1 has **no text layer**, so OCR is needed. ARC PDFs: `uploaded_files/wpi/Advance_Release_Calander_WPI_OPPI_IPPI.pdf` and `uploaded_files/Advance_Release_Calander_ICI.pdf` ("released ... through PIB as well as on website"). |
| CGA monthly accounts (site) | IN | 1 | scheduled | html | `https://cga.nic.in/` → `/Circular/Published/10549.aspx` ("Release of Union Government Accounts upto August 2026") | partial | n/a | Listing is reachable, but **the attachment is not exposed** in the HTML or the rendered DOM (ASP.NET UpdatePanel/postback). No feed. ARC: `https://cga.nic.in/Page/Advance-Release-Calendar.aspx`. |
| CGA monthly accounts via PIB echo | IN | 1 | scheduled | html | `https://www.pib.gov.in/PressReleasePage.aspx?PRID=2317034&reg=3&lang=1` | yes | Posted **30 SEP 2026 4:41PM** (Ministry of Finance) | Title: "Monthly Review of Accounts of Union Government of India upto the month of August 2026 (FY 2026-27)". **Use PIB.** |
| IMD press releases (incl. monsoon LRF) | IN | 1 | both | html | `https://internal.imd.gov.in/pages/press_release_mausam.php` → `https://internal.imd.gov.in/press_release/{YYYYMMDD}_pr_{n}.pdf` | yes | **LRF stage 1:** press conference 13 Apr 16:00; listed PDF dated 15 Apr, LM 15 Apr 12:12 (**about 44 h late**). Stage 2: 29 May, LM 10:23 | 4 MB single page with 6,778 rows (deep archive). English and Hindi releases are separate rows. Also `https://mausam.imd.gov.in/` homepage marquee (`Forecast/marquee_data/*.pdf`, file names with spaces). No RSS found. |
| NSE circulars | IN | 1 | stream | api | `https://www.nseindia.com/api/circulars` (`?fromDate=DD-MM-YYYY&toDate=DD-MM-YYYY`) | yes | listing has date only (`cirDate`). PDF LM e.g. CML76667 15:00 | Cookie bootstrap with plain httpx works: `/` returns 403 but sets cookies, then `/all-reports`, which yields `_abck` + `nsit`. Default response is 7 days, 182 circulars, 30-43/day. `circDepartment` lets you filter (e.g. "Surveillance & Investigation"). **Bad data:** some rows have `circFilelink` = `.../null76676.null`. PDFs at `nsearchives.nseindia.com/content/circulars/{DEPT}{num}.pdf` need no cookie. |
| NSE ASM lists | IN | 1 | stream | api | `https://www.nseindia.com/api/reportASM` | yes | daily snapshot (`asmTime` = refresh date, not entry date) | Same endpoint and caveats as in-house `Z:\Data Pipelines\nse-surveillance-pipeline\scrapers\asm_scraper.py` (`_ASM_URL`), which uses curl_cffi; httpx also works today. Additions have to be found by diffing snapshots. |
| NSE GSM list | IN | 1 | stream | api | `https://www.nseindia.com/api/reportGSM` | yes | `gsmTime` 01-Oct-2026 08:07:02 (pre-open refresh) | Same as `nse-surveillance-pipeline\scrapers\gsm_scraper.py`. Returns a flat JSON array. |
| NSE Indices (index changes) | IN | 1 | both | html | `https://www.niftyindices.com/press-release` → `https://www.niftyindices.com/Press_Release/ind_prs{DDMMYYYY}[_{n}].pdf` | yes | "Replacements in indices" PDFs LM **22:15** (15 Sep) and **21:00** (25 Sep): after-hours | 1,508 PRs back to 1998, on one 660 KB page. Strong ETag. Semi-annual review: `ind_prs10082026.pdf` ("Replacements in indices w.e.f. September 30, 2026"). No feed. |
| BSE notices | IN | 1 | stream | api | `https://api.bseindia.com/BseIndiaAPI/api/getCurrPreNextNoticesData_New/w?flag=` | partial (curl_cffi only) | `dt_tm` is date only. PDF LM e.g. 15:14 | **httpx gets 403 from Akamai ("Access Denied")** even with Referer/Origin. **curl_cffi `impersonate="chrome124"` returns 200 JSON** (as in `Z:\Data Pipelines\nse-bse-disclosures-pipeline\scrapers\bse_session.py`). Fields: Notice_no `YYYYMMDD-N`, Subject, Dept_Name, category_name, FileName. `LatestNewBindset_New/w?str=undefined` returned `{}`. curl_cffi is **not** in the project deps. |
| BSE notice PDFs | IN | 1 | stream | pdf | `https://www.bseindia.com/downloads/UploadDocs/Notices/{YYYYMMDD-N}/{YYYYMMDD-N}.pdf` | yes | n/a | Plain httpx works for the PDFs. Only `api.bseindia.com` blocks it. |

## URL patterns

Each pattern below has two concrete examples, all fetched successfully this session.

- **PIB release:** `https://www.pib.gov.in/PressReleasePage.aspx?PRID={PRID}&reg=3&lang=1`
  - `...PRID=2316953&reg=3&lang=1`: CCEA, "Cabinet approves One Network, Smarter Traffic ...", 30 SEP 2026 3:15PM
  - `...PRID=2316956&reg=3&lang=1`: Agriculture, "Cabinet approves MSP for Rabi Crops for Marketing Season 2027-28", 3:19PM
- **RBI MPC Monetary Policy Statement (resolution):** `https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={prid}`
  - prid=63287 (Aug 5 2026). PDF: `https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR80907599DE5FD164918A49085C9D6270116.PDF`
  - prid=62863 (Jun 5 2026). PDF: `.../PR3855508EB4A59FF46F9B57BBA200AA250B8.PDF`
- **RBI Governor's Statement:**
  - prid=63288 (Aug 5). PDF: `.../PR810CF4E75DAF41B4EBAA08034864C55D8A9.PDF`
  - prid=62864 (Jun 5). PDF: `.../PR386E3EF62B419E04A85ABE96B0EB8BAC99A.PDF`
- **RBI Statement on Developmental and Regulatory Policies:**
  - prid=63289 (Aug 5). PDF: `.../PR811985F030C62F347038BCF99E6A27A7CE2.PDF`
  - prid=62516 (Apr 8 2026). No June SDRP was issued.
- **RBI MPC minutes:**
  - prid=63403 (Aug 19, for Aug 3-5)
  - prid=62974 (Jun 19, for Jun 3-5)
- **RBI borrowing-calendar echo:**
  - prid=63668 (issuance calendar H2 FY27)
  - prid=63669 (T-bill calendar Q3)
- **SEBI board outcome:**
  - `https://www.sebi.gov.in/media-and-notifications/press-releases/sep-2026/key-decisions-taken-in-the-sebi-board-meeting-dated-24th-september-2026_104725.html` (PDF `https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1790259036651.pdf`)
  - Only one board outcome is visible on listing page 1. Page 2 needs a POST, so a second board example was not fetched (`verified: no`).
- **SEBI circular:**
  - `.../legal/circulars/sep-2026/review-of-position-limits-for-clients-and-penalty-provisions-for-violation-breach-of-position-limits-for-commodity-derivatives-segment_104387.html`
  - `.../legal/circulars/sep-2026/relaxation-in-timeline-with-respect-to-accredited-investor-mandate-for-angel-funds_104323.html`
- **SEBI consultation paper:**
  - `.../reports-and-statistics/reports/sep-2026/consultation-paper-on-measures-to-strengthen-business-continuity-plan-bcp-and-disaster-recovery-dr-of-market-infrastructure-institutions-miis-_104492.html`
  - `..._104464.html` (closing auction session / market timings)
- **GST Council outcome (PIB echo):**
  - PRID=2086873 (55th, 21 Dec 2024 8:23PM)
  - 56th: PIB printout at `https://gstcouncil.gov.in/sites/default/files/2025-09/press_release_press_information_bureau_0.pdf` ("Posted On: 03 SEP 2025 10:39PM")
- **DGFT:**
  - `https://content.dgft.gov.in/Website/dgftprod/be0b7ef2-e1e3-48a1-adc8-529ba4de48c5/RODTEP NOTIFICATION.pdf` (Notif 41/2026-27)
  - `.../2ff9ebb3-dfed-4269-a4e9-2896be4b7fae/Notif 39 E.pdf` (Notif 39/2026-27, MIP extension)
  - The URL is not predictable. It comes from the listing table.
- **MoSPI CPI:**
  - `https://www.mospi.gov.in/uploads/latestReleases/latest_release_1789381904344_6c792dcf-8a9f-4fca-93d3-99d833bdb358_Press_Release_of_CPI_for_August_2026.pdf` (14 Sep 2026)
  - `..._1786529680747_3113661d-1a2b-4b9a-af06-b340193ef9a0_Press_Release_CPI_July_2026.pdf` (12 Aug 2026)
- **MoSPI IIP:**
  - `..._1790590696008_1b346687-0a5f-445a-83a2-28283c586094_IIP_Press_Release_August_2026.pdf` (28 Sep)
  - `..._1787912132242_3d2fc197-d8a9-48a0-840f-d0cdc12c6835_IIP_Press_Release_July_2026.pdf` (28 Aug)
- **MoSPI GDP:**
  - `..._1788172583113_d65a77cf-240e-4491-82ee-59f78618fa41_Press_Note_on_GDP_Estimates_for_Q1_2026-27.pdf` (31 Aug)
  - `..._1780655857536_5ac01869-ca4a-422d-b7a7-57b81da60932_Press_Note_on_GDP_Estimates_for_Q4_2025-26_and_PE_FY_2025-26_F.pdf` (5 Jun)
- **OEA WPI:**
  - `https://eaindustry.nic.in/press_release/press_release_202608.pdf`
  - `https://eaindustry.nic.in/archive_data/wpi_press_release_202223/press_release_202606.pdf`
- **OEA core industries:**
  - `https://eaindustry.nic.in/eight_core_infra/Press_Release_ICI_20260921.pdf`
  - `https://eaindustry.nic.in/archive_data/ici_press_release/IPR_2026_07.pdf`
- **CGA (PIB echo):**
  - PRID=2317034 (Aug 2026 accounts)
  - Only one example was found. The July accounts PIB PRID was not located (`verified: no`).
- **IMD:**
  - `https://internal.imd.gov.in/press_release/20260415_pr_4893.pdf` (LRF stage 1)
  - `https://internal.imd.gov.in/press_release/20260529_pr_5028.pdf` (LRF stage 2)
- **NSE circular PDF:**
  - `https://nsearchives.nseindia.com/content/circulars/CML76667.pdf`
  - `https://nsearchives.nseindia.com/content/circulars/FAOP76666.pdf`
- **NSE Indices:**
  - `https://www.niftyindices.com/Press_Release/ind_prs15092026.pdf`
  - `https://www.niftyindices.com/Press_Release/ind_prs25092026.pdf`
- **BSE notice:**
  - `https://www.bseindia.com/downloads/UploadDocs/Notices/20261001-8/20261001-8.pdf`
  - `.../20261001-7/20261001-7.pdf`
- **Union Budget:**
  - `https://www.indiabudget.gov.in/doc/Budget_Speech.pdf`
  - `https://www.indiabudget.gov.in/doc/budget_speech.pdf` (same file, case-insensitive)
  - Only one Budget per year, so a second year's example is not available at a different URL.

## Lag evidence

These are the timestamps that could be checked against at least 2 past releases. "LM" is the server `Last-Modified` in IST. It shows when a file was last written, which is usually but not always the first publish (re-uploads happen; flagged where seen).

| release | official time | evidence | lag |
|---|---|---|---|
| RBI MPS (resolution) PDF, Jun 5 2026 | 10:00 | PDF created 10:23:03, LM 10:24:50 | **+25 min** for the PDF |
| RBI MPS (resolution) PDF, Aug 5 2026 | 10:00 | PDF created 11:19:16, LM 11:20:39 | **+80 min** for the PDF (or a later re-upload) |
| RBI SDRP PDF, Aug 5 2026 | 10:00 | LM 11:20:03 | +80 min |
| RBI Governor's Statement PDF, Jun 5 / Aug 5 | 10:00 | LM 11:52 (Jun 5) / 10 Aug 15:27 (replaced 5 days later) | not usable for timing |
| RBI MPC minutes | ~14 days after the decision | Jun 5 → Jun 19 (prid 62974); Aug 5 → Aug 19 (prid 63403, PDF created 17:19) | 14 days, about 17:00-17:30 |
| CPI Aug 2026 (MoSPI) | 14 Sep 16:00 (12th was a Saturday) | file-epoch 16:01, LM 16:01:44 | +2 min |
| CPI Jul 2026 | 12 Aug 16:00 | file-epoch 15:44, LM 15:44:40 | uploaded 16 min **before** the embargo |
| CPI Jun / May 2026 | 13 Jul / 12 Jun 16:00 | file-epoch 15:44 / 15:54 | pre-staged |
| IIP Aug 2026 | 28 Sep 16:00 | file-epoch 15:48. LM 17:45 (re-upload) | pre-staged; the listing record is `created_at` 15:50 |
| IIP Jul 2026 | 28 Aug 16:00 | file-epoch 15:45, LM 15:45:32 | pre-staged |
| GDP Q1 FY27 | 31 Aug 16:00 | file-epoch 16:06, LM 16:06:23 | +6 min |
| GDP Q4 FY26 / PE | 5 Jun 16:00 | file-epoch 16:07, LM 16:07:37 | +8 min |
| WPI Aug 2026 (OEA) | 14 Sep 12:00 | LM 11:26:43 | pre-staged 33 min early (embargo text in PDF) |
| WPI Jun 2026 | 14 Jul 12:00 | LM 11:42:02 (archive copy) | pre-staged |
| Core industries Aug 2026 | 21 Sep 17:00 | LM 17:06:15 | +6 min |
| Core industries Jul 2026 | 20 Aug 17:00 | LM 17:02:05 | +2 min |
| GST Council 55th | meeting day 21 Dec 2024 | PIB Posted On 8:23PM | same evening, on PIB |
| GST Council 56th | 3 Sep 2025 | PIB Posted On 10:39PM; gstcouncil.gov.in copy LM 9 Sep 12:55 | PIB same night; council site **+6 days** |
| CGA accounts Aug 2026 | ARC: last working day | PIB Posted On 30 Sep 4:41PM | same day |
| SEBI board meeting 24 Sep 2026 | meeting day | outcome PDF epoch/LM 19:40 | same evening |
| SEBI circular 104323 / CPs 104492, 104464 | n/a | uploads at 19:17 / 15:36 / 14:17 | evening releases are common |
| DEA H2 borrowing plan FY27 | 25 Sep | DEA PDF LM 17:19; RBI prids 63668/63669 same date | same day on both |
| IMD LRF stage 1 2026 | press conference 13 Apr 16:00 (PR 4888) | PDF dated "13 April" in text; listed as 15 Apr; LM 15 Apr 12:12 | **about +44 h** on IMD's press-release page |
| IMD LRF stage 2 2026 | 29 May | LM 10:23 | same morning |
| NSE Indices "Replacements in indices" | n/a | LM 22:15 (15 Sep), 21:00 (25 Sep) | after-hours releases |
| Cabinet (CCEA) 30 Sep 2026 | briefing about 15:00 | PIB CCEA 3:15PM; Agriculture MSP repost 3:19PM | n/a |
| ASI 2024-25 (MoSPI via PIB) | 30 Sep 16:00 | PIB PRID 2316981 Posted On 4:00PM. MoSPI listing record `created_at` 15:17 (pre-staged), but the current file's epoch is 17:12 (replaced) | PIB on time. MoSPI's file was replaced after release |

**Feed-level lag (live, 2026-10-01).** Measured by `india_t1_lag_watch.py`, polling every 120 s. "first_seen" minus the official stamp is an upper bound and includes up to 2 min of poll interval.

- **PIB RSS** (15:26-17:26, 30 new items): RSS first-seen minus "Posted On" had a median of **5.7 min** and a range of 2.0-8.9 min (raw `lag_watch_pib.jsonl`). With a 2-min poll the RSS itself trails "Posted On" by about 2-7 min. One item (AYUSH, Posted On 15:34) appeared at 16:33, **59.5 min** later: "Posted On" is a creation stamp, not always the publish time. Example: MoSPI's "Forward-Looking Survey on Private Corporate Sector CAPEX" was posted 16:00 and seen 16:05.
- **RBI press-release and notification RSS** (15:28-17:23): 6 new items. All were seen at the first poll after their pubDate (17:00 seen 17:01:27; five items at 17:20 seen 17:21:43), so the lag is **≤ 2 min**. RBI pubDates fall on 5-minute slots.
- **SEBI RSS** (15:28-17:23): **0 new items in 2 h**, while the SEBI "all" listing showed 22 items dated 1 Oct, including a press release. The feed's `Last-Modified` stayed at 14:00 IST. **The SEBI RSS is not real-time.** It looks batch-built, with today's items not yet included by 17:30.

**MPC resolution vs 10:00 scheduled.** This could not be measured for the last 2 meetings:

- RBI press-release HTML carries a date only.
- The RSS keeps only 10 items, so the Jun and Aug MPC entries are long gone.
- The Wayback CDX API returned 503 ("Temporarily Offline") during the session.

The PDF evidence above (+25 min Jun, +80 min Aug) argues that **the HTML press-release page, not the PDF, must be the Stage 1 source**. The live test opportunity is the next MPC, **Oct 7, 2026, 6 days away**: poll `pressreleases_rss.xml` every 30 s from 09:55 and record first-seen against the 10:00 pubDate.

## No reliable feed: recommendations

The SPEC makes PIB the single GoI ingestion point. For each ministry-type source, the question is whether PIB carries it in time.

| source | PIB echo verified? | recommendation | reasoning |
|---|---|---|---|
| GST Council outcomes | **yes**: 55th (8:23PM same day), 56th (10:39PM same day) | **(2) secondary official channel: PIB** (Ministry of Finance + "GST Council") | The council site lags PIB by days, its RSS is junk and its press-release page is empty. PIB is effectively the primary channel. |
| CGA monthly accounts | **yes**: PRID 2317034, 30 Sep 4:41PM | **(2) PIB** ("Monthly Review of Accounts of Union Government") | The cga.nic.in attachment is hidden behind a postback. PIB is same-day and parseable. |
| DEA borrowing calendar | n/a: **RBI echo verified** (prid 63668/63669, same day) | **(2) RBI press-release RSS** | Clean RSS with full text in HTML. DEA has broken TLS and reused file names. |
| MoSPI CPI/IIP/GDP | ASI was on PIB at exactly 16:00 (PRID 2316981). CPI/IIP/GDP PIB echo **not verified** (no PIB search API) | **(1) approved monitored poller** of the MoSPI latest-release JSON (POST) on calendar days only (16:00-16:30, every 30 s), **plus (2) PIB** as a parallel channel. Health check: on a scheduled release day, alert if no new row by 16:10. | The JSON is official and structured, and its file timestamps show uploads at 15:43-16:08. It is POST-only and undocumented, so it can change silently, which is why the health check is mandatory. Confirm the CPI PIB echo on 12 Oct 2026. |
| OEA WPI / core industries | ARC says "released through PIB"; **not verified** this session | **(2) PIB** plus **(1) scheduled fetch of the predictable WPI URL** `press_release/press_release_{YYYYMM}.pdf` at 12:00 on the 14th. Core: discover the link from the homepage at 17:00 on the 20th. | WPI has a fully predictable URL and a strong ETag, so it is cheap and robust. The core file name depends on the release date. |
| IMD monsoon LRF | **not verified** (MoES PRID not located) | **(3) manual** on the 2 LRF days a year (calendar reminder), **plus (2) PIB** (MoES) | IMD's own press list was about 44 h late for stage 1. Low frequency does not justify a scraper. |
| DGFT notifications / PNs / TNs | **not found**: RoDTEP notification 41 (CRT 30/09 20:28) not seen in PIB PRIDs scanned (2317021-2317099, 2317301-2317402). Textiles' related RoSCTL extension *was* on PIB at 8:58PM. | **(1) approved monitored scraper** of the 3 server-rendered DGFT tables, polling every 5 min from 10:00 to 24:00 IST. Key on Number + CRT DT. Health check: the table must parse to ≥ 200 rows with a CRT DT column. Never alert on a parse failure silently. | SPEC calls DGFT the most abrupt market mover, and PIB echo is inconsistent. The table is plain HTML, stable, and carries second-level upload timestamps, which makes it the least fragile scraper on this list. eGazette is not usable (no feed, postback search). |
| CBDT | **not verified** | **(2) PIB** (Ministry of Finance, keyword "CBDT") for press releases. **(3) manual** for notifications. | httpx is blocked (403). The site is a Liferay SPA. Building around it means a headless browser, which counts as fragile. |
| CBIC (GST/customs rate notifications) | **not verified** | **(2) PIB** for rate decisions (they follow GST Council or the Budget, both on PIB). Budget-day customs notifications come from `indiabudget.gov.in/doc/cen/`. **(3) manual** otherwise. | The SPA has an undocumented JSON API and a broken TLS chain. |
| Gazette of India | n/a | **(4) drop** as a real-time channel. Keep as a manual reference. | No feed or API, a postback search, and a broken TLS chain. |
| SEBI board outcomes / circulars / CPs | n/a (SEBI is not on PIB) | **(1) approved monitored scraper** of the 3 SEBI listing pages (circulars `sid=1&ssid=7`, press releases `sid=6&ssid=23`, consultation papers `sid=4&ssid=38`), page 1 only, every 5 min from 10:00 to 23:00. Health check: table parses to 25 rows with a date in the first column. Keep the RSS only as a daily reconciliation. | The official RSS lagged by more than 3.5 h and missed every item from the probe day. The listings are plain server-rendered HTML with stable URLs, and page LM / attachment epoch give upload times. |
| NSE Indices index changes | n/a (not GoI) | **(1) approved monitored scraper** of `niftyindices.com/press-release` (one page, links `ind_prs{DDMMYYYY}[_n].pdf`, strong ETag). Poll every 15 min, 17:00-23:30. | No feed. Index changes land after hours (21:00-22:15). Directly relevant to the SMID book. |
| BSE notices | n/a | **(1) monitored poller** using the curl_cffi TLS impersonation already used in-house. Health check on HTTP 200 + `Table` key. | The JSON is undocumented and Akamai-protected. NSE circulars plus NSE ASM/GSM cover most of the need. Treat BSE as secondary. |
| NSE circulars / ASM / GSM | n/a | **(1) monitored poller** reusing the in-house bootstrap (`nse-surveillance-pipeline/scrapers/nse_session.py`) | Unofficial JSON APIs, but they have been stable in-house for months. Add a cookie and schema health check. |

## Flags

1. **PIB's advertised English RSS URL serves Hindi.** `RssMain.aspx?ModId=6&Lang=1&Regid=3` 302s to `Lang=2&reg=48`. Append `&reg=3`. The release links in the feed (`PressReleaseIframePage.aspx`) also bounce to `lang=2`. Rewrite them to `PressReleasePage.aspx?PRID=..&reg=3&lang=1`.
2. **PIB RSS has no pubDate, no ministry, a fake ETag and only 20 items.** Poll at least every 2-3 min (cost about 4.5 KB per poll), use `allRel.aspx` as a gap-filler, and fetch each release page for `#MinistryName` and `#PrDateTime`. PRIDs are shared across 14 languages and regional offices and are **not time-ordered**: a Punjabi PRID 2317388 was "Posted On 21 SEP". Do not use PRID ranges as a clock.
3. **PIB "Posted On" is minute-resolution IST**, with no timezone and no seconds.
4. **The RBI RSS pubDate is naive IST** ("Thu, 01 Oct 2026 14:35:00"). feedparser returns `published_parsed=None` for it, and a generic RFC-822 parser would default it to UTC, which is 5h30m wrong in an output that must carry the release timestamp. Parse it explicitly as Asia/Kolkata.
5. **RBI RSS depth is 10 items.** Press releases roll over in about 1 day (30 Sep 17:30 to 1 Oct 14:35 at probe time). A poller outage of over 24 h loses items. The backstop is that `prid` is sequential, so gaps can be fetched by id.
6. **rbidocs.rbi.org.in can return HTTP 200 HTML bot challenges** ("Please enable JavaScript ... support ID", "Request Rejected") instead of the PDF. Seen at 15:29 for 4/4 PDFs, including in headless Chrome, and not reproducible minutes later. **Validate the `%PDF` magic, retry with backoff, and prefer the HTML press release.**
7. **Decimal hazard in MPC text.** A naive sentence split on "." turned "unchanged at 5.25 per cent" into "5." in the probe. Number extraction must be token-based. This matters directly for the "no model-generated numbers" rule.
8. **Next MPC decision is Wed Oct 7, 2026** (meeting Oct 5-7). That is the cheapest live lag test for RSS vs HTML vs PDF vs YouTube.
9. **No June 2026 SDRP was published.** Stage 2 "diff vs prior" logic must handle a missing document.
10. **Broken TLS chains on GoI hosts:** dea.gov.in (Let's Encrypt `YR1` / `ISRG Root YR`, not in certifi), www.cbic.gov.in and taxinformation.cbic.gov.in (Sectigo intermediate not sent), egazette.gov.in (LE `YR2` intermediate not sent). api.mospi.gov.in fails with `UNSAFE_LEGACY_RENEGOTIATION_DISABLED`. Fix with `truststore` (OS store + AIA) or pinned intermediates. Never use `verify=False` in production.
11. **CBDT (incometaxindia.gov.in) returns 403 to httpx** but works in Chrome. **BSE `api.bseindia.com` returns 403 (Akamai) to httpx** but works with `curl_cffi`, which is not in `pyproject.toml`. NSE `/api/*` works with plain httpx after the 2-step cookie bootstrap (`_abck` and `nsit` were set).
12. **MoSPI is a React SPA now.** Every route returns the same 2.6 KB shell with the same ETag, so any HTML scraper of mospi.gov.in breaks. The release list is a **POST** JSON endpoint (GET returns 403).
13. **Embargoed files are uploaded before release time.** MoSPI CPI/IIP files appear 5-16 min before 16:00, and WPI 18-33 min before 12:00. Whether they are publicly listed early was not tested; it would mean polling before the embargo. **Do not alert before the official time even if a file is visible.** Stamp alerts with the official release time and the first-seen time separately.
14. **CPI date rule:** "4 PM on the 12th" moves to the next working day (Aug 2026 CPI came out Mon 14 Sep). Drive the schedule from the MoSPI ARC, not a fixed rule.
15. **`Last-Modified` is not always first-publish.** Re-uploads were seen: IIP Aug LM 17:45 vs file-epoch 15:48, RBI Governor's Statement LM 5 days later, DGFT RoDTEP PDF re-uploaded the next day.
16. **Filename reuse at DEA:** `a.pdf`, `b.pdf`, `c.pdf` with a 1-year cache lifetime. **Scanned PDFs** at DGFT ("Adobe Scan ...") and on an OEA core-industries archive page need OCR, so verbatim number extraction is at risk there and the raw link must be sent flagged per SPEC.
17. **NSE circulars API data bug:** some rows carry `circFilelink: .../null{n}.null`. Fall back to `{DEPT}{num}.pdf` or skip.
18. **The SEBI RSS is stale and not usable for Tier 1.** It had none of the 22 items dated 1 Oct, including a press release, by 17:30, and its LM stayed at 14:00. It is date-only and dominated by enforcement and recovery orders. Use the server-rendered listing pages (see recommendations).
19. **The gstcouncil.gov.in RSS is the staff directory.** The site's latest meeting is the 56th (Sep 2025). **No newer GST Council meeting was found on the council site.** If one has happened since, it is only on PIB.
20. **YouTube RSS is cached for 15 min** (`max-age=900`) and holds 15 items, mostly shorts. It is fine for "presser video is up" but not for real-time.
21. **IMD's press-release page is 4 MB** per fetch. Use conditional requests sparingly or poll only on LRF days.
22. **Hindi-only items:** PIB regional and Hindi duplicates share the PRID space. IMD posts separate Hindi rows (e.g. "प्रेस विज्ञप्ति LRF 2026" next to the English one), and the MoSPI API returns Hindi unless `lang=en`.
23. **Not verified this session:**
    - per-ministry PIB RSS (none exists on ViewRss.aspx)
    - PIB echo for DGFT, CBDT, CBIC, IMD LRF and MoSPI CPI/IIP/GDP
    - historical MPC release-to-RSS lag (Wayback down)
    - a second SEBI board-outcome example (pagination is POST)
    - a second CGA PIB example
    - the structured MoSPI calendar API (GET 403)
    - a CBDT JSON API via plain HTTP
    - DGFT notification on eGazette (postback search not used)
