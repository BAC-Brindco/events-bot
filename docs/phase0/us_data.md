# Phase 0: US data agencies and US Tier 2 feeds

Probed live on 2026-10-01 between about 05:30 and 07:00 ET (15:00 to 16:30 IST) from the dev box (Windows, residential/office egress).
- Probe script: `docs/phase0/probes/us_data_probe.py`. The `--analyze` mode prints the lag and calendar evidence.
- Raw samples (first 50 KB each) and per-probe headers: `docs/phase0/raw/us_data/` (`_results_*.json`).
- Client: httpx over HTTP/1.1 at 1 request per second or less per host.
- User agent: a Chrome UA on every host except sec.gov. sec.gov got the declared UA `RAAS Research Capital events-bot parv.bangar@brindco.com`.
- No keys were registered and no forms were submitted. Playwright (headless Chrome) was used only for reachability checks on ISM, ADP and S&P.

Column notes:
- **Tier** follows SPEC. "T1-2nd" means a secondary or mirror channel for a T1 print.
- **Kind** is scheduled, stream or both.
- **Lag** means time from the official release time to when the content was observable. "≤x" is an upper bound taken from Last-Modified (LM) or pubDate evidence. "n/m" means not measurable after the fact; see "Lag evidence".
- **304** means a conditional GET with If-None-Match or If-Modified-Since returned 304. All times are ET unless marked.

