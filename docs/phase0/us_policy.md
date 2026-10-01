# Phase 0 feed verification: US policy & fiscal

Probed live from this machine on 2026-10-01, between about 09:55 and 10:25 UTC. The probe script is `docs/phase0/probes/us_policy_probe.py`. Raw samples (first ~50 KB of each response) and per-stage `_results_*.json` logs (status, headers, conditional-GET outcome) are in `docs/phase0/raw/us_policy/`.
Every request used a desktop Chrome User-Agent and waited at least 1.1 s between hits to the same host. **Cond. GET** means the probe replayed the request with the returned `ETag` and/or `Last-Modified` and got a `304` back.
All lags are relative to the official release time in ET. "LM" means the `Last-Modified` response header.

| source | country | tier | kind | feed type | URL | verified | lag | notes |
|---|---|---|---|---|---|---|---|---|
| Fed – monetary policy press releases | US | 1 | stream | rss | https://www.federalreserve.gov/feeds/press_monetary.xml | yes (200 text/xml) | feed LM +17 s after 14:00 ET (16 Sep). Item pubDate is the *scheduled* time (exactly 18:00:00Z), not when it was actually posted | ETag+LM, cond. GET 304. 15 items, back to Apr 2026. Carries statement, SEP release, FOMC minutes and discount-rate minutes. **No Beige Book.** No auth. Cloudflare front end. |
| Fed – all press releases | US | 1 | stream | rss | https://www.federalreserve.gov/feeds/press_all.xml | yes | same mechanism (feed LM +19 s after the 30 Sep 09:00 ET item) | ETag+LM, 304. 20 items (~7 weeks). Includes bcreg/enforcement/orders noise, so filter by URL prefix `monetary`. |
| Fed – speeches | US | 1 | stream | rss | https://www.federalreserve.gov/feeds/speeches.xml | yes | pubDate = scheduled start time on the Board calendar (5 of 5 matched). Feed LM +12 s (Cook 30 Sep 15:25 ET) | ETag+LM, 304. 15 items (~2.5 months). Board members only. Text-less "Discussion" events are not included. |
| Fed – testimony | US | 1 | stream | rss | https://www.federalreserve.gov/feeds/testimony.xml | yes | n/a (latest is 14 Jul 2026) | ETag+LM, 304. 15 items, and one has a bogus 1899 date, so parse defensively. |
| Fed – speeches & testimony | US | 1 | stream | rss | https://www.federalreserve.gov/feeds/speeches_and_testimony.xml | yes | as speeches | Superset of the two feeds above. Use this one instead of both. |
| Fed – per-governor feeds | US | 1 | stream | rss | https://www.federalreserve.gov/feeds/s_t_powell.xml (+ jefferson, m_w_Bowman, barr, cook, waller) | no: listed on feeds.htm, not fetched | – | **No Warsh or Miran feed is listed** (Warsh is Chair per testimony.xml). Not needed if speeches_and_testimony.xml is used. |
| Fed – FOMC meeting calendar | US | 1 | scheduled | html | https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm | yes | page LM 16 Sep 19:37Z | LM only (no ETag), If-Modified-Since → 304. Lists 2026 (Oct 27-28, Dec 8-9*) and all 2027 dates, marks SEP meetings with *, and links every document per meeting. Dates are "tentative until confirmed at the preceding meeting". |
| Fed – events calendar JSON (backs newsevents/calendar.htm) | US | 1 | both | api | https://www.federalreserve.gov/json/calendar.json | yes (200 application/json, UTF-8 BOM) | updated weekly: LM Fri 25 Sep 18:30Z. Speeches appear only about 1 week ahead | ETag+LM, 304. 2,596 events typed FOMC / Beige / Speeches / Testimony / Stat / Other (holidays). Includes the times ("2:00 p.m."), presser 2:30 p.m., minutes dates, Beige Book (14 Oct, 25 Nov), and `live` stream links. **Undocumented** (found in js/cms/calendar.js). Governors only, **no regional presidents**. Archive covers 2017–2022 and 2025–2026; **2023–2024 are missing**. |
| Fed – ICS/iCal for FOMC | US | 1 | scheduled | ics | – | no: none exists | – | No .ics link on fomccalendars.htm, calendar.htm, feeds.htm or speeches.htm. calendar.json is the machine-readable substitute. |
| Fed – FOMC statement (HTML/PDF) | US | 1 | scheduled | html/pdf | https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm | yes | HTML LM +15–21 s, PDF +24 s (3 meetings) | Deterministic URL. HTML has no ETag; PDF has an ETag. |
| Fed – implementation note | US | 1 | scheduled | html | https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a1.htm | yes | +15–33 s | Not a separate RSS item. It is linked from the statement page. |
| Fed – SEP / projection tables | US | 1 | scheduled | html/pdf | https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20260916.htm (+ files/…pdf) | yes | HTML +18–27 s, PDF +14–17 s (Sep and Jun) | Separate press release `monetary{date}b.htm` (+17 s), which does appear in RSS. |
| Fed – FOMC minutes | US | 1 | scheduled | html/pdf | https://www.federalreserve.gov/monetarypolicy/fomcminutes20260729.htm (+ files/…pdf) | yes | PDF +11 s, HTML +13 s to +81 s | Keyed by the *meeting* date, released ~3 weeks later at 14:00 ET (calendar.json gives the date: 7 Oct for the Sep meeting). Sep-meeting minutes still 404 today, as expected. |
| Fed – press-conference transcript PDF | US | 1 | scheduled | pdf | https://www.federalreserve.gov/mediacenter/files/FOMCpresconf20260916.pdf | yes | **8–13 days** (LM: Sep→24 Sep, Jul→10 Aug, Jun→25 Jun, Apr→12 May) | **Big problem for Stage 2.** The Sep presser page (LM 17 Sep) still does not link the PDF that exists at the direct URL, so poll the URL pattern. The LM is an upper bound on first posting. Re-check live on 28 Oct. |
| Fed – presser page | US | 1 | scheduled | html | https://www.federalreserve.gov/monetarypolicy/fomcpresconf20260916.htm | yes | updated days later | Holds YouTube links. The same-day video is the only same-day source for Q&A. |
| Fed – Beige Book | US | 1 | scheduled | pdf/html | https://www.federalreserve.gov/monetarypolicy/files/BeigeBook_20260902.pdf | yes | +10–14 s (2 releases) | **Not in any Fed RSS.** The date comes from calendar.json. The PDF is named by release date; the HTML slug is not (see URL patterns). |
| Richmond Fed – speeches | US | 1 | stream | rss | https://www.richmondfed.org/press_room/speeches?cc_view=rss | yes | not measured | Current (22 Sep 2026). 297 items back to 1993. No validators. pubDate is labelled "EST" even in summer. |
| Dallas Fed – speeches | US | 1 | stream | rss | https://www.dallasfed.org/rss/speeches.xml | yes | not measured | Latest 28 Aug 2026. 100 items. No validators. "CST" labels. |
| Boston Fed – speeches | US | 1 | stream | rss | https://www.bostonfed.org/feeds/rss_speeches.xml | yes, but **stale** | – | Newest item is 15 Nov 2024. Items have no titles. Do not rely on it. |
| Atlanta Fed – speeches | US | 1 | stream | rss | https://www.atlantafed.org/rss/speechindex | yes, but stale | – | Newest is 20 Feb 2026. Links point to YouTube or third parties. LM supported (304). |
| Chicago Fed – "Speeches" | US | 1 | stream | rss | https://www.chicagofed.org/forms/rss/Speeches | yes (200), but **broken** | – | Contains CFNAI releases and ends Apr 2023. Found via Playwright on /rss, which times out for httpx. |
| SF Fed – speeches | US | 1 | stream | rss | https://www.frbsf.org/news-and-media/speeches/?feed=rss2 | no (404) | – | Advertised on the page but returns 404. The site-wide https://www.frbsf.org/feed/ works (200, ETag) but is blog-only and stale (Jun 2026). |
| NY Fed – speeches | US | 1 | stream | html | https://www.newyorkfed.org/newsevents/speeches | yes (200, server-rendered, 638 speech links) | – | **No RSS.** /rss renders /errors/500 in Chrome. No validators (no-store). Akamai bot manager present. |
| Cleveland / Philadelphia / St. Louis / Kansas City / Minneapolis Fed | US | 1 | stream | html | e.g. https://www.clevelandfed.org/collections/speeches | partial: pages reachable, **no feed found** | – | St. Louis and KC time out for httpx (Playwright 200). Cleveland, Philadelphia and Minneapolis /rss return 404. |
| TreasuryDirect – auctioned (results) JSON | US | 1 | stream | api | https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=7 | yes | `updatedTimestamp` +3m18s to +4m18s after close (4 auctions) | No key, no validators. Gives xml/pdf result filenames. `format=xml` returns **406**. |
| TreasuryDirect – announced JSON | US | 1 | both | api | https://www.treasurydirect.gov/TA_WS/securities/announced?format=json&days=7 | yes | announcement ~11:02 ET (29 Sep) | Rows are re-emitted when results arrive. |
| TreasuryDirect – upcoming JSON | US | 1 | scheduled | api | https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json | yes | – | 8 rows through 8 Oct, including not-yet-announced auctions (CUSIP known, amount blank). |
| TreasuryDirect – announced/auctioned RSS | US | 1 | stream | rss | https://www.treasurydirect.gov/TA_WS/securities/auctioned/rss | yes | pubDate = updatedTimestamp (+4 min) | No validators. Every item links to a generic page. The "announced" RSS also re-emits on results. Use the JSON instead. |
| TreasuryDirect – result XML/PDF | US | 1 | stream | api/pdf | https://www.treasurydirect.gov/xml/R_20260930_1.xml | yes | **LM +2m22s to +2m39s** after close (3 auctions); fastest Treasury channel | ETag+LM. Filename comes from the JSON (`xmlFilenameCompetitiveResults`). PDF under /instit/annceresult/press/preanre/{yyyy}/. |
| Fiscal Data – auctions_query | US | 1 | stream | api | https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query?sort=-auction_date | yes | intraday lag not measured; 30 Sep results present on 1 Oct | No key, no validators, no documented rate limit. 11,134 rows back to 1979. Values are strings, and missing values are the string `'null'`. |
| Fiscal Data – upcoming_auctions | US | 1 | scheduled | api | …/v1/accounting/od/upcoming_auctions?sort=-record_date,auction_date | yes | – | **Default sort returns 2024 rows**, so always sort. |
| Fiscal Data – record_setting_auction | US | 1 | scheduled | api | …/v2/accounting/od/record_setting_auction | yes | quarterly (record_date 2026-09-30) | Reference data only. |
| Fiscal Data – debt_to_penny | US | 1 | stream | api | …/v2/accounting/od/debt_to_penny?sort=-record_date | yes | T+1 business day (29 Sep row on 1 Oct) | Headroom context for debt-limit events. |
| Treasury – press releases (GovDelivery RSS) | US | 1 | stream | rss | https://public.govdelivery.com/topics/USTREAS_49/feed.rss | yes (application/rss+xml) | not measured against the site (the site exposes no publish time) | ETag, 304. 25 items (~5 weeks). Links go to content.govdelivery.com bulletins, not home.treasury.gov. **No TIC press release in the window.** Topic ID taken from the subscribe link on the press-releases page. |
| Treasury – press releases page | US | 1 | stream | html | https://home.treasury.gov/news/press-releases | yes | – | No RSS link on the page. ETag+LM 304, but LM differed between fetches (S3/CDN edges). Release pages carry a site-rebuild LM (all 16 Sep 22:57Z), which is useless for lag. Hrefs are unquoted. |
| Treasury – quarterly refunding docs | US | 1 | scheduled | html | https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding/most-recent-quarterly-refunding-documents | yes | – | Links the Aug-2026 refunding (press releases sb0584/85/90/91/92, TBAC PDFs, `TentativeAuctionScheduleQ32026.pdf`, `2026-3rd-Quarter.xls`). ETag+LM, 304. No feed and no machine-readable date for the next refunding. |
| Treasury – debt limit page | US | 1 | stream | html | https://home.treasury.gov/policy-issues/financial-markets-financial-institutions-and-fiscal-service/debt-limit | yes | – | Latest letter: `Debt-Limit-Letter-to-Congress-06-25-2025.pdf`, so no active extraordinary-measures episode. ETag+LM, 304. |
| Federal Register – EOs | US | 1 | stream | api | https://www.federalregister.gov/api/v1/documents.json?conditions[type][]=PRESDOCU&conditions[presidential_document_type]=executive_order&order=newest | yes | **publication is 4–5 days after signing** (EO 14431 signed 18 Sep, PI 22 Sep 11:15 ET, FR 23 Sep) | No key, no validators. 1,567 EOs back to 1994 (EO 12890). Use it for canonical EO number and text, not for speed. |
| Federal Register – BIS documents | US | 2 | stream | api | https://www.federalregister.gov/api/v1/documents.json?conditions[agencies][]=industry-and-security-bureau&order=newest | yes | PI is 2 days ahead (polysilicon TFR: PI 22 Sep 08:45, pub 24 Sep) | 2,987 docs. Mostly denial-order notices, so filter `type=Rule`. |
| Federal Register – public inspection (current) | US | 1/2 | stream | api | https://www.federalregister.gov/api/v1/public-inspection-documents/current.json | yes | regular filings 08:45 ET, special 16:15 ET (also 11:15 / 18:00 seen), 1+ business day before publication | **Blank from 00:00 to 08:45 ET daily** (`meta.pil_unavailability_message`). The agency filter is ignored when combined with `available_on`, so filter client-side. |
| Federal Register – PI by document | US | 1/2 | stream | api | https://www.federalregister.gov/api/v1/public-inspection-documents/2026-19555.json | yes | – | Gives `filed_at`, `filing_type`, `pdf_url`. |
| White House – presidential actions | US | 1 | stream | rss | https://www.whitehouse.gov/presidential-actions/feed/ | yes (application/rss+xml) | EO posted the **evening of signing** (14431: 18 Sep 18:02 ET; 14430: 17 Sep 17:31 ET), 4–5 days before FR | ETag+LM, 304. 30 items (~5 weeks). Full-text bodies (~600 KB), so always send a conditional GET. Includes nominations noise. |
| White House – executive orders | US | 1 | stream | rss | https://www.whitehouse.gov/presidential-actions/executive-orders/feed/ | yes | as above | 30 items back to May 2026. Titles differ from FR (typos such as "Integrity and Integrity", ALL CAPS), so match on date and fuzzy title. |
| White House – proclamations | US | 1 | stream | rss | https://www.whitehouse.gov/presidential-actions/proclamations/feed/ | yes | as above | **This is where tariff actions land** (e.g. "Restriction on Entry…", Section 232/IEEPA proclamations). |
| White House – site feed | US | 1 | stream | rss | https://www.whitehouse.gov/feed/ | no (404) | – | Use the presidential-actions feeds instead. |
| USTR – press releases | US | 1 | stream | html | https://ustr.gov/about-us/policy-offices/press-office/press-releases | yes (200, server-rendered list) | – | **No RSS found** on this page or the home page. ETag works (304). LM equals the render time (changes every fetch). max-age=600. |
| Congress.gov API v3 – bill | US | 1 | stream | api | https://api.congress.gov/v3/bill?api_key=DEMO_KEY&format=json | yes (DEMO_KEY) | – | **DEMO_KEY: `x-ratelimit-limit: 10`** (api.data.gov umbrella). Production needs a real key: **pending key**. cache-control 1800 s. |
| Congress.gov API v3 – committee-meeting | US | 1 | scheduled | api | https://api.congress.gov/v3/committee-meeting/119?api_key=DEMO_KEY&format=json | yes (DEMO_KEY) | – | Gives event IDs and updateDate; per-event detail needs more calls. |
| Congress.gov API v3 – committee / law / house-vote / daily-congressional-record | US | 1 | stream | api | https://api.congress.gov/v3/law/119?api_key=DEMO_KEY&format=json | yes (DEMO_KEY) | – | `law/119` shows the latest public law (119-117, 25 Sep). Usable to detect CR, appropriations and debt-limit enactment. Pending key. |
| congress.gov – CRS appropriations status table | US | 1 | scheduled | html | https://www.congress.gov/crs-appropriations-status-table | **no** (403 Cloudflare challenge, httpx and headless Chrome) | – | Web front end is blocked from this machine. |
| OPM – operating status JSON | US | 1 | stream | api | https://www.opm.gov/json/operatingstatus.json | yes | – | `StatusSummary: "Open"` on 1 Oct 2026 (FY27 day 1). A cheap shutdown confirmation signal. |
| CBO – site and RSS | US | 3 | stream | rss/html | https://www.cbo.gov/ , https://www.cbo.gov/about/rss | **no** (403 DataDome captcha, httpx and headless Chrome) | – | Feed URLs could not be discovered or verified. |
| Treasury TIC – release dates | US | 3 | scheduled | html | https://home.treasury.gov/data/treasury-international-capital-tic-system/release-dates-of-tic-data | yes | – | All releases at 16:00 ET. Dates published through Dec 2027 (next: 16 Oct, 18 Nov, 15 Dec 2026). The `-0` URL is a redirect stub. |
| Treasury TIC – data files | US | 3 | stream | html/zip | https://ticdata.treasury.gov/resource-center/data-chart-center/tic/Documents/slt_table5.html | yes | **LM 16:02:02 ET** on the 16 Sep 16:00 release (cslt.zip 16:02:01) | ETag+LM, 304. www.treasury.gov/… redirects to ticdata. |
| Treasury TIC – mfh.txt | US | 3 | stream | html | https://ticdata.treasury.gov/resource-center/data-chart-center/tic/Documents/mfh.txt | yes, but **stale** | – | Holds Jan-2023 data. Use slt_table5.html for major foreign holders. |

