# Economic Events Bot: Phase 0 Proposal

RAAS Research Capital · prepared 2026-10-01. Built against `docs/SPEC.md`. All feed claims below come from live probes run today; see `FEED_MAP.md` and `docs/phase0/`.

## Summary

- **Most Tier 1 sources have a usable official channel.**
  - US: Fed, BLS, BEA, DOL, Treasury, Census, White House, Federal Register, Congress API, FDA.
  - India: RBI, PIB, MoSPI, OEA, NSE.
- **About 17 sources have no feed** and need either an approved monitored poller or a fallback (§d).
- Four findings change the build:
  1. **PIB is not a single ingestion point.** It does not carry DGFT, CBDT/CBIC, IMD, or most Tier 2 regulators (FLAGS F-02).
  2. **FOMC presser transcripts post 8–13 days late.** Stage 2 for FOMC needs machine transcription of the Fed's video, the same approach as RBI (F-03).
  3. **The SEBI RSS is not real-time.** It missed every one of the 22 items SEBI posted on 1 Oct. Use the SEBI listing pages instead.
  4. **Five API keys are pending**, and Phase 3 is blocked on them (F-01).
- **Two live test windows are coming up:** RBI MPC on **7 Oct** and FOMC on **28 Oct**. I recommend running a capture-only harness for the 7 Oct MPC (§c).

## (a) Stack

As specified in the build prompt:

- Runtime: Python 3.12 managed with `uv`.
- Fetching and parsing:
  - httpx (async; conditional GET verified working on Fed, BLS, BEA, Census, DOL, EIA, SEC, TreasuryDirect XML, White House, RBI, CERC and PNGRB)
  - feedparser, icalendar, selectolax
- PDFs: pdfplumber, with PyMuPDF as fallback.
- Data and config: pydantic v2; Supabase Postgres via psycopg; SQL migrations.
- Scheduling and delivery: APScheduler; Jinja2 + SMTP.
- Diffing: difflib.
- Transcription: yt-dlp + faster-whisper.
- Anthropic API, used only behind the number guard.
- Logging and tests: structlog, pytest.

**Additions requested (F-06):**

| package | why | evidence |
|---|---|---|
| `truststore` | Uses the OS trust store, which fixes the incomplete TLS chains on dea.gov.in, cbic.gov.in, egazette.gov.in and the flaky cercind.gov.in, without ever setting `verify=False`. | india_t1 flag 10 |
| `curl_cffi` | BSE `api.bseindia.com` returns 403 to httpx (Akamai) but works with Chrome TLS impersonation. The in-house `nse-bse-disclosures-pipeline` already does the same. | india_t1 row "BSE notices" |

**Market data:**
- Use a `MarketDataProvider` with two implementations: BQL first, FMP as fallback.
- FOMC: US 2Y, US 10Y and DXY.
- RBI: India 10Y, USDINR and Nifty Bank.
- Instruments are configurable per event type in `config/sources.yaml`.
- If neither provider returns data, the market line is omitted.

**Timestamps:**
- Every output carries `source_published_at` (the time the source states), `official_release_at` (the scheduled time, from the calendar) and `first_seen_at` (our fetch time).
- Phase 0 found that many sources stamp the *scheduled* time or stage files early: Fed RSS, BLS, Census, MoSPI and OEA. So the alert shows the official time plus our first-seen time.

## (b) Feed per source

`FEED_MAP.md` has one row per source. The table below shows the primary channel and the fallback for each Tier 1 source.