| source | country | tier | kind | feed type | URL | verified | lag | notes |
|---|---|---|---|---|---|---|---|---|
| BLS Public Data API v2 (keyless) | US | T1 | scheduled | api | `POST https://api.bls.gov/publicAPI/v2/timeseries/data/` (also `GET .../data/{series}`) | yes | n/m; measure on 2026-10-02 Empsit | 200 JSON `REQUEST_SUCCEEDED`. All 7 test series returned M08-2026 with `latest:true` (NFP 159,075k, UR 4.1, AHE 37.75, CPI SA 334.131, core 337.765, PPI FD 157.411, JOLTS 7,079k). JOLTS also carried the message "Unable to get Catalog Data" but its data was fine. Keyless use gets v1 limits: **25 queries/day**, 25 series/query, 10 yrs. Registered: 500/day, 50 series, 20 yrs. Both: 50 req/10 s (BLS API FAQ, fetched). `Cache-Control: no-cache`, no ETag/LM. Only a UA header is needed; Akamai does not gate api.bls.gov. **Needs registered key (pending key)**: 25/day cannot support release-window polling. |
| BLS release calendar ICS | US | T1 | scheduled | ics | `https://www.bls.gov/schedule/news_release/bls.ics` | yes | n/a | **403 (Akamai) with the default httpx UA and with a Chrome UA alone. 200 only with the full Chrome header set** (Accept, Accept-Language, Sec-Ch-Ua*, Sec-Fetch-*, Upgrade-Insecure-Requests). ETag+LM present, 304 works. 313 VEVENTs from 2025-01-03 to 2026-12-30, TZID US-Eastern, LM 2026-06-10. SUMMARY gives the release name only, with no reference period. Next: Empsit 10-02 08:30, CPI 10-14 08:30, PPI 10-15 08:30, JOLTS 11-03 10:00. |
| BLS per-release RSS (empsit/cpi/ppi/jolts) | US | T1 | both | rss | `https://www.bls.gov/feed/empsit.rss` (+ `cpi.rss`, `ppi.rss`, `jolts.rss`; index `https://www.bls.gov/feed/`) | yes | n/m (pubDate is pre-stamped) | Feed type application/rss+xml with ETag+LM and working 304. Each feed holds 12 items. Each item has a headline title and lead paragraph, linking to `archives/<rel>_MMDDYYYY.htm`. **The pubDate is about 07:50 ET for 08:30 releases (09:20 for the 10:00 JOLTS release), so it is a staging stamp, not a publish time.** The stamp is 40 minutes before the release and is consistent across 3 months × 4 feeds. Same browser-header requirement as the ICS. |
| BLS "latest numbers" RSS | US | T1 | stream | rss | `https://www.bls.gov/feed/bls_latest.rss` | yes | n/m | A single rolling item, "Major Economic Indicators Latest Numbers", with headline values in HTML. LM 09-30 10:03. Useful only as a change trigger. |
| BLS news release HTML (current) | US | T1 | scheduled | html | `https://www.bls.gov/news.release/{empsit,cpi,ppi,jolts}.nr0.htm` | yes | n/m | No ETag/LM, so changes must be detected by content hash. `<pre>` text carries "embargoed until 8:30 a.m. (ET) Friday, September 4, 2026" (JOLTS: "For release 10:00 a.m."), which gives a parseable official timestamp. This is the canonical verbatim source for Stage 1. Browser headers required. |
| BLS news release PDF (current + archive) | US | T1 | scheduled | pdf | `https://www.bls.gov/news.release/pdf/empsit.pdf`; `https://www.bls.gov/news.release/archives/empsit_09042026.pdf` | yes | n/m (LM pre-staged) | ETag+LM. **LM is about 07:07 ET on 08:30 release days** (Empsit 09-04, CPI 09-11, CPI 08-12, PPI 09-10), so the file is staged before the embargo lifts. **The Empsit PDF of 08-07 has LM 10:15 ET**, which means it was replaced after release. Store a hash at first fetch and treat the HTML as canonical. |
| BLS archive index | US | T1 | scheduled | html | `https://www.bls.gov/bls/news-release/empsit.htm` | yes | n/a | Lists `archives/empsit_MMDDYYYY.htm/.pdf` back to at least 2021 in the 50 KB sample. Future slots (10-02, 11-06, 12-04) sit in an HTML comment; the 10-02 URL returns 404 today. |
| BEA API | US | T1 | scheduled | api | `https://apps.bea.gov/api/data?UserID=…&method=GETDATASETLIST` | partial (endpoint up; **pending key**) | n/m | **HTTP 200** with JSON `APIErrorCode 1 "Invalid Request - Invalid API UserId."` for both an empty and a dummy key. The error must be detected from the body, not the status code. |
| BEA release calendar ICS | US | T1 | scheduled | ics | `https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics` | yes | n/a | text/plain, LM 2026-07-13, 304 works. 119 events from 2025-01-07 to 2026-12-23, in UTC. SUMMARY includes the period (e.g. "GDP (Advance Estimate), 3rd Quarter 2026"). Next: Trade 10-06, GDP adv + PIO 10-29 08:30. |
| BEA release_dates.json | US | T1 | scheduled | api | `https://apps.bea.gov/API/signup/release_dates.json` | yes | n/a | Keyless JSON `{release name: {release_dates:[ISO UTC]}}`, ETag+LM, 304 works. Shows the 2025 shutdown gap: trade goes 2025-09-04, then 2025-11-19, then 2025-12-11. Best machine-readable BEA calendar. |
| BEA schedule HTML | US | T1 | scheduled | html | `https://www.bea.gov/news/schedule` | yes | n/a | ETag+LM (regenerated daily about 04:00 ET), 304 works. Has a "To Be Announced" bucket. Use as a cross-check of the ICS. |
| BEA news release RSS | US | T1 | both | rss | `https://apps.bea.gov/rss/rss.xml` | yes | **≤1.8 min** (1 obs) | 25 items, ETag+LM, 304 works. Item pubDates match the scheduled time (09-30 08:30 GDP, 08:31 PIO). **File LM 09-30 08:31:48**, which bounds lag for that release. Links point to `bea.gov/news/2026/<slug>`. |
| BEA release HTML page | US | T1 | scheduled | html | `https://www.bea.gov/news/2026/personal-income-and-outlays-august-2026` | yes | n/m | LM is the nightly regeneration time (04:00 ET), so it says nothing about publish time. |
| Census EITS time-series API | US | T1 (retail, durables) / T2 (resconst) | scheduled | api | `https://api.census.gov/data/timeseries/eits/{marts,advm3,resconst}` | partial (**pending key**) | n/m | **Data queries without a key redirect to `/data/missing_key.html` with HTTP 200 text/html** ("A valid key must be included with each data API request"). Keyless access no longer works. Metadata (`.../marts/variables.json`) is keyless. Validate both the final URL and the content type. |
| Census Economic Briefing Room RSS | US | T1 / T2 | both | rss | `https://www.census.gov/economic-indicators/indicator.xml` | yes | n/m (pubDate = scheduled time) | 18 items, one per indicator (latest only). Each item has a verbatim headline sentence plus current and prior % change. Item pubDate is exactly the scheduled time (08:30:00 or 10:00:00), so it is a stamp. LM present (no ETag), 304 works. Channel rebuilt about hourly. Covers retail sales, advance durables (M3), new residential construction and trade. |
| Census economic indicator calendar | US | T1 / T2 | scheduled | html | `https://www.census.gov/economic-indicators/calendar-listview.html` | yes | n/a | **No ICS/JSON found.** Static HTML list whose anchor IDs encode date and time (`A202601080830`). The yearly PDF `.../econcards/assets/pdf/censusreleaseglance_2026.pdf` is also verified. Parse once a day and diff. |
| Census release PDFs | US | T1 / T2 | scheduled | pdf | `https://www.census.gov/retail/marts/www/marts_current.pdf`; `https://www.census.gov/manufacturing/m3/adv/pdf/durgd.pdf`; `https://www.census.gov/construction/nrc/pdf/newresconst.pdf` | yes | durgd **≤0.1 min** (LM 09-25 08:30:08). Others n/m | LM only, no ETag. 304 works for durgd and NRC but not marts. marts LM 09-28 and NRC LM 09-24 are later than their release dates (09-16, 09-17), so the files get replaced after release. |
| DOL weekly claims PDF (current) | US | T1 | scheduled | pdf | `https://www.dol.gov/ui/data.pdf` | yes | ≤0 (LM 09-24 **08:30:00**) | ETag+LM, 304 works. Embargo line on page 1: "8:30 A.M. (Eastern) Thursday, September 24, 2026". pdfplumber and pymupdf both extract numbers cleanly from the full file. **The 50 KB truncated sample does not parse.** |
| DOL claims release (dated) | US | T1 | scheduled | pdf | `https://www.dol.gov/newsroom/releases/eta/eta20260924` → `.../files/OPA/newsreleases/ui-claims/20261535.pdf` | yes | **≤0.3 / ≤0.6 min** (LM 08:30:17 on 09-24, 08:30:34 on 09-17) | The dated URL is predictable (`eta` + YYYYMMDD) and redirects to a numbered PDF. Best per-release primary source. |
| DOL news releases RSS | US | T1 | stream | rss | `https://www.dol.gov/rss/releases.xml` | yes | n/m (date-only stamps) | Only 10 items across all DOL agencies. pubDate is always 12:00 UTC, so it is date-only. No ETag/LM. Claims appear as "Unemployment Insurance Weekly Claims Report". Use as a backstop only. |
| DOL OUI claims data | US | T1-2nd | scheduled | html / csv | `https://oui.doleta.gov/unemploy/claims.asp`; `https://oui.doleta.gov/unemploy/csv/ar539.csv` | yes | not timely | claims.asp is an interactive form page. ar539.csv is state-level weekly history (from 1986, LM Wed 15:50). It is not the Thursday national release. Use for history only. |
| EIA API v2 | US | T2 | scheduled | api | `https://api.eia.gov/v2/petroleum/stoc/wstk/data/` | partial (**pending key**) | n/m | No key returns **403** `API_KEY_MISSING`. **`api_key=DEMO_KEY` works** (200; `x-ratelimit-limit: 10`) and returned WCESTUS1 period 2026-09-25, the 09-30 WPSR. DEMO_KEY is fine for tests, not production. |
| EIA WPSR release files | US | T2 | scheduled | pdf / csv | `https://ir.eia.gov/wpsr/wpsrsummary.pdf`; `https://ir.eia.gov/wpsr/table1.csv` | yes | n/m (LM pre-staged 09:35 vs 10:30 release) | ETag+LM, 304 works. Highlights PDF `https://www.eia.gov/petroleum/supply/weekly/pdf/highlights.pdf` is also verified. |
| EIA WPSR page + schedule | US | T2 | scheduled | html | `https://www.eia.gov/petroleum/supply/weekly/`; `.../weekly/schedule.php` | yes | n/a | The schedule page lists holiday shifts only, e.g. Columbus Day week moves to Thu 10-15 12:00, Veterans Day to Thu 11-12 12:00. Default is Wed 10:30 ET. No ICS. |
| EIA RSS (TWIP / press) | US | T2 | stream | rss | `https://www.eia.gov/petroleum/weekly/includes/week_in_petroleum_rss.xml`; `https://www.eia.gov/rss/press_rss.xml` | yes (fetched) | n/a | **The TWIP feed is stale (last item 2025-10-29, LM 2025-10-29) and its dates are malformed (`####`).** The press feed is live but does not cover WPSR. Neither is a WPSR trigger. |
| openFDA drugs@FDA | US | T1 | stream | api | `https://api.fda.gov/drug/drugsfda.json` | yes | days (`meta.last_updated` 2026-09-29) | Keyless: 240/min and 1,000/day per IP (open.fda.gov, fetched). With a key: 120k/day. Refreshed weekly, so not real-time. Nested `submissions[]` makes date-range queries for new approvals imprecise (the test query matched old ORIGs). Use as confirmation or back-fill. |
| openFDA drug enforcement (recalls) | US | T1 | stream | api | `https://api.fda.gov/drug/enforcement.json?search=country:"India"` | yes | days (last_updated 2026-09-23) | Has `recalling_firm`, `city`, `country` and `classification`. 281 India records; latest Inventia Healthcare (Kalyan) 2026-09-23. **Good for Indian facility matching.** |
| FDA warning letters (listing + xlsx) | US | T1 | stream | html | `https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/compliance-actions-and-activities/warning-letters` (+ `.../warning-letters/datatables-data?page&_format=xlsx`) | yes | **weeks to months** (issue → posting) | **No RSS or API.** The xlsx export has 7 columns (Posted, Issue date, Company, Issuing Office, Subject, Response, Closeout), is capped at 1,000 rows, and has **no address or country**. Each letter page has the recipient's full postal address with country (verified for Armenia and US). Posting lags issue, e.g. issued 04-20 and posted 09-29. Posts arrive in batches (seven on 09-29). |
| FDA press releases RSS | US | T1 | stream | rss | `https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml` | yes | n/m | 20 items, ETag+LM, 304 works. Carries novel approvals (e.g. "FDA Approves First Treatment for MCT8 Deficiency") and enforcement items about Indian nationals and firms. |
| FDA "What's New: Drugs" RSS | US | T1 | stream | rss | `https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml` | yes | n/m | 20 items, 304 works. pubDates are batch-stamped about 00:00 ET. Feed index: `https://www.fda.gov/about-fda/contact-fda/subscribe-podcasts-and-news-feeds` (the old `/stay-informed/rss-feeds` returns 404). |
| SEC press releases RSS | US | T2 | stream | rss | `https://www.sec.gov/news/pressreleases.rss` | yes | n/m | Declared UA. 25 items, ETag+LM, 304 works. pubDates carry real times (e.g. 14:30:01). Rule proposals and adoptions show up here ("SEC Proposes Amendments…"). SEC fair access is 10 req/s (published policy; not re-verified). |
| SEC litigation releases RSS | US | T2 | stream | rss | `https://www.sec.gov/enforcement-litigation/litigation-releases/rss` | yes | n/m | 25 items, ETag+LM, 304 works. **The legacy `/rss/litigation/litreleases.xml` returns 404.** |
| SEC administrative proceedings RSS | US | T2 | stream | rss | `https://www.sec.gov/enforcement-litigation/administrative-proceedings/rss` | yes | n/m | 25 items with links to PDFs. A trading-suspensions RSS also exists (`.../trading-suspensions/rss`, verified, low volume). |
| SEC statements RSS | US | T2 | stream | rss | `https://www.sec.gov/news/statements.rss` (also `speeches-statements.rss`) | yes | n/m | Staff and commissioner statements. |
| SEC legacy rules RSS | US | T2 | stream | rss | `https://www.sec.gov/rss/rules/proposed.xml` | yes (fetched), **stale** | n/a | **Last item 2023-07-26.** `/rss/rules/final.xml` returns 404. The index `https://www.sec.gov/about/rss-feeds` lists no rulemaking feed. **Do not use.** |
| Federal Register (SEC documents) | US | T2-2nd | stream | api / rss | `https://www.federalregister.gov/api/v1/documents.json?conditions[agencies][]=securities-and-exchange-commission` (+ `documents.rss`) | yes | days to weeks after SEC vote | Keyless JSON and RSS. 1,807 SEC documents; latest PRORULE 2026-09-21. Use as the official rule-text secondary, not as the alert trigger. |
| ISM PMI reports | US | T2 | scheduled | html | `https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/` | **no** | n/a | **httpx gets a reCAPTCHA v3 interstitial (922 B).** Headless Chrome hits "Oops!" on the first load and renders the "ISM® PMI® Reports" landing page on the second. I did not reach a report page with headline numbers. |
| UMich Surveys of Consumers | US | T2 | scheduled | html / csv | `https://www.sca.isr.umich.edu/`; `https://www.sca.isr.umich.edu/files/tbmics.csv` (+ `tbcics.csv`) | yes | n/m | The home page shows the headline table for free ("Final Results for September 2026: ICS 48.1…") and the next date: "Friday, October 09, 2026 … Preliminary October data at 10am ET". CSV goes back to 1952, ETag+LM, 304 works. LM 09-30 09:58 is a post-release re-upload. **Headline is free, so it can go in the digest.** |
| Conference Board CCI | US | T2 | scheduled | html | `https://www.conference-board.org/topics/consumer-confidence` | yes | n/m | No ETag/LM. The free page carries the latest release text verbatim: "…fell by 6.7 points to 81.9 (1985=100) in September, down from 88.6". "Updated: Tuesday, September 29, 2026". **Headline is free.** Full data is premium. |
| ADP National Employment Report | US | T2 | scheduled | html | `https://adpemploymentreport.com/` | partial (browser only) | n/m | httpx gets an empty React shell. Data loads client-side; the bundle references `sheets.googleapis.com/v4/spreadsheets` and a "Download historical data" link built at runtime. Headless Chrome renders "Private employers added 90,000 jobs in September". **Headline is free but needs JS rendering.** |
| S&P Cotality Case-Shiller | US | T2 | scheduled | html | `https://www.spglobal.com/spdji/en/index-family/indicators/sp-corelogic-case-shiller/` | **no** | n/a | **403 Akamai for httpx and also for headless Chrome** ("Error … Security Controls"). |
| NAHB/Wells Fargo HMI | US | T2 | scheduled | html / xls | `https://www.nahb.org/news-and-economics/housing-economics/indices/housing-market-index` (+ `.../nahb-wells-fargo-housing-market-index-release-dates`) | yes | n/m | No ETag/LM. The page text has the headline for free ("…fell three points to 32 in September") plus monthly xls tables under `/-/media/.../hmi/2026-09/…xls?rev=…` (hashed URLs). Release dates page: Oct 19, Nov 17, Dec 16, 2026. **Headline is free.** |
| FRED graph CSV (mirror) | US | T1-2nd / T2-2nd | scheduled | api (csv) | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=PAYEMS` | yes | **+4 to +67 min** (4 obs) | Keyless CSV, LM present, 304 works. **Mirror, not primary.** Must not be the source URL for Stage 1. The first default-UA attempt timed out after 30 s; the browser profile with a 90 s timeout worked. CSUSHPISA (Case-Shiller) is also served. |
| FRED API | US | 2nd | scheduled | api | `https://api.stlouisfed.org/fred/series?series_id=PAYEMS` | partial (**pending key**) | n/a | 400 JSON "Variable api_key is not set." |