## URL patterns

All examples below were fetched (GET or HEAD = 200) this session unless noted otherwise.

- **FOMC statement**: `/newsevents/pressreleases/monetary{YYYYMMDD}a.htm` and `/monetarypolicy/files/monetary{YYYYMMDD}a1.pdf`. YYYYMMDD is the meeting's final day.
  - https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm
  - https://www.federalreserve.gov/monetarypolicy/files/monetary20260729a1.pdf
- **Implementation note**: `/newsevents/pressreleases/monetary{date}a1.htm`.
  - …/monetary20260916a1.htm
  - …/monetary20260729a1.htm
- **SEP**: `/monetarypolicy/fomcprojtabl{date}.htm` and `/monetarypolicy/files/fomcprojtabl{date}.pdf`.
  - …/fomcprojtabl20260916.htm
  - …/files/fomcprojtabl20260617.pdf
  - The SEP press release is `monetary{date}b.htm`. Only 20260916b was verified.
  - **Anomaly**: the calendar links `fomcprojtable20220316.htm` (with an extra "e").
- **Minutes**: `/monetarypolicy/fomcminutes{meetingdate}.htm` and `/monetarypolicy/files/fomcminutes{meetingdate}.pdf`.
  - …/fomcminutes20260729.htm
  - …/fomcminutes20260617.htm
