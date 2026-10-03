# Progress

## Phase 0: feed verification and proposal (2026-10-01)

**Built**
- Repo scaffold at `Z:\Data Pipelines\events_bot`: uv (Python 3.12), git, `.gitignore`, and the probe dependencies (httpx, feedparser, icalendar, selectolax, pdfplumber, pymupdf, pyyaml, playwright).
- `docs/SPEC.md`, copied from the version supplied on 2026-10-01.
- Four probe suites in `docs/phase0/probes/`, with raw evidence in `docs/phase0/raw/` and per-group write-ups in `docs/phase0/*.md`:
  - US policy and fiscal: about 53 endpoints.
  - US data and US Tier 2: about 45 endpoints.
  - India Tier 1: 101 checks, plus 2 h of live RSS lag polling.
  - India Tier 2/3: about 40 endpoints, plus a scan of 2,203 PIB releases for PIB echo coverage.
- `docs/FEED_MAP.md`, `docs/PROPOSAL.md` and `docs/FLAGS.md`.

**Verified**
- Every source in SPEC was probed live from this machine.
- Lag was measured against past releases wherever the source exposes timestamps:
  - FOMC documents, Beige Book, minutes, Treasury auctions, TIC, DOL, Census durables and BEA RSS.
  - MoSPI CPI/IIP/GDP, OEA and RBI MPC PDFs.
- Lag was measured live for PIB RSS (median 5.7 min) and RBI RSS (≤2 min).
- These sources stamp files before release, so their lag is deferred to the live dry run: BLS, EIA, the MPC HTML page, and MoSPI/OEA embargo uploads.

**Tests:** none yet. Phase 0 contains no product code.

**Added to FLAGS.md:** F-01 to F-13 are decisions, T-01 to T-16 are technical hazards, plus an "unverified" list.