## URL patterns

All examples below were fetched with status 200 in this session unless marked otherwise.

- **BLS current release HTML:** `https://www.bls.gov/news.release/{code}.nr0.htm`
  - `https://www.bls.gov/news.release/empsit.nr0.htm`
  - `https://www.bls.gov/news.release/cpi.nr0.htm`
  - Codes: empsit, cpi, ppi, jolts.
- **BLS archived release:** `https://www.bls.gov/news.release/archives/{code}_{MMDDYYYY}.{htm|pdf}`. MMDDYYYY is the release date.
  - `https://www.bls.gov/news.release/archives/empsit_09042026.htm`
  - `https://www.bls.gov/news.release/archives/empsit_08072026.htm`
  - PDFs: `.../archives/cpi_09112026.pdf` and `.../archives/cpi_08122026.pdf`.
  - The URL for the next release (`empsit_10022026.htm`) currently returns 404.
- **BLS current PDF:** `https://www.bls.gov/news.release/pdf/{code}.pdf`
  - `.../pdf/empsit.pdf`
  - `.../pdf/jolts.pdf`
- **BLS RSS:** `https://www.bls.gov/feed/{code}.rss`
  - `.../feed/empsit.rss`
  - `.../feed/cpi.rss`
  - JOLTS is `jolts.rss`, not `jlt.rss`.