- **Press-conference page**: `/monetarypolicy/fomcpresconf{date}.htm`.
  - …/fomcpresconf20260916.htm
  - …/fomcpresconf20260729.htm
  - **Anomaly**: the Jan 2026 link is `fomcpressconf20260128.htm` (with an extra "s"). Read links from fomccalendars.htm instead of constructing them blindly.
- **Presser transcript**: `/mediacenter/files/FOMCpresconf{date}.pdf`.
  - …/FOMCpresconf20260916.pdf
  - …/FOMCpresconf20260729.pdf (also 20260617 and 20260429)
- **Beige Book PDF**: `/monetarypolicy/files/BeigeBook_{releaseYYYYMMDD}.pdf`.
  - …/BeigeBook_20260902.pdf
  - …/BeigeBook_20260715.pdf
  - The HTML is `/monetarypolicy/beigebook{YYYYMM}.htm`, but YYYYMM is **not** the release month: `beigebook202608.htm` was released 2 Sep and `beigebook202607.htm` on 15 Jul. Take the slug from beige-book-default.htm.
- **Fed speeches**: `/newsevents/speech/{surname}{YYYYMMDD}a.htm`. The date in the URL may differ from the delivery date: `waller20260928a` was delivered 29 Sep, while `cook20260930a` matches. Take URLs from the RSS; never construct them.
- **TreasuryDirect results**: `https://www.treasurydirect.gov/xml/R_{YYYYMMDD}_{n}.xml` and `/instit/annceresult/press/preanre/{YYYY}/R_{YYYYMMDD}_{n}.pdf`.
  - R_20260930_1.xml
  - R_20260924_3.xml
  - `n` is a per-day sequence, so read it from TA_WS JSON. Announcement files `A_{YYYYMMDD}_{n}.xml` appear in the JSON but were **not fetched**.