| source | primary | fallback / cross-check |
|---|---|---|
| FOMC (statement, implementation note, SEP) | Fed `press_monetary.xml` RSS + deterministic URLs, polled in the 14:00 ET window | calendar.json, fomccalendars.htm |
| FOMC presser | faster-whisper on the Fed's presser video | official transcript PDF (deterministic URL, polled daily) |
| Fed minutes, Beige Book | deterministic URLs on calendar.json dates | `press_monetary.xml` (minutes only) |
| Fed speeches | `speeches_and_testimony.xml` (governors) | regional: Richmond/Dallas RSS; NY Fed + voters via approved poller |
| BLS NFP/CPI/PPI/JOLTS | BLS API v2 (**key**) for numbers; `nr0.htm` for verbatim text and embargo line | BLS RSS as a change trigger; calendar from ICS |
| BEA GDP/PCE | BEA API (**key**) | BEA RSS + release HTML; calendar from `release_dates.json` |
| DOL claims | `eta{YYYYMMDD}` PDF | `ui/data.pdf` |
| Treasury auctions | TA_WS JSON → result XML | Fiscal Data `auctions_query` |
| Treasury refunding | GovDelivery RSS + refunding documents page | dates entered by hand each quarter |
| Census retail sales, durables | Census API (**key**) | `indicator.xml` headline + release PDF |
| Executive orders, tariffs | White House presidential-actions RSS (EOs, proclamations) | Federal Register API for the canonical EO number and text |
| USTR | approved poller of the press-release list | White House proclamations; Federal Register public inspection |
| Congress fiscal | Congress API (**key**) for laws/bills; OPM status JSON | CR deadlines entered by hand into the forward calendar |
| FDA | FDA press RSS (approvals); openFDA enforcement (country = India) | warning letters via the xlsx export, in the EOD digest (F-07) |
| PIB / Cabinet | PIB English RSS (`&reg=3`) → release page for ministry and time | `allRel.aspx` to fill gaps |
| RBI | press-release and notification RSS; HTML press release for MPC text | PDFs (with `%PDF` validation); YouTube RSS for the presser |
| SEBI | approved poller of 3 listing pages | daily reconciliation against SEBI RSS |
| GST Council | PIB (Ministry of Finance) | — |
| MoF/DEA | RBI press-release RSS (borrowing calendar); indiabudget.gov.in (Budget) | PIB for CBDT/CBIC press; notifications manual |
| DGFT | approved poller of 3 DGFT tables | — (no PIB echo found) |
| MoSPI CPI/IIP/GDP | MoSPI release-list JSON (POST), polled on release-calendar days | PIB |
| OEA WPI/core | predictable PDF URL / homepage link | PIB |
| CGA | PIB | — |
| IMD | manual on the 2 long-range forecast days + PIB (MoES) | IMD press list (lags ~44 h) |
| NSE/BSE | NSE circulars, ASM and GSM JSON (in-house bootstrap); niftyindices poller; BSE notices via curl_cffi | — |

## (c) Timeline

Estimates are working days of build. Calendar time also depends on how quickly you review: golden YAMLs, sample emails and approvals. Each phase ends with a commit and a `PROGRESS.md` entry.

| phase | scope | estimate | dependencies |
|---|---|---|---|
| 0 | Feed verification + these docs | done | — |
| 1 | Core: models, config/registry, DB migrations, archiver, scheduler, dedupe, `--replay` / `--dry-run`, email interface, health skeleton | 4–5 d | F-10 (Supabase, SMTP) for the live parts; can build against local Postgres meanwhile |
| 1a (optional) | **Capture-only harness for the 7 Oct MPC:** poll RBI RSS / HTML / PDF / YouTube from 09:50 IST, archive raw bytes and first-seen times. No alerts are sent. | 1 d, before 7 Oct | your go |
| 2 | FOMC + RBI MPC, both stages: extraction, validator, redline, vote and projection diffs, whisper transcripts, market line, golden tests (6 meetings each) | 7–9 d | golden YAML review by you; first live FOMC test on 28 Oct |
| 3 | BLS/BEA/DOL prints + US morning wrap | 3–4 d | **F-01 keys** |
| 4 | PIB filters + classifier; RBI/SEBI/GST/MoF/DGFT/MoSPI/OEA/CGA/IMD/NSE-BSE | 7–9 d | F-04 scraper approvals, F-13 watchlist |
| 5 | Remaining US Tier 1: Fed minutes/speeches/Beige Book, Treasury, Census, White House/USTR, Congress, FDA | 5–6 d | F-01 |
| 6 | Tier 2 India and US + India EOD digest | 5–6 d | F-04, F-05 |
| 7 | Tier 3 + weekly digest + 14-day forward calendar | 3 d | — |
| 8 | Ops hardening: heartbeat, health alerts, schema drift, Docker + systemd, RUNBOOK | 3–4 d | F-10 host |
| DoD | Replays + 7-day live dry run across all sources | 7 d elapsed (runs in parallel with fixes) | everything above |