- **BLS API:**
  - `POST https://api.bls.gov/publicAPI/v2/timeseries/data/` with body `{"seriesid":[…],"startyear":"2025","endyear":"2026"}`
  - `GET https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0`
- **BEA release:** `https://www.bea.gov/news/{YYYY}/{slug}`
  - `https://www.bea.gov/news/2026/personal-income-and-outlays-august-2026`
  - `https://www.bea.gov/news/2026/personal-income-and-outlays-july-2026`
  - The slug is the title lower-cased with hyphens. Some slugs carry suffixes such as `-2nd`, so take the URL from RSS or the schedule rather than building it.
- **Census release PDFs** use a fixed "current" URL per indicator, which is overwritten on every release:
  - `https://www.census.gov/manufacturing/m3/adv/pdf/durgd.pdf`
  - `https://www.census.gov/construction/nrc/pdf/newresconst.pdf`
  - `https://www.census.gov/retail/marts/www/marts_current.pdf`
- **DOL claims:** `https://www.dol.gov/newsroom/releases/eta/eta{YYYYMMDD}` redirects to `https://www.dol.gov/sites/dolgov/files/OPA/newsreleases/ui-claims/{number}.pdf`.
  - `eta20260924` → `20261535.pdf`
  - `eta20260917` → `20261529.pdf`
  - Also `https://www.dol.gov/ui/data.pdf`, which always holds the latest release.