- **Federal Register PI**: `https://www.federalregister.gov/api/v1/public-inspection-documents/{document_number}.json`.
  - 2026-19555
  - 2026-19537
- **White House actions**: `https://www.whitehouse.gov/presidential-actions/{YYYY}/{MM}/{slug}/`. Two examples, as listed in the verified feed (the item pages themselves were not fetched):
  - …/2026/09/restoring-american-saltwater-angling-and-recreation/
  - …/2026/09/inaugurating-the-era-of-super-intelligence/
- **Treasury press release**: `https://home.treasury.gov/news/press-releases/{id}`.
  - sb0584
  - sb0590
- **USTR press release**: `/about/policy-offices/press-office/press-releases/{YYYY}/{month}/{slug}`. Seen in the listing but individual pages were **not fetched**. Both `/about/` and `/about-us/` prefixes appear.

## Lag evidence

All times are converted from HTTP `Last-Modified` (GMT) to ET unless a different source is named.

**FOMC, scheduled 14:00:00 ET**

| doc | 16 Sep 2026 | 29 Jul 2026 | 17 Jun 2026 |
|---|---|---|---|
| statement .htm | 14:00:17 | 14:00:15 | 14:00:21 |
| statement .pdf | 14:00:24 | 14:00:24 | – |
| implementation note | 14:00:33 | 14:00:15 | – |
| SEP .htm / .pdf | 14:00:27 / 14:00:14 | (no SEP) | 14:00:18 / 14:00:17 |
| SEP press release (…b.htm) | 14:00:17 | – | – |
| press_monetary.xml feed LM | 14:00:17 (pubDate 14:00:00 = scheduled) | – | – |
| presser transcript PDF | **24 Sep 11:03** | **10 Aug 11:33** | **25 Jun 14:22** |

