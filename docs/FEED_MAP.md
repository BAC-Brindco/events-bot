# Feed map

One row per source in `docs/SPEC.md`. Every source was probed live on 2026-10-01 from this machine (Indian IP). Per-endpoint detail is in the four group files: [`phase0/us_policy.md`](phase0/us_policy.md), [`phase0/us_data.md`](phase0/us_data.md), [`phase0/india_t1.md`](phase0/india_t1.md) and [`phase0/india_t23.md`](phase0/india_t23.md). Each of those has the probe script, the raw samples and the timestamps behind every number below.

How to read the columns:

- **verified**
  - `yes`: fetched this session and the content was sensible.
  - `partial`: reachable only with a workaround or the content is incomplete.
  - `key`: the endpoint responds, but a registered API key is pending.
  - `no`: blocked or no feed exists.
- **feed type**
  - `rss`, `api`, `ics`, `pdf`: official feeds and files.
  - `json*`: an undocumented JSON backend behind the agency's own site. It is official data but not a published API, so it needs a health check.
  - `html`: listing-page parsing. This needs scraper approval (see `PROPOSAL.md` §d).
- **lag**: from the official release time to when the content was fetchable. "n/m" means it can't be measured after the fact and will be measured in the live dry run.
- **Times**: ET for US sources, IST for India.

## India