- **EIA WPSR:** `https://ir.eia.gov/wpsr/{file}`
  - `wpsrsummary.pdf`
  - `table1.csv`
- **FDA warning letter:** `https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/{company-slug}-{MARCS-CMS no}-{MMDDYYYY issue date}`
  - `…/babikian-healthcare-products-cjsc-730513-09212026`
  - `…/stream2sea-llc-718983-02022026`
- **openFDA:**
  - `https://api.fda.gov/drug/enforcement.json?search=country:"India"&sort=report_date:desc&limit=3`
  - `https://api.fda.gov/drug/drugsfda.json?search=…&limit=3`
- **SEC:**
  - Litigation release: `https://www.sec.gov/enforcement-litigation/litigation-releases/lr-{n}`, e.g. `…/lr-26662`.
  - Press release: `https://www.sec.gov/newsroom/press-releases/{YYYY}-{n}-{slug}`, e.g. `…/2026-96-sec-proposes-amendments-expand-responsible-retailization-private-markets`.
- **FRED:** `https://fred.stlouisfed.org/graph/fredgraph.csv?id={SERIES}`
  - `PAYEMS`
  - `CPIAUCSL`
  - Also verified: `PCEPI`, `ICSA`, `CSUSHPISA`.

## Lag evidence

Last-Modified (LM) is converted to ET. "Official" is the scheduled release time.