**Total:** about 38–47 build days, plus the 7-day dry run. FOMC and MPC (Phase 2) can be in front of you in about 2.5 weeks from your go.

## (d) Sources with no reliable feed

Recommendation key: **(1)** approved monitored poller with a health check · **(2)** secondary official channel · **(3)** manual · **(4)** drop.

Every (1) poller in this table works the same way:
- The poller is isolated in its own adapter.
- It uses conditional GET where the source supports it.
- Its health rule is "parse yields ≥ N rows with the expected columns" plus a staleness check.
- It is listed here and in FLAGS.
- It is built only for the sources you approve.

| source | tier | rec. | what it polls | why |
|---|---|---|---|---|
| SEBI board outcomes / circulars / CPs | 1 | (1) | 3 server-rendered listing pages, page 1, every 5 min 10:00–23:00 | The official RSS missed all 22 items on 1 Oct. The listings are plain HTML. |
| DGFT notifications / PNs / TNs | 1 | (1) | 3 tables with a seconds-resolution `CRT DT` column, every 5 min 10:00–24:00 | The spec calls DGFT the most abrupt mover, and PIB does not echo it. This is the least fragile poller on the list. |
| MoSPI CPI/IIP/GDP | 1 | (1) + (2) PIB | official POST JSON, only in release windows (16:00–16:30) | The site is now a JavaScript app, so HTML scraping is impossible. The JSON is the site's own data. |
| NSE index changes (niftyindices) | 1 | (1) | one press-release page (ETag), every 15 min 17:00–23:30 | Changes land at 21:00–22:15. Direct SMID relevance. |
| NSE circulars / ASM / GSM | 1 | (1) | in-house cookie bootstrap; stable in-house for months | No official feed. |
| BSE notices | 1 | (1) | curl_cffi JSON (needs F-06) | Secondary to NSE. |
| OEA core industries | 1 | (1) + (2) PIB | homepage link at 17:00 on release day | The file name depends on the release date. (WPI has a predictable URL, so no poller is needed.) |
| CGA, GST Council | 1 | (2) PIB | — | Verified same-day on PIB. The sources' own sites are slow or hide attachments. |
| CBDT, CBIC | 1 | (2) PIB + (3) | — | 403 / JavaScript app with broken TLS. Rate decisions also come via GST Council or Budget items on PIB. |
| IMD monsoon | 1 | (3) + (2) PIB | — | 2 events a year; IMD's own page lagged 44 h. |
| Census calendar | 1 | (1) | list-view HTML with `AYYYYMMDDHHMM` anchors, daily | No ICS or JSON. Low fragility. |
| Regional Fed presidents | 1 | (1) + RSS | NY Fed + current-year voters' speech index pages, plus Richmond/Dallas RSS | No aggregate feed. Non-voters manual or dropped. |
| USTR | 1 | (1) + (2) | press-release list (ETag) | Tariff actions also arrive as White House proclamations. |
| Congress deadlines | 1 | (2) + (3) | Congress API + OPM status; CR dates entered by hand | congress.gov web pages are behind Cloudflare. |
| FDA warning letters | 1→EOD | (1) | daily xlsx export, then the letter page for the address | No feed. Posting lags issue by weeks. |
| IRDAI, PFRDA, CERC, PPAC, SIAM, FADA, CDSCO notices | 2 | (1) | listing pages; CERC/PNGRB support conditional GET | No feeds and not on PIB. PFRDA daily only (low value). |
| Power, DoT, MeitY, PNGRB, CDSCO NSQ | 2 | (1) + (2) PIB | the sites' own JSON backends | Cheaper and more stable than HTML. Ministry policy also goes via PIB. |
| NPCI | 2 | (3) | — | Blocked except in a browser (F-05). Monthly, on the 1st. |
| ISM | 2 | link only | — | reCAPTCHA. No number. |
| ADP | 2 | (3) / link only | — | Rendered by JavaScript (F-05). |
| Case-Shiller | 2 | link only, or FRED mirror (tagged) | — | 403 everywhere. |
| Conference Board, NAHB, UMich | 2 | (1) daily page check (UMich via CSV with ETag) | — | The free headline is on the publisher's own page. |
| PRS bill tracker | 3 | (1) weekly | per-bill pages for watched bills | Clean stage markup. PRS is secondary to sansad.in. |
| Election Commission | 3 | (2) PIB + (1) | ECI JSON as a backstop | Encrypted params may rotate. |
| CBO | 3 | (3), or re-test from a runner | — | DataDome. |
| Finance Commission | 3 | (3) / (4) | — | Nothing scheduled. |
| Gazette of India | — | (4) | — | No feed, postback search, broken TLS. |

