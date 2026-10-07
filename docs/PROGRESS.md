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

## Hosting move: GitHub Actions + Supabase (2026-10-06)

**Decisions (user, 2026-10-06):** host on GitHub Actions + Supabase Pro, with no always-on box. The repo is public (`BAC-Brindco/events-bot`). Tables go in the existing `nse-announcements` project under schema `events_bot`, alongside `morning_brief` and `raas_macro`. A 15-minute poll is acceptable for the unscheduled stream. Calendar events get burst jobs that poll every 20–30 s inside their window.

**Built:**
- `EVENTS_BOT_DB_SCHEMA` setting. `Database` runs `create schema if not exists` and `set search_path` once per session. It does this per session because the Supabase session pooler drops DSN `options`.
- `0001_init.sql` was applied to Supabase via `cli migrate`, creating 12 tables in `events_bot`. The schema is not exposed through PostgREST.
- Repo secret `EVENTS_BOT_DSN`: the session-pooler DSN, the same one morning-brief uses.
- `harness/reachability.py` and the `reachability` workflow: every FEED_MAP endpoint is fetched with httpx, falling back to curl_cffi (Chrome).

**Verified:** reachability was 59/65 from the local PC and 57/65 from a GitHub runner (run 37423139536).

Fails only on the runner:
- **DGFT `/CP/?opt=notification`:** 403 from both clients, so an IP block. This is Tier 1 and PIB does not echo it.
- **CBIC:** TLS failure, missing intermediate certificate. Windows fills that gap via AIA and Linux does not. Fixable by shipping the intermediate certificate.
- **cdscoonline.gov.in:** DNS and connect failure (T2/3).
- **eci.gov.in:** 406 (T3).

Works only on the runner: irdai and ismworld.

Fails from both: the MoSPI API (needs a POST), YouTube RSS, dot.gov.in and cbo.gov, all as in Phase 0.

**Open:** how to reach DGFT (and CDSCO/ECI) from Actions. Options are an Indian-IP relay or a self-hosted runner.

## Everything on Actions (2026-10-06, continued)

**Decisions (user):** DGFT is dropped for now (FLAGS F-17). Everything is routed to GitHub Actions (F-18), and the local Windows task "events_bot MPC capture" is **disabled** (not deleted).

**Built:**
- `migrations/0002_raw_blobs.sql` and `DbArchive` (`EVENTS_BOT_ARCHIVE=db`): raw bytes go into Postgres, gzip-compressed and content-addressed with the same keys as the file archive. The reason is that runner disk is discarded after every job.
- `App.tick()` and `cli tick`: one pass over every enabled stream source, then health. Here `poll_interval` is ignored because the trigger cadence sets the pace. `active_hours` still applies.
- `db.claim_window_launches()`, `cli due-windows` and `cli windows`. `tick` claims events whose window opens within 25 minutes and dispatches `windows.yml`. That burst job polls the windows, then exits once none is open or due within 30 minutes. A calendar refresh keeps the claim unless the window has moved.
- Workflows:
  - `tick.yml`: `*/15` cron and dispatch. It runs dry-run until repo variable `EVENTS_BOT_LIVE=true`.
  - `windows.yml`.
  - `calendars.yml`: 06:00 and 18:00 IST.
  - `mpc_capture.yml`: self-chaining dispatch. Each run waits up to 330 minutes, then re-dispatches with `GITHUB_TOKEN`, because GitHub cron can start hours late.

**Verified:**
- 41 tests pass locally.
- Runner tick 1 baselined rbi_pr and rbi_notif (10 + 10) into Supabase. Runner tick 2 returned new 0, so state persists across runs.
- An MPC test chain on a 5-minute window handed off twice, then captured: 78 requests, 0 errors.
- The real chain for 2026-10-07 09:40–14:00 IST is running (run 37433907554 onward). Its artifact is `mpc_capture`, kept for 90 days.

**Open:**
- cron-job.org as the primary `tick` trigger, since the GitHub `*/15` schedule may lag.
- SMTP secrets and recipients (F-10) before `EVENTS_BOT_LIVE=true`.

## Phase 2: FOMC and RBI MPC, both stages (2026-10-06, in progress)

**Built:**
- `extract/base.py` and `extract/validator.py`:
  - Document text is a deterministic function of the archived bytes. Every extraction is a character span into it.
  - Fractions and hyphen variants are handled ("3‑3/4" uses U+2011).
  - The validator checks verbatim position, rate bounds (0–20 %), period (the dated line must match the meeting), and that any move over 100 bp is stated in the source.
- `extract/fomc.py`:
  - Statement: both formats (F-22). Fields are action, change size, target range, tally, voters for when listed, and dissenters with their verbatim preference.
  - Implementation note: IORB, standing repo rate, ON RRP rate and cap, primary credit, directive range, runoff caps, and the balance-sheet lines.
  - SEP table 1: medians, current and the table's own prior-projection row.
  - Cross-document checks: statement range = directive range, IORB inside the range, named dissenters = tally.