| release | official | evidence | observed | reading |
|---|---|---|---|---|
| DOL claims 09-24 | 08:30:00 | `eta20260924` PDF LM | 08:30:17 | ≤17 s |
| DOL claims 09-17 | 08:30:00 | `eta20260917` PDF LM | 08:30:34 | ≤34 s |
| DOL claims 09-24 | 08:30:00 | `ui/data.pdf` LM | 08:30:00 | LM is set to the embargo time, so it is not a true lag measure |
| Census advance durables 09-25 | 08:30:00 | `durgd.pdf` LM | 08:30:08 | ≤8 s |
| BEA GDP 3rd + PIO 09-30 | 08:30:00 | `rss.xml` LM | 08:31:48 | ≤1.8 min to RSS |
| BLS Empsit 09-04 / 08-07 | 08:30 | PDF LM; RSS pubDate | 07:07:02 / 10:15:58; 07:51:08 / 07:50:34 | **Unusable**: pre-staged, or re-uploaded later |
| BLS CPI 09-11 / 08-12 | 08:30 | PDF LM; RSS pubDate | 07:07:19 / 07:08:11; 07:50:40 / 07:51:16 | **Unusable** (pre-staged) |
| BLS JOLTS 09-29 | 10:00 | PDF LM; RSS pubDate | 08:35:36; 09:20:32 | **Unusable** (pre-staged) |
| EIA WPSR 09-30 | 10:30 | summary PDF / table1.csv LM | 09:35:40 / 09:35:38 | **Unusable** (pre-staged) |
| FRED mirror: PAYEMS (Empsit 09-04) | 08:30 | fredgraph.csv LM | 09:27:34 | +57 min |
| FRED mirror: CPIAUCSL (CPI 09-11) | 08:30 | LM | 09:37:49 | +67 min |
| FRED mirror: PCEPI (PIO 09-30) | 08:30 | LM | 08:43:32 | +13 min |
| FRED mirror: ICSA (claims 09-24) | 08:30 | LM | 08:34:21 | +4 min |