The April 29 transcript PDF has LM **12 May 17:10**.

**Minutes, scheduled 14:00 ET**

- July meeting, released 19 Aug: PDF 14:00:11, HTML 14:01:21.
- June meeting, released 8 Jul: HTML 14:00:13.

**Beige Book, 14:00 ET**

- 2 Sep: PDF 14:00:10, HTML 14:00:11.
- 15 Jul: PDF 14:00:14, HTML 14:00:14.

**Fed speeches**

RSS pubDate compared with the calendar.json start time:

| speech | calendar.json start (ET) | RSS pubDate (UTC) |
|---|---|---|
| Cook, 30 Sep | 3:25 p.m. | 19:25Z |
| Waller, 29 Sep | 3:00 p.m. | 19:00Z |
| Barr, 29 Sep | 12:40 p.m. | 16:40Z |
| Bowman, 29 Sep | 11:00 a.m. | 15:00Z |
| Cook, 28 Sep | 1:25 p.m. | 17:25Z |

The speeches.xml feed LM was 19:25:12Z for the 30 Sep Cook item, 12 s after its start time.

**Treasury auctions**

| auction | competitive close | results XML LM | results PDF LM | TA_WS updatedTimestamp |
|---|---|---|---|---|
| 17-wk, 30 Sep | 11:30 | 11:32:39 | 11:32:35 | 11:34:18 |
| 6-wk, 29 Sep | 11:30 | 11:32:26 | 11:32:22 | 11:33:32 |
| 7-yr note, 24 Sep | 13:00 | 13:02:28 | 13:02:23 | 13:03:20 |