- `extract/mpc.py`:
  - Repo, SDF, MSF and Bank Rate; action; stance; rate vote, with unanimity found even when it is stated only in the rationale (Aug 2025).
  - The six members; stance and rate dissents with the verbatim view; CRR and SLR.
  - GDP, CPI and core projections, FY and quarterly, covering every phrasing seen since Aug 2025.
  - LAF corridor check.
- `diff/redline.py`: sentence alignment, then a word-level diff rendered with `<del>`/`<ins>`. It does not split on initials or decimals.
- `extract/guard.py`, the number guard from §7:
  - A number must equal a validated extraction at the same precision, for the period stated in its clause. After cue words such as "from" or "previously" it must come from the prior item.
  - Years, fiscal years and quarters must exist in the extractions. Quotes must be exact substrings of the source.
  - If more than half the bullets are dropped, the caller falls back to template text.
- `sources/us/fed.py`:
  - Calendar from fomccalendars.htm: 14:00 ET on the final day, DST-aware, with SEP flags and page links.
  - In the window it polls every 20 s. Statement, note and SEP 404 until posted, and a stale-page guard applies.
  - Stage 1 goes out when all documents are in, or at T+5 min with what exists. Stage 2 goes out at T+60.
  - Extractions are stored per document.
- `sources/india/rbi_mpc.py`:
  - Schedule and published index come from Annualpolicy.aspx, with FY blocks and cross-month meetings.
  - The resolution is discovered by RSS title or by probing the next press-release IDs; whichever finds it first wins.
  - Stage 1 goes out on first sight, Stage 2 at T+60.
- `stage1/render.py`: one HTML and text layout for every scheduled event. `stage1/fomc.py` and `stage1/mpc.py` build the content (rates, votes, projections). Stage 2 adds the redline, vote, dissent and membership changes, and prior→current tables whose changes are labelled as computed.
- `replay fomc:<date>` and `replay rbi_mpc:<date>`, from the archive or `--from-dir` fixtures. Output for the last 2 meetings of each is in `out/replay/`.
- `windows.yml` takes an optional `start_utc` with a self-chaining wait.

**Verified:**
- Golden YAMLs for 7 FOMC meetings (Dec 2025 to Sep 2026) and 7 MPC meetings (Aug 2025 to Aug 2026). All are flagged `needs_human_review: true`, with 0 failed validations.
- The September SEP's "June projection" row equals the June table's own medians.
- In every Stage 1 test, each number in the body appears verbatim in a source document.
- In-window flows against a mocked federalreserve.gov and rbi.org.in: Stage 1, then Stage 2, each once. Adapter tests cover SEP-late, a stale page, and discovery by both RSS and press-release ID probing.

**Tests:** 127 passing.

**Live test 2026-10-07 (RBI MPC, decision 10:00 IST)**, both runs on GitHub runners, neither sending email:
- `mpc-capture` chain (run 37433907554 onward): raw capture, 09:40–14:00 IST.
- `windows` armed for 09:45 IST (run 37460631606 onward): the real adapter, dry run, writing Stage 1 and Stage 2 HTML into the run artifact. The event was marked `burst_launched_at` so a tick cannot start a second job.

**Still open in Phase 2:**
- Discussion points: faster-whisper plus LLM bullets (F-21a/c).
- Market reaction line (F-21b).
- Transcript follow-ups: Fed official PDF at 8–13 days, RBI edited transcript at about 2 days (F-19).
- Human review of the golden YAMLs.

## Live test: RBI MPC 2026-10-07 (dry run)

- **Decision:** repo +25 bps to 5.50, unanimous; stance changed to calibrated tightening. Stance dissents from Dr. Nagesh Kumar and Prof. Ram Singh, who wanted it kept at neutral.
- **Release timing:** RSS pubDate 10:25 IST for the resolution (prid 63742), Governor's statement (63744) at 10:35, SDRP (63743) at 10:30. For first-seen timing per channel, see the `mpc_capture` artifact.
- **Failures found and fixed the same day:**
  1. The armed `windows` run slept about 5 h inside its own job, so the 6 h timeout killed it at 10:24 IST, one minute before the resolution appeared. The waiting now happens in hand-off runs only (`windows.yml`, `mpc_capture.yml`).
  2. Three phrasings were new, and the parser missed all of them: "change the stance to X", "CPI inflation is projected to be X per cent for FY", and "Two members - A and B - were of the view". The first draft therefore had no stance, CPI FY or quarterly figures, or dissents. All three are fixed. Required fields that are not found now render as EXTRACTION FAILED instead of being silently absent.
  3. The full-resolution redline was about 5,500 px of noise, because the outlook section is rewritten every meeting. Stage 2 now redlines only the policy paragraphs (decision, stance, vote, dissent). The sentence splitter no longer splits after Dr./Prof./Smt.
- After the fixes, the bot re-ran on GitHub in dry run and produced Stage 1 and Stage 2. The 7 Oct resolution is golden case 8. 131 tests pass.