**Why BLS lag can't be measured after the fact.** BLS stamps both RSS pubDate and PDF LM 40 to 85 minutes before the embargo lifts. The nr0 HTML and the API return no time headers, and the API does not expose a "first available" timestamp.

**How lag will be measured in the live dry run.** For each release the bot will:
- Poll from T-2 min to T+15 min:
  - every 10 s for the nr0 HTML (by content hash), the RSS feed (ETag) and the dated PDF;
  - every 20 s for the API (needs a registered key: about 50 queries per release fits in 500/day).
- Record the first fetch time at which the new period appears, using a local clock disciplined by NTP.

Upcoming dry-run windows (all verified in the calendars above):

| date | release | ET | IST |
|---|---|---|---|
| today, 10-01 (Thu) | DOL claims | 08:30 | 18:00 |
| today, 10-01 (Thu) | ISM Manufacturing; Census construction spending | 10:00 | 19:30 |
| 10-02 (Fri) | BLS Employment Situation | 08:30 | 18:00 |
| 10-06 | BEA trade | 08:30 | |
| 10-07 | EIA WPSR | 10:30 | |
| 10-09 | UMich preliminary | 10:00 | |
| 10-14 | BLS CPI | 08:30 | |
| 10-15 | BLS PPI | 08:30 | |
| 10-29 | BEA GDP advance + PIO | 08:30 | |

## No reliable feed: recommendations

- **Census calendar** (no ICS/JSON). **(1) Approved monitored scraper.** The list-view HTML has stable machine anchors (`AYYYYMMDDHHMM`), and the yearly PDF is a fallback. Health check: the parse must yield at least 80 events for the year and every T1 indicator must have a next date. This is low fragility.
- **Census indicator data** (API needs a key). Register a key: this is the official data API. Until then, use **(2) the secondary official channel**: the `indicator.xml` RSS headline sentence plus the release PDF for verbatim numbers.
- **FDA warning letters** (no feed or API; xlsx lacks country). **(1) Monitored scraper** of the xlsx export, once a day (letters post on Tuesdays, so this belongs in an EOD digest, not real-time). For new rows with Issuing Office CDER or CDER/OMQ, fetch the letter page and parse the Recipient block for the country. Health check: xlsx header row equals the expected 7 columns and the row count is above 900.
  - Complement this with **openFDA enforcement** (`country:"India"`, keyless) and the FDA press RSS.
  - The FDA Data Dashboard compliance API also exists but needs a key; it was not probed and is not verified.
- **FDA approvals.** Use **(2)**: FDA press RSS for novel approvals, plus openFDA drugsfda (weekly) for ANDA/NDA confirmation. A same-day approval feed for Indian generics does not exist, so I recommend accepting T+1 to 7 days for generic approvals.
- **SEC rulemaking** (legacy rules RSS dead). Use **(2)**: the SEC press RSS as the trigger (proposals and adoptions are announced there with times), and the Federal Register API as the official text and confirmation.
- **EIA WPSR** (no live RSS; the API needs a key). **Register an EIA key.** Then use the schedule page (holiday shifts) plus polling of the ETag on `ir.eia.gov/wpsr/table1.csv` and `wpsrsummary.pdf` from 10:30. That is official file polling, not scraping.
- **ISM PMIs** (reCAPTCHA wall). Options **(3) manual** or **(4) link-only** for the T2 digest; the numbers are not free in any feed we can reach.
  - I could not verify ISM's PR Newswire distribution feed. Check it in Phase 1 before relying on it.
  - Do not build a browser scraper against reCAPTCHA. Per the Tier-2 rule: link with no number unless a free, reachable ISM release is confirmed.