| source | country | tier | kind | feed type | URL | verified | lag | notes |
|---|---|---|---|---|---|---|---|---|
| PIB (Cabinet/CCEA + all GoI releases) | IN | 1 | stream | rss + html | `https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3&reg=3` + `allRel.aspx?reg=3&lang=1` + `PressReleasePage.aspx?PRID={id}&reg=3&lang=1` | yes | RSS median 5.7 min after "Posted On" (n=30, range 2–9; 1 outlier 59 min) | The advertised URL without `&reg=3` redirects to the **Hindi** feed. The RSS holds 20 items with no pubDate and no ministry tag. The ministry tag comes from `allRel.aspx` (grouped by ministry) or `#MinistryName` on the release page. CCEA ministry = "Cabinet Committee on Economic Affairs (CCEA)". |
| RBI MPC (schedule, resolution, Governor's statement, SDRP, minutes) | IN | 1 | scheduled | html + rss | `https://www.rbi.org.in/scripts/Annualpolicy.aspx`; `https://rbi.org.in/pressreleases_rss.xml`; `BS_PressReleaseDisplay.aspx?prid={n}` | yes | RSS ≤2 min after pubDate (live). PDFs +25 min (Jun) and +80 min (Aug) after 10:00. HTML page time n/m | FY27 schedule verified; **next decision 7 Oct 2026**. Full text is in the HTML press release, so that is the Stage 1 source, not the PDF. The RSS pubDate is naive IST. |
| RBI circulars, master directions, draft regulations | IN | 1 | stream | rss | `https://rbi.org.in/notifications_rss.xml`; drafts via `pressreleases_rss.xml` | yes | ≤2 min (n=4) | Each feed holds 10 items (about 1 day for press releases), so the backstop is to backfill by sequential `prid`. |
| RBI Bulletin | IN | 1 | scheduled | rss | announced in `pressreleases_rss.xml`; issues at `BS_ViewBulletin.aspx` | yes | ≤2 min | |
| RBI YouTube (presser video) | IN | 1 | both | rss | `https://www.youtube.com/feeds/videos.xml?channel_id=UCIfCOl43tunZVNYafeC4RQA` | yes | cached up to 15 min | Used for the faster-whisper source. |
| SEBI board outcomes | IN | 1 | both | html | `https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=6&ssid=23&smid=0` | yes | outcome uploaded 19:40 on the meeting day (24 Sep) | **The SEBI RSS is stale** (0 of 22 same-day items by 17:30), so listing pages are the only timely channel. |
| SEBI circulars | IN | 1 | stream | html | `...HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0` | yes | same day; the attachment file name gives the upload time | Same as above. |
| SEBI consultation papers | IN | 1 | stream | html | `...HomeAction.do?doListing=yes&sid=4&ssid=38&smid=35` | yes | same day | |
| GST Council outcomes | IN | 1 | both | html (PIB echo) | PIB, Ministry of Finance, title "Recommendations of the NNth Meeting of the GST Council" | yes | PIB posted the same evening (55th: 20:23, 56th: 22:39). The council's own site is +6 days | The council's RSS lists staff-directory entries and its press page is empty. **PIB is the de facto primary.** |
| MoF/DEA borrowing calendar | IN | 1 | scheduled | rss (RBI echo) | `https://rbi.org.in/pressreleases_rss.xml` (prid 63668/63669) | yes | same day as DEA | dea.gov.in has a broken TLS chain and reuses PDF file names. |
| Union Budget documents | IN | 1 | scheduled | pdf | `https://www.indiabudget.gov.in/doc/Budget_Speech.pdf` (+ `Finance_Bill.pdf`, `memo.pdf`, `doc/cen/`) | yes | pre-staged (LM 06:11 on 1 Feb) | Fixed paths, overwritten each year. A scheduled once-a-year job. |
| CBDT notifications | IN | 1 | stream | — | `https://www.incometaxindia.gov.in/` | partial | n/m | httpx gets 403; the site works only in a browser. PIB echo not seen. |
| CBIC notifications | IN | 1 | stream | — | `https://www.cbic.gov.in/` | partial | n/m | A JavaScript app with an incomplete TLS chain. |
| DGFT notifications, public notices, trade notices | IN | 1 | stream | html | `https://www.dgft.gov.in/CP/?opt=notification` (`public-notice`, `trade-notice`) | yes | the CRT DT column gives upload time to the second; releases often post 17:00–23:00 | **No PIB echo** (RoDTEP 30 Sep was missing from PIB). Some PDFs are scanned, so OCR is needed. |
| MoSPI CPI / IIP / GDP | IN | 1 | scheduled | json* | `POST https://www.mospi.gov.in/api/latest-release/get-web-latest-release-list`; ARC via `/api/documents/get-latest-release-calender` | yes | files uploaded 16 min early to +8 min around 16:00 (6 releases) | The site is now a JavaScript app, so any HTML scraper would break. The JSON endpoint is POST-only. `api.mospi.gov.in` fails TLS. |
| OEA WPI | IN | 1 | scheduled | pdf | `https://eaindustry.nic.in/press_release/press_release_{YYYYMM}.pdf` | yes | pre-staged 18–33 min before 12:00 | Predictable URL with a strong ETag. |
| OEA Eight Core Industries | IN | 1 | scheduled | pdf | `https://eaindustry.nic.in/eight_core_infra/Press_Release_ICI_{YYYYMMDD}.pdf` | yes | +2 / +6 min after 17:00 | Name depends on the release date, so discover it from the homepage. |
| CGA monthly accounts | IN | 1 | scheduled | html (PIB echo) | PIB PRID 2317034 (Ministry of Finance) | yes | same day (30 Sep 16:41) | Attachments on cga.nic.in sit behind postbacks. |
| IMD monsoon forecasts | IN | 1 | scheduled | html/pdf | `https://internal.imd.gov.in/pages/press_release_mausam.php` | yes | **IMD's own page was +44 h** for the April forecast | Twice a year. A 4 MB page. PIB (MoES) echo not verified. |
| NSE circulars | IN | 1 | stream | json* | `https://www.nseindia.com/api/circulars` | yes | date only; PDF LM e.g. 15:00 | Cookie bootstrap with plain httpx. Some rows carry bad `null` file links. |
| NSE ASM / GSM additions | IN | 1 | stream | json* | `https://www.nseindia.com/api/reportASM`, `/api/reportGSM` | yes | daily snapshot (GSM 08:07) | Same endpoints as in-house `nse-surveillance-pipeline`. Additions are found by diffing snapshots. |
| NSE index changes | IN | 1 | both | html | `https://www.niftyindices.com/press-release` → `Press_Release/ind_prs{DDMMYYYY}.pdf` | yes | after hours (LM 21:00, 22:15) | No feed. |
| BSE notices | IN | 1 | stream | json* | `https://api.bseindia.com/BseIndiaAPI/api/getCurrPreNextNoticesData_New/w?flag=` | partial | date only | Akamai returns 403 to httpx; it works with `curl_cffi` (not in deps, as used in-house). PDFs work with httpx. |
| TRAI | IN | 2 | both | rss | `https://www.trai.gov.in/rss.xml` | yes | ~0 at source | 10 items (~3 days), so poll every 1–2 h. PIB (Communications) echoes at −11 to +66 min. |
| IRDAI | IN | 2 | stream | html | `https://irdai.gov.in/press-releases`, `/circulars`, `/monthly-business-figures1` | yes | same day | An RSS link exists in the page template but is commented out. Not on PIB. |
| PFRDA | IN | 2 | stream | html | `https://www.pfrda.org.in/regulatory-framework/circulars/active-circulars` | yes | same day | Low cadence and low equity value. |
| NPCI | IN | 2 | scheduled | json* | `https://www.npci.org.in/api/product-statistic/tab/detail?product_name=upi&...` | no (httpx 403) | UPI monthly stats posted on the 1st at ~17:04 | Reachable only in headless Chrome. |
| CERC | IN | 2 | stream | html | `https://cercind.gov.in/recent_orders.html`, `/Draft_reg.html` | yes | median 0 d | ETag/LM supported. The first TLS handshake is flaky; a retry works. The press page is dead (2019). |
| Ministry of Power | IN | 2 | stream | json* | `https://www.powermin.gov.in/cms/wp-json/post-page/whats_new` | yes | ~0 | Not on PIB, and the reverse also holds, so both channels are needed. |
| PNGRB | IN | 2 | stream | json* | `https://www.pngrb.gov.in/eng-web/whatsnew/whats-new-json.js` | yes | ~0 | JS with `//` comments; ETag/LM supported. |
| PPAC | IN | 2 | both | html | `https://ppac.gov.in/` | yes | ~0 (the epoch in the file name is the upload time) | Gas price and ICB ratio. Not on PIB. |
| DoT | IN | 2 | stream | json* + PIB | `https://www.dot.gov.in/cms/wp-json/post-page/documents?sort=acf&limit=10&page=1` | yes | ~0 | ~95% admin orders, response time 1–46 s. Policy goes through PIB (Communications). |
| MeitY | IN | 2 | stream | PIB (+ json*) | PIB ministry id 1323; `https://www.meity.gov.in/cms/wp-json/post-page/documents?...` | yes | ~0 | Its own JSON is sparse. |
| CDSCO | IN | 2 | both | json* + html | `https://cdscoonline.gov.in/CDSCO/publicNsqDrugTable`; `.../Notifications/Public-Notices/` | yes | NSQ for month M posted in M+1 | Lists drugs that failed quality tests, with manufacturer names, so they can be matched to listed pharma. |
| SIAM | IN | 2 | scheduled | html | `https://www.siam.in/news-&-updates/press-releases` | yes | ~15th of the month | The free release carries headline numbers. |
| FADA | IN | 2 | scheduled | pdf | `https://www.fada.in/press-release-list.php` | yes | ~7th of the month | The free PDF carries headline numbers. |
| NITI Aayog | IN | 3 | stream | rss | `https://www.niti.gov.in/rss.xml` | yes | PIB is 2.7–24 h **ahead** of NITI's own site | |
| PRS Legislative (bill tracking) | IN | 3 | stream | html | `https://prsindia.org/billtrack/{slug}` | yes | n/a | No feed. Per-bill pages have a clean stage timeline. PRS is a private think tank (secondary source). |
| Election Commission | IN | 3 | stream | json* + PIB | `https://www.eci.gov.in/eci-backend/public/api/get-event?...` | partial | PIB ~+99 min (probable match) | Query parameters are scrambled tokens that may change. The PDFs could not be fetched with httpx. |
| Finance Commission | IN | 3 | scheduled | — | `https://fincomindia.nic.in/commission-reports-sixteenth` | yes (static) | n/a | 16th FC report already tabled. The notifications page shows unrelated content. |

## US

| source | country | tier | kind | feed type | URL | verified | lag | notes |
|---|---|---|---|---|---|---|---|---|
| Fed FOMC calendar | US | 1 | scheduled | html + json* | `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm`; `https://www.federalreserve.gov/json/calendar.json` | yes | n/a | **No ICS exists.** calendar.json is undocumented. It covers FOMC, minutes, Beige Book, governor speeches and times. |
| Fed FOMC statement + implementation note | US | 1 | scheduled | rss + html/pdf | `https://www.federalreserve.gov/feeds/press_monetary.xml`; `.../pressreleases/monetary{YYYYMMDD}a.htm` / `a1.htm` | yes | +15 to +33 s after 14:00 (3 meetings) | The RSS pubDate is the scheduled time, so the release time uses first-seen. |
| Fed SEP / dot plot | US | 1 | scheduled | html/pdf | `https://www.federalreserve.gov/monetarypolicy/fomcprojtabl{YYYYMMDD}.htm` | yes | +14 to +27 s (2 meetings) | |
| Fed FOMC minutes | US | 1 | scheduled | html/pdf | `https://www.federalreserve.gov/monetarypolicy/fomcminutes{meetingdate}.htm` | yes | +11 to +81 s | Next on 7 Oct, for the Sep meeting. |
| Fed press conference (transcript + video) | US | 1 | scheduled | pdf + video | `https://www.federalreserve.gov/mediacenter/files/FOMCpresconf{YYYYMMDD}.pdf`; video link on `fomcpresconf{date}.htm` | yes | **transcript 8–13 days** (Last-Modified, 4 meetings) | **Changes the Stage 2 design.** See FLAGS F-03. |
| Fed governor speeches | US | 1 | stream | rss + json* | `https://www.federalreserve.gov/feeds/speeches_and_testimony.xml`; calendar.json | yes | feed +12 s | Board members only. |
| Fed regional presidents' speeches | US | 1 | stream | rss / html | Richmond `https://www.richmondfed.org/press_room/speeches?cc_view=rss`; Dallas `https://www.dallasfed.org/rss/speeches.xml`; NY `https://www.newyorkfed.org/newsevents/speeches` (html) | partial | n/m | Only Richmond and Dallas feeds are current. Boston and Atlanta are stale, Chicago is broken, SF returns 404, and the rest have no feed. |
| Fed Beige Book | US | 1 | scheduled | pdf | `https://www.federalreserve.gov/monetarypolicy/files/BeigeBook_{YYYYMMDD}.pdf` | yes | +10 to +14 s (2 releases) | Not in any RSS. Dates come from calendar.json (next 14 Oct). |
| BLS calendar | US | 1 | scheduled | ics | `https://www.bls.gov/schedule/news_release/bls.ics` | yes | n/a | Akamai lets only the full Chrome header set through. Calendar runs to 2026-12-30. |
| BLS NFP / CPI / PPI / JOLTS | US | 1 | scheduled | api + html | `https://api.bls.gov/publicAPI/v2/timeseries/data/`; `https://www.bls.gov/news.release/{empsit,cpi,ppi,jolts}.nr0.htm` | key (API) / yes (HTML) | n/m (BLS stamps files before release) | Without a key the API allows 25 queries/day, which is not enough for polling. Never use RSS pubDate or PDF Last-Modified as the release time. Live test **2 Oct, 18:00 IST**. |
| BEA calendar | US | 1 | scheduled | api + ics | `https://apps.bea.gov/API/signup/release_dates.json`; `https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics` | yes | n/a | |
| BEA GDP / PCE | US | 1 | scheduled | api + rss | `https://apps.bea.gov/api/data` (key); `https://apps.bea.gov/rss/rss.xml` | key / yes | RSS ≤1.8 min | Without a key the API returns HTTP 200 with an error in the body. |
| DOL weekly claims | US | 1 | scheduled | pdf | `https://www.dol.gov/newsroom/releases/eta/eta{YYYYMMDD}`; `https://www.dol.gov/ui/data.pdf` | yes | ≤17 s / ≤34 s | Predictable dated URL. |
| Treasury auction results | US | 1 | stream | api + xml | `https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=7` → `https://www.treasurydirect.gov/xml/R_{YYYYMMDD}_{n}.xml` | yes | XML +2m22s to +2m39s; JSON +3–4 min (3–4 auctions) | Plus Fiscal Data `auctions_query` for history back to 1979. **There is no primary when-issued (WI) source, so tail-vs-WI is omitted.** |
| Treasury quarterly refunding | US | 1 | scheduled | rss + html | `https://public.govdelivery.com/topics/USTREAS_49/feed.rss`; `https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding/most-recent-quarterly-refunding-documents` | yes | n/m | No machine-readable refunding date, so the date is entered by hand each quarter. |
| Census calendar | US | 1 | scheduled | html | `https://www.census.gov/economic-indicators/calendar-listview.html` | yes | n/a | No ICS or JSON exists. Anchors are machine-readable (`AYYYYMMDDHHMM`). |
| Census retail sales / durable goods | US | 1 | scheduled | api + rss + pdf | `https://api.census.gov/data/timeseries/eits/{marts,advm3}` (key); `https://www.census.gov/economic-indicators/indicator.xml`; `.../m3/adv/pdf/durgd.pdf` | key / yes | durables PDF ≤8 s | Without a key the API returns HTTP 200 and redirects to `missing_key.html`. |
| White House executive orders + tariff proclamations | US | 1 | stream | rss | `https://www.whitehouse.gov/presidential-actions/feed/` (+ `/executive-orders/feed/`, `/proclamations/feed/`) | yes | posted the evening of signing | Tariff actions arrive as proclamations. |
| Federal Register (EO numbers, BIS) | US | 1 | stream | api | `https://www.federalregister.gov/api/v1/documents.json?...`; `/public-inspection-documents/current.json` | yes | EOs are published **4–5 days after signing**; public inspection is 1–2 days earlier | The canonical text, not the fast channel. |
| USTR press releases | US | 1 | stream | html | `https://ustr.gov/about-us/policy-offices/press-office/press-releases` | yes | n/m | No RSS. ETag works. |
| Congress (bills, laws, committee meetings) | US | 1 | stream | api | `https://api.congress.gov/v3/bill`, `/law/119`, `/committee-meeting/119` | key (DEMO_KEY ok) | n/a | DEMO_KEY allows 10 requests/hour. The congress.gov web pages are behind a Cloudflare 403. |
| Debt ceiling / shutdown deadlines | US | 1 | scheduled | api + html | OPM `https://www.opm.gov/json/operatingstatus.json`; Treasury debt-limit page; Fiscal Data `debt_to_penny` | yes | n/a | No feed of deadlines exists. CR expiry dates are entered by hand. |
| FDA approvals | US | 1 | stream | rss + api | `https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml`; `https://api.fda.gov/drug/drugsfda.json` | yes | press feed same day; openFDA refreshed weekly | No same-day feed for generic (ANDA) approvals. |
| FDA warning letters + Indian facilities | US | 1 | stream | html/xlsx + api | `.../warning-letters` (+ `datatables-data?_format=xlsx`); `https://api.fda.gov/drug/enforcement.json?search=country:"India"` | yes | **weeks to months** from issue to posting | The xlsx export has no country column; the letter pages carry the address. openFDA has a `country` field (281 India records). |
| ISM PMIs | US | 2 | scheduled | — | `https://www.ismworld.org/.../ism-report-on-business/` | no | n/a | reCAPTCHA wall. |
| UMich sentiment | US | 2 | scheduled | html + csv | `https://www.sca.isr.umich.edu/`; `/files/tbmics.csv` | yes | n/m | The free headline is on the page. |
| Conference Board sentiment | US | 2 | scheduled | html | `https://www.conference-board.org/topics/consumer-confidence` | yes | n/m | The free headline is on the page. |
| ADP | US | 2 | scheduled | html (JS) | `https://adpemploymentreport.com/` | partial | n/m | The headline renders only in a browser. |
| Housing: starts/permits | US | 2 | scheduled | rss + pdf | `indicator.xml`; `https://www.census.gov/construction/nrc/pdf/newresconst.pdf` | yes | n/m | |
| Housing: Case-Shiller | US | 2 | scheduled | — | `https://www.spglobal.com/spdji/en/index-family/indicators/sp-corelogic-case-shiller/` | no | n/a | Returns 403 even in headless Chrome. The FRED mirror (`CSUSHPISA`) works. |
| Housing: NAHB | US | 2 | scheduled | html | `https://www.nahb.org/news-and-economics/housing-economics/indices/housing-market-index` | yes | n/m | The free headline is on the page. Release dates are published. |
| SEC rulemaking + enforcement | US | 2 | stream | rss + api | `https://www.sec.gov/news/pressreleases.rss`; `/enforcement-litigation/litigation-releases/rss`; `/administrative-proceedings/rss`; Federal Register (SEC) | yes | n/m | Legacy rules RSS is dead (2023) and some feeds return 404. Requests must declare a user agent. |
| Commerce/BIS export controls | US | 2 | stream | api | `https://www.federalregister.gov/api/v1/documents.json?conditions[agencies][]=industry-and-security-bureau` | yes | public inspection is 2 days before publication | Filter for `type=Rule` (most documents are denial orders). |
| EIA inventories | US | 2 | scheduled | api + files | `https://api.eia.gov/v2/petroleum/stoc/wstk/data/` (key); `https://ir.eia.gov/wpsr/table1.csv`, `wpsrsummary.pdf` | key / yes | files staged 09:35 for the 10:30 release | Holiday shifts are on `schedule.php`. The TWIP RSS is stale. |
| CBO projections | US | 3 | stream | — | `https://www.cbo.gov/` | no | n/a | 403 DataDome CAPTCHA, even in headless Chrome. |
| Treasury TIC flows | US | 3 | scheduled | html + zip | `https://home.treasury.gov/data/treasury-international-capital-tic-system/release-dates-of-tic-data`; `https://ticdata.treasury.gov/.../slt_table5.html` | yes | +2 min after 16:00 | Release dates are published through 2027. |