TA_WS RSS pubDate = updatedTimestamp (30 Sep: 15:34:18Z).

**TIC, scheduled 16:00 ET, 16 Sep 2026**

- slt_table5.html LM 16:02:02.
- cslt.zip LM 16:02:01.
- Only one release could be measured: older file versions are overwritten.

**Executive orders**

| EO | signed | WH RSS pubDate (ET) | FR public inspection `filed_at` | FR published |
|---|---|---|---|---|
| 14431 (H-1B) | 18 Sep | 18 Sep 18:02 | 22 Sep 11:15 | 23 Sep |
| 14430 (Saltwater) | 17 Sep | 17 Sep 17:31 | 21 Sep 11:15 | 22 Sep |

**BIS on the Federal Register**

| document | PI `filed_at` | FR published |
|---|---|---|
| 2026-19537 (polysilicon TFR) | 22 Sep 08:45, special filing | 24 Sep |
| 2026-19927 | 28 Sep 08:45 | 29 Sep |
| 2026-20058 | 29 Sep 08:45 | 30 Sep |

## No reliable feed: recommendations

- **Regional Fed presidents' speeches.** There is no aggregate feed. federalreserve.gov (RSS and calendar.json) covers Board members only.
  - Feeds exist for 6 of 12 banks, and only Richmond and Dallas are current.
  - **Recommendation: (1) an approved monitored scraper of the speech index pages, restricted to NY Fed plus the current-year rotating voters.** Each index needs a health check: "page returned more than N speech links and the newest is under X days old". Use the Richmond and Dallas RSS where alive. Treat the other banks as **(3) manual / drop** for non-voters.
  - Caveats: the NY Fed index is server-rendered but sends no validators (592 KB per poll). St. Louis and KC time out to plain httpx and may need headers or a browser, so they are fragile.