- **S&P Case-Shiller** (S&P blocks every client we tried). **(4) Drop the primary.** Digest a **link with no number**, or use the FRED CSUSHPISA mirror tagged "via FRED (mirror)" if the desk accepts mirrors in T2. FRED LM 09-30 17:00 suggests same-day availability.
- **ADP** (JS-rendered, data in Google Sheets). **(1) is not recommended**: the page is headless-only and the data store is an implementation detail. Use **(3) manual or link-only**. The headline is free on the page, so a monitored headless render is acceptable as a T2 nice-to-have if the desk wants it.
- **Conference Board / NAHB / UMich.** The headline is in the publisher's own free page (and CSV for UMich). Use **(1) a daily monitored HTML check** for CB and NAHB, keyed on the "Updated:" or month text. UMich can use the CSV, which has ETag. Headline numbers only, with a link.
- **DOL claims.** No real feed is needed: the dated URL `eta{YYYYMMDD}` is predictable, `ui/data.pdf` has an ETag, and the RSS is a backstop.

## Flags

1. **Akamai on www.bls.gov.** The default httpx UA and a Chrome UA alone both get 403. Only the full Chrome header set passes. api.bls.gov is not gated. The bot must carry the full header profile, and its health checks must treat 403 plus "Access Denied" as a distinct failure. This was tested from this machine only; recheck from the GitHub/Azure runner egress before going live.
2. **BLS timestamps are pre-staged.** RSS pubDate is about 07:50 and PDF LM about 07:07 for 08:30 releases. **Never use them as the release timestamp in alerts.** Use the ICS or embargo-line official time plus our own first-seen time. PDFs can also be replaced after release (Empsit 08-07 PDF LM 10:15). Hash and store the first-seen copy.
3. **Keyless BLS API is 25 queries/day.** That is not enough for polling, so register a key: Build Order item 2 depends on it.
4. **Keys are pending for the BEA, EIA, Census and FRED APIs.**
   - The Census API is no longer keyless. It answers HTTP 200 with a redirect to missing_key.html.
   - BEA returns HTTP 200 with an error JSON.
   - Status-code checks will miss both, so validate the body.
5. **Stale official feeds.** EIA "This Week in Petroleum" RSS (last item 2025-10-29) and SEC `/rss/rules/proposed.xml` (2023-07-26). SEC `/rss/litigation/litreleases.xml` and `/rss/rules/final.xml` return 404, and the FDA RSS index moved. Feed health checks need a "newest item older than N days" alarm, not just HTTP 200.
6. **Calendar horizon and shutdowns.**
   - The BLS ICS ends 2026-12-30 (LM 2026-06-10) and the BEA ICS ends 2026-12-23, so 2027 schedules must be ingested when they are posted.
   - FY2027 starts today. No lapse or shutdown banner was seen on the BLS, BEA, Census, DOL, EIA, FDA or SEC home pages at about 06:05 ET.
   - The 2025 lapse shows in the BEA JSON as a 2.5-month gap followed by rescheduled dates. The bot needs a "calendar changed" diff alert, and "no release at scheduled time" must alert rather than fail silently.
7. **FDA warning letters are not real-time.** Posting lags issuance by weeks to months, and there is no country field in the export. The SPEC expects Indian facility coverage on a T1 real-time basis; I recommend downgrading warning letters to the EOD/weekly digest. Indian facility matching is feasible through the letter-page address and openFDA enforcement `country`.
8. **The US timing default (07:00 IST wrap) is fine for these sources.** All T1 US data prints observed land at 08:30 or 10:00 ET, which is 18:00 or 19:30 IST, so they arrive before midnight IST as SPEC states. No disagreement for data prints. FOMC is out of this group's scope.
9. **Tier-2 headline availability:**
   - Free in the publisher's own release: UMich, Conference Board, NAHB, ADP (JS).
   - Not reachable: ISM (reCAPTCHA) and Case-Shiller (403 everywhere). These go link-only, with no number.
10. **FRED is a mirror** (+4 to +67 min after the official release). Use it for back-fill and cross-checks only, never as the Stage 1 source URL.
11. **BLS JOLTS API quirk.** The API returned the message "Unable to get Catalog Data for series JTS000000000000000JOL" alongside valid data. Treat API `message[]` entries as warnings, not failures.
