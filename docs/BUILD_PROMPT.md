# Build Prompt: Economic Events Bot (Full Build, India + US, All Tiers)

> Copied verbatim from the 2026-10-01 session. Amendments since: BQL dropped, public sources only (FLAGS F-14, 2026-10-03); Phase 0 flags all approved 2026-10-03.


You are building an internal bot for an investment desk. It does three things:

1. Maintains a forward calendar of scheduled policy and economic events in India and the US.
2. Monitors official channels for both scheduled and unscheduled releases.
3. Pushes structured updates at three cadences: real-time (Tier 1), EOD digest (Tier 2) and weekly digest (Tier 3).

India and US are parallel pipelines with the same architecture. US is a first-class track.

The full spec is in `docs/SPEC.md`. Read it end to end before writing any code. This prompt covers **every source in the spec**. Build it in the order given in section 4.

---

## 1. Non-negotiable rules

1. **No model-generated numbers.**
   - Every figure in any output must be extracted from a source document or official API response, and must be traceable to its exact location.
   - If extraction or validation fails, send the raw source link flagged `EXTRACTION FAILED`.
   - A wrong number is worse than no alert.
2. **Every output item carries its source URL and release timestamp.**
   - Use the publish time stated by the source where one exists. Also record our fetch time.
   - Store all times in UTC. Render them in IST.
3. **Stage 2 is primary sources only.**
   - Allowed: statements, transcripts, official speeches, published projections.
   - Media is allowed only in the market reaction line, tagged `[MARKET DATA]`.
4. **Prefer official RSS, APIs and ICS feeds over scraping.**
   - Isolate any HTML parsing inside its own adapter, with a health check, and list it in `docs/FLAGS.md`.
   - If a source has no reliable feed, flag it rather than building a fragile scraper around it. Build a scraper only where I approve it after Phase 0.
5. **Never block on a missing transcript or document.** Send what exists, state what is pending, and follow up when it posts.
6. **Do not invent endpoints.** Every URL in config must have been fetched successfully by you. Anything unverified goes in `docs/FLAGS.md`.
7. **Silent failure is the main risk.** Every source must have health monitoring that alerts.

---

## 2. Stack

- Python 3.11+, managed with `uv`
- Fetching and parsing:
  - `httpx` (async, with conditional GET via ETag and If-Modified-Since)
  - `feedparser`
  - `icalendar`
  - `selectolax` or `beautifulsoup4` for HTML
- PDFs: `pdfplumber`, with `PyMuPDF` as the fallback. Use `camelot` or `pdfplumber` tables for tabular releases.
- `pydantic` v2 for models and settings. Secrets go in `.env`.
- Supabase Postgres via `psycopg`, with SQL migrations in `migrations/`
- `APScheduler` for scheduling
- Email: `Jinja2` templates sent over SMTP
- `difflib` for redlines
- `yt-dlp` and `faster-whisper` for pressers that have no same-day transcript
- Anthropic API, used only for these four jobs, always behind the number guard (section 7):
  - relevance classification
  - prose bullets
  - theme grouping
  - digest summarisation
- `structlog` for logging, `pytest` for tests

Ask before adding any dependency outside this list.

**Market data.** Build a `MarketDataProvider` interface with two adapters:
- a BQL adapter, which runs only on a terminal-connected host
- an FMP fallback

If neither returns data, omit the market line.

---

## 3. Architecture

**Source registry.** All sources are declared in `config/sources.yaml`. Each entry has these fields:
- `id`, `country`, `tier`
- `kind`: `scheduled`, `stream` or `both`
- `feed_type`: `rss`, `api`, `ics` or `html`
- `urls`, `poll_interval`, `window_poll_interval`
- `filters`
- `adapter`
- `stage2_eligible`
- `health_rules`

Adding a source should mean adding a YAML entry plus one adapter module. Source-specific logic never goes in `core/`.

**Two ingestion modes.**
- **Scheduled.** These are known release times (FOMC, MPC, CPI, NFP, GDP, auctions, etc.).
  - The calendar is refreshed daily from official calendars. Never hardcode dates.
  - Polling runs tight inside the event window, from T minus 10 min to T plus 90 min.
- **Stream.** These are unscheduled releases (PIB, RBI circulars, SEBI circulars, DGFT, NSE/BSE circulars, FDA, White House/USTR, Federal Register, etc.).
  - Poll continuously at the source's interval.
  - Run every new item through dedupe, then filter, then classify, then route.

**Filtering and routing for streams.**
- **PIB.** Filter on ministry tag plus a keyword list kept in `config/keywords/pib.yaml`. Seed the list with MSP, subsidy, PLI, export, duty, disinvestment and FDI.
  - Keep the list editable without a code change.
  - Log every item filtered out to `filtered_items`, so misses can be reviewed and the list expanded.
  - Provide a CLI command that shows the last N rejected items.