- **FOMC press-conference Q&A (Stage 2 "discussion points").** The official transcript lags 8–13 days.
  - **Recommendation: (2) secondary official channel**: transcribe the Fed's own live stream or YouTube video (the presser page and calendar.json `live` field give the link) within the 2–3 h window.
  - Then poll the deterministic transcript PDF URL daily and send the follow-up the SPEC already allows.
  - This is a SPEC-level decision, because it puts ASR into the Stage 2 path.
- **Beige Book.** It is not in RSS, but the date is in calendar.json and the PDF URL is deterministic (`BeigeBook_{date}.pdf`, live within 15 s).
  - **Recommendation:** a scheduled known-URL poll at 14:00 ET on calendar days. This is not a scraper. Health check: the 404 must turn into a 200 by 14:05.
- **USTR press releases.** No RSS. The index is server-rendered with a working ETag (cheap 304 polling).
  - **Recommendation: (1) a monitored scraper of the index**, plus **(2) secondary channels**: tariff actions usually arrive as White House proclamations or EOs (RSS verified), and USTR Section 301 notices appear in Federal Register PI (`agencies` = trade-representative-office-of-united-states; filter client-side, slug not verified).
- **Treasury press releases (refunding, TIC).** There is no RSS on treasury.gov.
  - **Recommendation: (2) GovDelivery RSS** (verified). Resolve the canonical home.treasury.gov URL from the item body.
  - Refunding dates are not machine-readable. **(3) Manual calendar entry once a quarter** from the refunding press release.
  - TIC press releases were not seen in the GovDelivery feed. For TIC, detect the release via the `Last-Modified` change on slt_table5.html at 16:00 ET on the published dates.