**Spec and prompt conflicts** (recorded here per prompt §9)
1. **PIB as single ingestion point.** SPEC says PIB is the single GoI ingestion point with no separate ministry scrapers. The prompt (and SPEC's own source list) has separate DGFT, MoF/DEA and CBDT/CBIC sources. Phase 0 shows PIB does not echo DGFT, CBDT, CBIC or IMD forecasts, nor most Tier 2 regulators. Following SPEC literally would miss DGFT entirely. Resolution proposed in FLAGS F-02, pending sign-off.
2. **Where US Tier 2 goes.** SPEC sends Tier 2 to the EOD digest; the prompt puts US Tier 2 in the 07:00 IST morning wrap. Pending sign-off (F-09). The recommendation is the morning wrap.
3. **Stage 1 summary.** SPEC asks for a 5–8 bullet summary in Stage 1. The prompt's Stage 1 doesn't mention bullets. SPEC wins: Stage 1 will carry 5–8 number-guarded bullets.
4. **Stage 2 transcript sources.** SPEC prefers official transcripts. The FOMC official transcript arrives 8–13 days late, so the prompt's "Fed: from the official transcript" isn't achievable within the 2–3 h window SPEC sets. Proposed resolution: faster-whisper on the Fed's video, then a follow-up once the official transcript posts (F-03).

**Status at end of Phase 0:** stopped for your go and the inputs in PROPOSAL §g.

## Decisions (2026-10-03)

- **Go on Phase 1.** All flags approved as recommended: F-02, F-03, F-05, F-07, F-08, F-09, F-11 and F-12; every (1) poller in PROPOSAL §d (F-04); `truststore` and `curl_cffi` (F-06).
- **Phase 1a: yes.** A capture-only harness runs for the 7 Oct MPC.
- **No BQL. Only publicly reachable sources** (F-14). Market data is FMP plus official public fallbacks. No Windows terminal box is needed.
- Still open: F-01 keys, F-10 (SMTP, recipients, Supabase, Anthropic/FMP keys, Linux host), F-13 watchlist.

## Phase 1a: MPC capture harness (2026-10-03)

**Built:** `harness/mpc_capture.py`, a capture-only harness that sends nothing. It polls:
- the RBI press-release RSS every 20 s and the notifications RSS every 60 s, both with conditional GET;
- the next 6 unpublished press-release IDs, each about every 30 s. Unpublished IDs return HTTP 200 with an empty page shell, so a release counts as live only when `.tablebg` is present;
- `Annualpolicy.aspx` for new links, and the RBI YouTube RSS.

For each new release it archives the HTML and every linked PressRelease PDF, with `%PDF` validation and backoff retry. It re-fetches the HTML 15 and 60 minutes later to catch re-uploads (T-08). Output goes to `archive/captures/<date>_mpc/` (`events.jsonl`, `polls.jsonl`, `raw/`, `SUMMARY.md`).

**Verified:** a 3-minute live run with `--seed-prid 63717` rediscovered IDs 63718 and 63719 and archived both pages and PDFs. There were 0 errors in 39 requests, about 13 a minute to rbi.org.in. A 75-second launch through Task Scheduler also polled correctly.

**Scheduled:** Windows task "events_bot MPC capture" runs once on 2026-10-07 from 09:40 to 14:00 IST (`harness/run_mpc_capture.cmd`, WakeToRun, StartWhenAvailable). It runs only while the user is logged on, so the PC must be on and signed in.

**Next:** Phase 1 core, built against local Postgres until F-10 inputs arrive.

## Phase 1: core framework (2026-10-03)

**Built** (package `events_bot/`, layout per BUILD_PROMPT §4):
- `core/`:
  - `models` (pydantic): SourceConfig, RawItem, FetchResult, ScheduledEvent, Message.
  - `config`: registry, delivery and keyword YAML. `registry`: adapter base with fetch and parse split, so replay never touches the network.
  - `db`: psycopg, migration runner. `archive`: content-addressed raw store. `fetch`: conditional GET, per-host politeness, truststore TLS, HTTP-200 bot-page rejection, archive-before-parse.
  - `dedupe`: URL, then document hash, then cross-source title rule. `pipeline`: dedupe → filter → classify → tag → route.
  - `scheduler` (APScheduler), `timeutil`, `logsetup` (structlog JSON).
- `migrations/0001_init.sql`: sources, http_cache, documents, events, items, extractions, filtered_items, sends (unique on ref, stage, kind), digests, source_health, health_alerts.
- `filter/`: keyword and ministry filter, with every rejection logged to filtered_items. Watchlist tagger (inert until F-13). Pass-through classifier interface.
- `deliver/`: Dispatcher with live, dry_run and replay modes. Email over SMTP, Jinja2 templates, Slack and Telegram stubs. Live mode claims the `sends` row before sending, so a restart cannot double-send. A crash between claim and send is reported, never resent automatically. `cli retry-send` resends only failed rows.
- `stage1/build.py`: deterministic Stage 1 for stream items: verbatim title, verbatim excerpt, source URL, source time and first-seen time. The key-number and bullet slots are filled in Phases 2–4.
- `ops/`: health skeleton (consecutive errors, stale stream, stuck send; each alert raised once and cleared when resolved), replay (`replay <ref>` and `replay --file`), and the CLI: migrate, sources, poll, run, replay, rejected, health, calendar, retry-send.
- Baseline guard: the first poll of a new source records existing items without alerting. Items older than 12 h go to the digest, not realtime.
- First adapter: `sources/india/rbi.py` (RbiRss) for `rbi_pr` and `rbi_notif`.

**Verified:**
- Against live RBI from this machine (dry run): the first poll baselined 10 + 10 items with correct IST times, and the second poll returned 304. `replay item:rbi_pr:prid:63719` re-rendered the alert from archived bytes.
- `run --dry-run` boots the scheduler and polls on interval.

**Tests:** 38 passing (`uv run pytest`). They cover timezone parsing, URL and title dedupe (including recurring same-source titles), conditional GET with 304, bot-page rejection, archive idempotency, baseline, filter logging and word boundaries, the old-item reroute, watchlist tagging, restart double-send, the failed-send retry gate, health alert raise/dedupe/clear, the min_items shape check, stuck claims, registry and route coverage, and replay without network.

**Local dev DB:** MSYS2 Postgres 18 in `C:\Users\HP\AppData\Local\events_bot_pg` on port 55433 (databases `events_bot` and `events_bot_test`). Start it with:
`PATH=/c/msys64/ucrt64/bin:$PATH pg_ctl -D /c/Users/HP/AppData/Local/events_bot_pg -o "-p 55433" start`

**Deviations from the prompt** (recorded here per §9):
1. Fetching is synchronous httpx on APScheduler threads, not async. Every job is short and I/O-bound, psycopg stays simple, and per-host politeness is a lock. This can be revisited if source count makes thread time matter.
2. The raw archive is local disk (`archive/raw`) behind a two-method interface. A Supabase Storage backend follows the F-10 decision.
3. `tzdata` was added. Windows has no system zoneinfo, and US Eastern needs DST rules (FLAGS F-15).

**Added to FLAGS:** F-15 (tzdata), F-16 (PIB ministry strings unverified).

**Next:** Phase 2, FOMC and RBI MPC, both stages. The 7 Oct capture feeds the MPC channel choice.