- **Watchlist tagging.**
  - `config/watchlist.csv` holds tickers and company names for the book. Tag any item that mentions them.
  - NSE/BSE ASM and GSM additions, index changes, and FDA actions on Indian facilities get priority tags.
  - Keep a configurable list of Indian pharma companies and their facility names for FDA matching.
- **Dedupe.** The same announcement often appears on several channels (for example a ministry notification and PIB). Dedupe by URL, by title similarity, and by document hash.

**Delivery cadences.** All times are configurable in `config/delivery.yaml`.
- **India real-time (Tier 1).** Sent immediately.
- **US real-time.** FOMC days only (statement, presser, SEP), sent immediately.
- **US morning wrap, 7 AM IST.** Covers US Tier 1 prints and stream items from the prior US session, plus US Tier 2.
- **India EOD digest, 7:30 PM IST.** Covers India Tier 2.
- **Weekly digest, Saturday 9 AM IST.** Covers India and US Tier 3, plus the forward calendar for the next 14 days.

Put the delivery schedule in the proposal as a recommendation for sign-off. Delivery sits behind an interface. Email comes first, with Slack and Telegram adapters stubbed.

**Two stages.**
- **Stage 1 (release alert).** Applies to every Tier 1 scheduled release and to Tier 1 stream items.
- **Stage 2 (post-event summary).** Applies only to `stage2_eligible` event types. Initial set:
  - FOMC
  - RBI MPC
  - GST Council meetings
  - SEBI board meetings
  - Union Budget
  - US quarterly refunding

  Data prints get Stage 1 only, with print vs prior. Note this in the proposal.

---

## 4. Build order and phases

Work through the phases in order. Commit at the end of each phase and append a summary to `docs/PROGRESS.md`. The summary covers what was built, what was verified, what tests pass, and what was added to `FLAGS.md`.

**Phase 0 is the only mandatory stop.** After that, continue phase to phase unless you are blocked. Blocked means a source needs a decision from me, credentials are missing, or the spec and this prompt conflict.

### Phase 0: verify every feed and write the proposal. Then STOP.

**Verify each source live:**
- exact URL and format
- auth or API key requirements
- rate limits
- the publish lag versus the official release time. Check this against at least 2 past releases where possible.
- archive depth (how far back history is available)
- known failure modes

**Treat these candidates as unverified until you fetch them:**
- **Fed:** RSS feeds (monetary policy, all press releases, speeches), the FOMC calendar page, and the URL patterns for the statement, implementation note, SEP, minutes, presser transcript and Beige Book
- **BLS:** API v2 and the release calendar (ICS)
- **BEA:** API and release schedule
- **Census:** economic indicators API and release schedule
- **DOL:** weekly claims release
- **Treasury:** Fiscal Data API, TreasuryDirect auction data, and the refunding pages
- **Executive orders and BIS export controls:** Federal Register API
- **Congress:** congress.gov API
- **SEC:** RSS feeds
- **EIA:** API v2
- **FDA:** openFDA, warning letters
- **CBO:** feed
- **TIC:** release pages
- **RBI:** press release and notification RSS, MPC schedule release, MPC resolution and Governor's statement patterns, and the official YouTube channel
- **PIB:** RSS and ministry tagging
- **SEBI:** RSS
- **India statistical sources:** check for official APIs or release calendars (MoSPI, OEA, CGA)

**Do a reachability check on every Tier 2 and Tier 3 source in the spec.** Expect some to have no feed (for example GST Council, DGFT, IMD, NSE/BSE circulars behind anti-bot checks, ISM and the Conference Board behind paywalls or embargoes).

**For each source with no reliable feed, recommend one of these:**
- (1) an approved monitored scraper with a health check
- (2) a secondary official channel, such as a PIB echo or a Gazette notification
- (3) manual
- (4) drop

**Deliverables:**
- `docs/FEED_MAP.md`: one row per source in the spec. Columns: source, country, tier, kind, feed type, URL, verified, lag, notes.
- `docs/PROPOSAL.md`, covering:
  - (a) the stack
  - (b) the feed per source
  - (c) a realistic timeline for each phase below
  - (d) the sources with no reliable feed, with a recommendation for each
  - the proposed delivery schedule
  - the Stage 2 eligible list
- `docs/FLAGS.md`

**Stop and wait for my go.**

### Phase 1: core framework

**Repo layout:**