- **CBO.** Blocked by DataDome from this machine, even in headless Chrome.
  - Tier 3 only, so **(3) manual weekly**, or **(2)** pick up CBO cost estimates through the Congress.gov API bill records (needs a key).
  - Re-test from a GitHub/Azure runner before deciding (see the NSE memory: egress can differ).
- **Shutdown and appropriations deadlines.** The congress.gov web page is blocked, and there is no feed of deadlines.
  - **Recommendation:** combine three sources:
    - **(2)** Congress.gov API `law`/`bill` (key needed) to detect a CR or debt-limit enactment.
    - **(2)** OPM operating-status JSON for actual shutdown status.
    - **(3)** Manual entry of CR expiry dates into the forward calendar.
  - Debt limit: Treasury's debt-limit page has had no letters since 25 Jun 2025. Watch it with conditional GET (ETag works); the event is currently dormant.
- **Fed FOMC ICS.** Does not exist. **Use calendar.json** for FOMC, minutes, Beige Book and governor-speech scheduling, and fomccalendars.htm for the long-range meeting list.
  - calendar.json is undocumented, so it needs a schema/health check: `events` is non-empty and contains a future FOMC entry.

## Flags

1. **Congress.gov API needs a real api.data.gov key.** DEMO_KEY is capped at 10 requests/hour (header `x-ratelimit-limit: 10`). Status: **pending key**. No sign-up was done.
2. **CBO (all of cbo.gov) and congress.gov web pages are unverified (403 bot walls).** They need a decision: manual, a runner re-test, or drop.
3. **The presser transcript lag of 8–13 days breaks the SPEC assumption** that the Stage 2 discussion section can use the official transcript. A decision is needed on ASR of the live video. Evidence is Last-Modified only; confirm live on 28 Oct.
4. **Fed calendar.json is undocumented.** It could change without notice. 2023–2024 are missing, and governor speeches only appear about 1 week ahead (weekly Friday refresh).
5. **Fed RSS pubDate equals the scheduled time, not the actual posting time.** Use first-seen time or the feed `Last-Modified` for the "release timestamp" in outputs.
6. **Regional Fed coverage is poor.** Of the feeds tested, Richmond and Dallas are current, Boston and Atlanta are stale, Chicago is broken and SF returns 404. NY, Cleveland, Philadelphia, St. Louis, KC and Minneapolis have no feed.
7. **Federal Register:** the PI list is blank from 00:00 to 08:45 ET (09:30–18:15 IST), and the agency filter is silently ignored with `available_on`. FR publication is 4–5 days late for EOs, so it is **not** a Tier 1 real-time source for EOs. Use the White House RSS for speed and FR for canonical numbering and BIS rules.
8. **TreasuryDirect quirks:** TA_WS `format=xml` returns 406, the RSS links are generic, and the "announced" feed re-emits items on results. For speed, poll the JSON and then fetch the result XML (+2.5 min).
9. **Fiscal Data `upcoming_auctions`** returns 2024 rows unless sorted. The API has no validators, so full refetches are needed.
10. **The Treasury GovDelivery feed links to govdelivery bulletins** rather than home.treasury.gov, and TIC releases were absent. Treasury Drupal `Last-Modified` values are rebuild times and vary between CDN edges, so they are unusable as release timestamps.
11. **Unverified URL patterns:** the USTR item pages, the White House item pages, the TreasuryDirect announcement `A_*.xml` files and the per-governor RSS feeds were seen as links but not fetched.
12. **Timing vs the SPEC's "7 AM IST wrap":**
    - FOMC 14:00 ET is 23:30 IST (EDT) or 00:30 IST (EST), so real time is required, as the SPEC says.
    - Treasury auction results land at 21:00 or 22:30 IST. They fit the wrap unless a refunding auction is market-moving.
    - The quarterly refunding statement is morning ET (18:00–19:00 IST), so it may deserve real-time treatment. Its time was not verified here.