## (e) Delivery schedule (recommendation for sign-off)

| cadence | time (IST) | content |
|---|---|---|
| India real-time | immediate | India Tier 1: Stage 1, plus Stage 2 for eligible events |
| US real-time | immediate | FOMC days (statement, SEP, presser Stage 2). **Proposed additions (F-08):** tariff proclamations and EOs that match India or watchlist keywords; an optional toggle for NFP and CPI (18:00 IST). |
| US morning wrap | 07:00 | US Tier 1 prints and stream items from the prior US session, plus US Tier 2 (F-09) |
| India EOD digest | 19:30 | India Tier 2 (+ FDA warning letters, F-07). Items posted after 19:30 roll into the next day's digest. |
| Weekly digest | Sat 09:00 | India + US Tier 3, plus the 14-day forward calendar for both countries |
| Ops heartbeat | 08:00 | Health, failures in the last 24 h, filtered counts. Goes to the operator, not the desk. |

Notes:
- **FOMC Stage 1** lands at 23:30 IST (00:30 IST after the US clock change on 1 Nov). Stage 2 then lands between about 01:30 and 03:30 IST.
- If the desk would rather not receive overnight emails, Stage 2 can be held for the 07:00 wrap. Stage 1 stays real-time either way.
- **India late-evening releases are common.** DGFT posts 17:00–23:00, SEBI board outcomes around 19:40, and index changes 21:00–22:15. These are Tier 1, so they go out in real time.

## (f) Stage 2 eligible list

FOMC, RBI MPC, GST Council meetings, SEBI board meetings, the Union Budget and US quarterly refunding.

Data prints (BLS, BEA, DOL, Census, MoSPI, OEA, CGA) get **Stage 1 only**: the print against the prior value, with revisions shown explicitly. They have no presser and no statement to redline, so a Stage 2 would only repeat Stage 1.

## (g) Inputs needed from you

1. **Your go on Phase 1**, plus sign-off on F-02, F-03, F-07, F-08, F-09, F-11 and F-12.
2. **Scraper approvals (F-04):** the (1) rows in §d. "Approve all as recommended" works.
3. **Dependencies (F-06):** `truststore`, `curl_cffi`.
4. **Keys (F-01)**, into `Z:\Data Pipelines\events_bot\.env`:
   - `BLS_API_KEY`, `BEA_API_KEY`, `CENSUS_API_KEY`, `EIA_API_KEY`, `API_DATA_GOV_KEY`
   - `ANTHROPIC_API_KEY`, `FMP_API_KEY`
   - SMTP credentials and the 3 recipients
5. **Supabase:** a new project for the bot, or a schema inside an existing project?
6. **Hosting:** which Linux host runs the bot (the systemd unit), and which Windows terminal box runs the BQL adapter?
7. **Watchlist + Indian pharma facility list (F-13)**, needed by Phase 4.
8. **Phase 1a (optional):** capture-only harness for the **7 Oct MPC**. Yes or no?