```
events_bot/
  core/        models, config, registry, db, scheduler, timeutil, dedupe
  calendar/    calendar refresh per source, unified forward calendar
  sources/
    us/        fed, bls, bea, dol, treasury, census, whitehouse_ustr, congress, fda,
               ism, umich, conference_board, adp, housing, sec, bis, eia, cbo, tic
    india/     pib, rbi, sebi, gst_council, mof_dea, dgft, mospi, oea, cga, imd, nse_bse,
               trai, irdai, pfrda, npci, cerc, pngrb_ppac, dot, meity, cdsco, siam_fada,
               niti, prs, eci, finance_commission
  filter/      keywords, classifier, watchlist
  extract/     one module per document type, validator.py
  diff/        redline.py, structured_diff.py
  stage1/
  stage2/      build.py, transcripts.py, themes.py
  digest/      us_morning_wrap.py, india_eod.py, weekly.py
  market/      provider.py, bql.py, fmp.py
  deliver/     interface.py, email.py, slack_stub.py, telegram_stub.py, templates/
  ops/         health.py, heartbeat.py, replay.py, cli.py
config/        sources.yaml, delivery.yaml, keywords/, watchlist.csv, recipients via env
tests/         fixtures/, golden/
migrations/
docs/
```

**Database tables (minimum):**
- `sources`
- `events`: scheduled items, with the calendar source URL and refresh time
- `items`: stream items
- `documents`: id, parent ref, doc_type, url, published_at, fetched_at, sha256, http_headers, raw storage key, text
- `extractions`: document_id, field, value_text (exact source string), value_norm, unit, period, page, char_start, char_end (or a JSON path for API responses), snippet, validated
- `filtered_items`
- `sends`: unique on (ref, stage, kind) so a restart can never double-send
- `digests`
- `source_health`

**Fetching and archiving:**
- Archive the raw bytes of every new document, or the raw response of every API call, before parsing.
- Build `--replay <ref>` and `--dry-run` into the core from day one.

### Phase 2: FOMC and RBI MPC, both stages

**FOMC fields:**
- target range
- voters for and against, with any stated dissent preference
- implementation note: IORB, ON RRP, primary credit, runoff caps
- SEP medians: GDP, unemployment, PCE, core PCE, fed funds by year and longer run

**RBI MPC fields:**
- repo, SDF, MSF and Bank Rate
- stance
- rate and stance vote splits, with names
- CPI and GDP projections, full year and quarterly
- any CRR or SLR change

**Stage 2:**
- Statement redline: align at sentence level, then diff at word level, rendered as HTML with `<del>` and `<ins>`.
- Vote split diff.
- Projections table: prior, current, and a change column computed arithmetically from extracted values and labelled as computed.
- Discussion points:
  - Fed: from the official transcript.
  - RBI: from a faster-whisper transcription of the official YouTube presser, labelled `Machine transcription, pending official transcript`, with a follow-up when the official transcript posts.
- Market reaction line:
  - FOMC: US 2Y, US 10Y and DXY.
  - RBI: India 10Y, USDINR and Nifty Bank.
  - Instruments are configurable per event type.

**Golden tests.** Archive the last 6 meetings of each. Write the expected values to YAML with `needs_human_review: true`.

### Phase 3: US Tier 1 data prints and the morning wrap

**Prints covered:**
- BLS: NFP (headline payrolls, unemployment rate, AHE m/m and y/y, revisions to the prior two months), CPI and core CPI (m/m and y/y), PPI, JOLTS
- BEA: GDP (advance, second, third), PCE and core PCE
- DOL: weekly claims

**Extraction:**
- Prefer structured API values. Store the API response as the document and the JSON path as the location.
- Every print shows current vs prior. Revisions are shown explicitly.
- No consensus or forecast figures, because they are not primary.

**Build the US morning wrap digest.**

### Phase 4: PIB with filters and the remaining India Tier 1

- PIB: Cabinet and CCEA decisions, with ministry plus keyword filtering, rejected item logging, and the classifier.
- RBI: circulars, master directions, draft regulations, bulletin.
- SEBI: board outcomes (Stage 2 eligible), circulars, consultation papers.
- GST Council: outcomes (Stage 2 eligible).
- MoF/DEA: borrowing calendar, Budget documents (Stage 2 eligible on Budget day), CBDT/CBIC notifications.
- DGFT: export bans, restrictions and duty changes, with priority alerting.
- MoSPI/NSO: CPI, IIP, GDP, extracted from the release PDFs or an API, current vs prior.
- OEA: WPI and core sector.
- CGA: monthly fiscal deficit.
- IMD: monsoon forecasts.
- NSE/BSE: index changes and ASM/GSM additions, tagged against the watchlist.

Use the Phase 0 recommendation for any source without a feed.

### Phase 5: remaining US Tier 1

- **Fed:** minutes, speeches by the governors and regional presidents (from the public calendar), and the Beige Book.
- **Treasury:** quarterly refunding (Stage 2 eligible) and auction results (high yield, bid-to-cover, indirect/direct/dealer shares, tail versus the WI only if WI comes from a primary source, otherwise omit).
- **Census:** retail sales, durable goods.
- **White House/USTR:** executive orders and tariff actions, via the Federal Register API plus the official press release feeds.
- **Congress fiscal events:** debt ceiling, shutdown deadlines, major bills. Keep a deadline tracker in the forward calendar.
- **US FDA:** approvals and warning letters, with Indian facility matching.

### Phase 6: Tier 2 and the EOD digests

**India:** TRAI, IRDAI, PFRDA, NPCI, CERC/Power, PNGRB/PPAC, DoT, MeitY, CDSCO, SIAM/FADA auto sales.

**US** (these go in the morning wrap): ISM PMIs, UMich and Conference Board sentiment, ADP, housing (starts and permits, Case-Shiller, NAHB), SEC rulemaking and enforcement, Commerce/BIS export controls, EIA inventories.

Where a figure sits behind a paywall or embargo, include the headline only if it comes from the publisher's own free release. Otherwise send the link with no number.

### Phase 7: Tier 3 and the weekly digest

**India:** NITI Aayog, PRS Legislative (bill status tracking, diffing stage changes week to week), Election Commission, Finance Commission.

**US:** CBO projections, Treasury TIC flows.

The weekly digest also carries the 14-day forward calendar for both countries.

### Phase 8: operations hardening

- **Heartbeat:** a daily 8 AM IST email covering upcoming events, source health per source, failures in the last 24 hours, and the count of filtered items.
- **Health alerts:** send an alert for each of these:
  - a source errors 3 times in a row
  - a calendar refresh fails
  - a scheduled event's window closes with no document found
  - a stream source has published nothing for longer than its configured staleness threshold
  - a parser's output shape changes (a schema drift check against the fixtures)
- **Deployment:** Dockerfile plus a systemd unit. Secrets go only in env.
- **Runbook:** `docs/RUNBOOK.md` covers setup, env vars, adding a source, editing keywords and the watchlist, and the meaning of every alert.

---

## 5. Email formats

Every number in a subject line comes from extractions. If extraction failed, the subject says `EXTRACTION FAILED`.

**Subjects:**
- Real-time: `[<SOURCE>] <event> | <extracted headline figure or title>`
- Stage 2: `[<SOURCE>] Stage 2 | <event>`

**Body order:** key numbers block, then source and timestamp, then bullets, then the links.

**Digests:**
- Grouped by country, then source.
- Each item is one line plus its link.
- Watchlist-tagged items come first.

---

## 6. Extraction validation

`validator.py` re-checks every extraction before it is used:
- The value must exist verbatim in the archived source at the stored location, or at the stored JSON path for API responses.
- Sanity bounds apply per field type. Rates must be between 0 and 20 percent. A policy rate change larger than 100 bp, or a data print outside its historical range, requires the source text itself to confirm it.
- The period must match the expected release period. This catches stale or cached documents.

A failed validation never reaches an email as a number.

---

## 7. Number guard (all LLM text, everywhere)

The LLM receives only the source text and the extraction table, with an instruction never to introduce figures.

After generation:
1. Tokenise every numeric token in the output: digits, percents, bp, dates and quarters.
2. Each token must match a validated extraction for that item or for the prior item used in the diff. If any token fails, drop that bullet or line.
3. If more than half the bullets are dropped, fall back to deterministic template text built from the extractions.
4. Every quoted span must be an exact substring of the source or transcript. Check this in code.

Write unit tests with adversarial cases: invented figures, rounding drift, a wrong year, a wrong quarter, and a prior value presented as the current one.

---

## 8. Definition of done

- Golden extraction tests pass for every scheduled data type, and the human-reviewed YAMLs are confirmed.
- `--replay` produces correct output for each of these:
  - the last 2 FOMC meetings
  - the last 2 MPC meetings
  - the last 2 US CPI releases
  - the last 2 NFP releases
  - the last 2 India CPI releases
  - the last GST Council meeting
  - the last SEBI board meeting
  - the last Budget

  Output goes to `out/replay/` for review.
- A replayed week of PIB shows the filter's keep/reject decisions, with the rejected list available for review.
- Each digest type renders correctly from a replayed week.
- The number guard tests pass.
- A 7-day live dry run completes across all sources with no unhandled errors. Any health alerts during the run are explained in `PROGRESS.md`.
- `FEED_MAP.md`, `FLAGS.md`, `PROPOSAL.md` and `RUNBOOK.md` are current.

---

## 9. Working style

- Keep adapters small, typed and tested. Each adapter ships with fixtures and at least one replay test.
- When the spec and this prompt disagree, follow `docs/SPEC.md` and record the conflict in `PROGRESS.md`.
- Do not build a scraper for anything that Phase 0 flagged unless I have approved it.
