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

**Local dev DB:** MSYS2 Postgres 18 in `C:\Users\HP\AppData\Local\events_bot_pg` on port 54433 (was 55433 until 2026-10-08, see FLAGS F-28) (databases `events_bot` and `events_bot_test`). Start it with:
`PATH=/c/msys64/ucrt64/bin:$PATH pg_ctl -D /c/Users/HP/AppData/Local/events_bot_pg -o "-p 54433" -l /c/Users/HP/AppData/Local/events_bot_pg.log start`

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

## Go-live for scheduled events (2026-10-07)

- **User request:** fire today's MPC email, and arm the tracker so that no event is ever missed.
- **Live switch:** repo variable `EVENTS_BOT_LIVE=scheduled`. Scheduled events (`windows.yml`, `ops fire`) send to bac-reports. The stream `tick` stays dry until the Phase 4 filters exist; set `true` to make everything live.
- **Bug fixed:** every DRY expression used `cond && '' || '--dry-run'`, which always yields `--dry-run` because `''` is falsy. The live switch therefore never worked before this fix.
- **Today's sends:** the 7 Oct MPC Stage 1 and Stage 2 were sent live at 11:17 IST via `ops fire` (sends 1 and 2). The bot first saw the resolution at 11:00 IST in the dry re-run; RBI posted it at 10:25 IST.
- **Never-miss arming:**
  - `cli arm --hours 36` claims every event opening within 36 h.
  - Both `calendars` (now 4 times a day) and `tick` (cron-job.org plus the GitHub schedule) dispatch `windows.yml` with `start_utc` 5 minutes before each window.
  - The claim on the events row ensures exactly one armed chain per event.
- **Health alerts:**
  - `unarmed:<ref>`: the window opens within 6 h and nothing is armed.
  - `missed:<ref>`: the window closed with no Stage 1.
  - Operator alerts are sent live whenever SMTP is configured. `calendars` runs `health` each time.

## Phase 4 (started 2026-10-07): PIB and a local LLM with no paid API

**Decision (user):** no API LLM, nothing that depends on the user's PC, and the LLM work should be heavily optimized.

**Model:** llama.cpp `llama-server` (build b11460) runs on the GitHub runner's 4 CPUs, serving a small GGUF model from the Actions cache. The default is Qwen3-4B-Instruct-2507 Q4_K_M (2.5 GB), configurable through repo variable `LLM_GGUF`. The client is OpenAI-compatible, so any endpoint can be substituted via `LLM_BASE_URL`.

**Optimizations:**
- The model sees only what the rules cannot decide.
- One item per call. The system prompt is byte-identical, so the KV cache is reused.
- Output is a schema-constrained boolean of a few tokens.
- Every answer is cached in `llm_cache` by sha256 of the request.
- The step runs only when the queue is non-empty.
- If the model is down, an item waits up to 60 minutes, then goes to the digest tagged "unclassified". Nothing is lost.

**PIB:**
- `allRel` (ministry-tagged, complete for the day) is merged with RSS (fast, covers midnight rollover) by PRID.
- The release page (ministry, Posted On in IST, paragraphs) is fetched only for kept or uncertain items.

**Evaluation on 263 hand-labelled releases** (`tests/golden/pib/triage_labels.json`, needs human review):
- The rules alone keep 21, all relevant; reject 120 with no relevant item lost; and leave 122 uncertain, of which 10 are relevant.
- The models scored on those 122:

  | Model | Relevant found (of 10) | False alarms | Unparsed | Median time per item |
  |---|---|---|---|---|
  | Qwen3-4B | 8 | 3 | 0 | 1.8 s |
  | Qwen2.5-7B | 8 | 4 | 0 | 2.2 s |
  | Llama3.2-3B | 6 | 4 | 75 unparsed | — |
  | Qwen2.5-3B | 4 | 2 | 0 | — |

- **Overall: 29 of 31 relevant captured, 3 noise items.** The two misses are the India-UAE task force meeting and the PM-Trump call, both borderline labels.
- Regression test `tests/test_pib.py` fails if a `pib.yaml` edit rejects any relevant release.

**Live:** a `tick` run on the runner polled PIB and baselined 20 items. The LLM step is skipped when nothing is pending.

**Tests:** 140 passing.

## 2026-10-07 (afternoon)

- **Detailed MPC review.** The user found Stage 2 unhelpful; the new `stage2/mpc.py` follows the house template and has these sections:
  1. decision, with settings against the prior meeting;
  2. what the MPC decided;
  3. rationale;
  4. growth, with the GDP table;
  5. inflation, with the CPI and core tables;
  6. global backdrop;
  7. vote and dissent;
  8. Governor's Statement by topic (from the PDF, footnotes and sign-off removed);
  9. developmental and regulatory measures (SDRP);
  10. what happens next;
  11. wording changes.

  All text is verbatim, and a test checks every quoted paragraph against the sources. Today's review was re-sent to bac-reports (send 5, `resend2`).
- **SEBI.** `sebi_circ` (circulars and consultation papers) and `sebi_pr` (press releases, filtered, with board outcomes tagged). Listing pages are used because the RSS is stale. A date-only stamp counts as end of day for freshness.
- **First live LLM triage on the runner.**
  - 6 PIB items, all judged not relevant, correctly (five "PM shares an article", one event inauguration).
  - Timing: 15 s model cache restore, 4 s server start, 33 s total.
  - "shares an article" was added to the PIB excludes so these no longer cost calls.
- **Tests:** 144 passing.
- **MoSPI (NSO).**
  - Polls the POST-only release API every tick. It is reachable from runners; the first run baselined 30 releases.
  - CPI, IIP and GDP releases have their PDF fetched and numbers extracted and validated, giving a print-vs-prior alert in the house style.
    - CPI: headline table, layout-independent. July and August 2026 have different layouts.
    - IIP: headline growth, prior month and the four sectors, including "(-)" negatives.
    - GDP: real and nominal GDP, GVA, GFCF and PFCE, plus MoSPI's verbatim key highlights.
  - Other NSO releases (PLFS, ASI, ISP and similar) go out as normal alerts.
  - New `growth_pct` validator bounds of -60 to 100.
  - Cross-month check: August's "prior" equals July's printed value.
  - Latency is 15-minute polling for now. Scheduled windows from the advance release calendar are a follow-up, so 16:00 prints arrive within about 15 minutes.
- **Tests:** 151 passing.
- **OEA (DPIIT).** WPI (headline, food index, primary, fuel and manufactured groups, each current and prior) and Eight Core Industries (growth current and prior, fiscal-year-to-date cumulative), with verbatim key highlights.
  - Discovery is from the homepage "Latest ... Press Release" links; file names shift with holidays.
  - Image-only (scanned) PDFs extract nothing, so the alert subject says EXTRACTION FAILED.
  - Live on the runner: baselined 3 releases.
- **Live triage, 12 new PIB releases:** 4 removed by rules and 8 by the model, none sent. All decisions were reasonable; the EFTA TEPA remarks are borderline.
- **Tests:** 156 passing.
- **NSE surveillance (ASM long- and short-term, GSM).**
  - Cookie bootstrap, then the full lists are diffed against the previous archived snapshot. Each change produces one consolidated alert (added, removed, stage change). Watchlist names are flagged; their symbols and names are in the summary so tagging works.
  - Baselined on the runner.
- **NSE Indices.**
  - Listing of about 1,500 press releases; the adapter reads the newest 60.
  - Routine maintenance is filtered out: Nifty IPO, fixed income, bonds, SME.
  - The release PDF is parsed into verbatim (index, action, company, symbol) rows. The semi-annual 10 Aug review gives 980 rows, with Nifty 50 changes WIPRO out and BSE in.
  - Single-index notices take the index from the title.
  - The alert is compact: headline indices by symbol, a watchlist table, and counts for the other indices. It stays under 90 KB, below Gmail's clipping limit of about 100 KB.
- **Performance.** The pipeline now runs one "known ext_ids" query per poll. The NSE Indices baseline had made one tick take 11 min 40 s; polling all 11 sources now takes 36 s.
- **Tests:** 162 passing.

## Phase 4 continued (2026-10-08): RBI directions, CBDT, CBIC, BSE, IMD, data-print windows

All new stream sources are **dry** (tick runs `--dry-run` until `EVENTS_BOT_LIVE=true`). Everything below was verified with dispatched runs on a GitHub runner (ticks 37744055991 and 37744965586, calendars 37744961269) unless noted.

**RBI** (`sources/india/rbi.py`, FLAGS F-23, T-18)
- Title tags: `rbi:directions`, `rbi:amendment_directions`, `rbi:master_direction`, `rbi:draft`, `rbi:policy_rates`, `rbi:bulletin`, `rbi:enforcement`.
- Gap backfill: prid and notification Id are sequential, so ids skipped between the stored maximum and the oldest new feed id are fetched from their display pages (an unpublished id has no `.tablebg`). At most 25 per poll; never on the baseline poll.
- `rbi_pr` keyword file drops routine market operations (VRRR/VRR, Money Market Operations, auction notices and results, Reserve Money) and co-operative bank penalties. Runner: 5 new press releases on 8 Oct, all 5 correctly filtered.
- Master directions and drafts were already covered by `rbi_notif` and `rbi_pr`; no separate scraper for `BS_ViewMasterDirections.aspx` (Phase 0: reference only).

**Fetcher**
- `impersonate=True` sends the request through curl_cffi (Chrome 124 fingerprint) for Akamai-fronted hosts. Tests route it through the mock transport.
- `HOST_INTERMEDIATES`: for hosts that do not send their intermediate certificate, a certifi store plus the shipped intermediate (`core/certs/`), for that host only. Verification is never disabled. CBIC (Sectigo OV R36, from the issuer's AIA URL) is the first.
- `certifi` is now an explicit dependency (it was already installed through httpx).

**CBDT** (`sources/india/cbdt.py`, F-25): Liferay search API with the site's own blueprints, three POSTs per poll (notifications, circulars, press releases, 20 each). `receivedDate` gives the IST posting time. Institution approvals filtered (`config/keywords/cbdt.yaml`). Runner: baselined 60.

**CBIC** (`sources/india/cbic.py`, F-24): `fetchUpdatesByTaxId` for GST, Customs and Central Excise (newest 4 each). Rate-bearing notifications (Rate, ADD, CVD, safeguard, excise tariff) tagged `cbic:rate`; tariff-value fixations and adjudication appointments filtered. Runner: baselined 12 with the shipped intermediate; the second tick hit "connection reset by peer", so polls now retry 3 times.

**BSE notices** (`sources/india/bse.py`, F-26): curl_cffi, today and yesterday each poll. Segment/category/department go into the summary so the keyword file can exclude SME, debt, MF and SLB. About 5 of 45 a day are kept. Runner: baselined 54; next tick 4 new, all filtered.

**IMD** (`sources/india/imd.py`, T-17): English rows of the press-release list; monsoon, seasonal and monthly outlooks, withdrawal/onset, cyclone and heat-wave releases kept; the daily bulletin is not. New `tick_every_minutes` (60) throttles the 4 MB page in `tick`; `active_hours` 07:00–23:00. Runner: baselined 44.

**Data-print windows** (`sources/india/data_prints.py`, F-27)
- Scheduled source `data_prints`: CPI, IIP and quarterly GDP from the MoSPI ARC PDF (pointer API, then pdfplumber tables), WPI and ICI by rule. Weekend dates move to Monday, which matches every weekend slip in the 2026 ARC.
- Window 10 min before to 45 min after release; from release time it runs the `mospi`/`oea` stream poll every 30 s, so the existing validated print-vs-prior alert goes out within about 30 s.
- `reconcile()` (called by `tick`) marks an event captured when its print arrives later, so a holiday slip raises one `missed:` alert that then clears.
- Embargo (T-09): MoSPI/OEA hold back new items of a kind due within 12 h.
- Window sends follow the stream switch: dry unless `EVENTS_BOT_LIVE=true` (`windows.yml` now passes the variable).
- Runner calendars: CPI 12 Oct 16:00, WPI 14 Oct 12:00, ICI 20 Oct 17:00, IIP 28 Oct 16:00 (plus FOMC 28 Oct). They are armed by the usual `arm --hours 36`.

**Tests:** 187 passing (new: test_rbi_phase4, test_tax, test_bse, test_imd, test_data_prints).

**Still open in Phase 4:** CGA monthly fiscal deficit, GST Council outcomes (via PIB), Budget-day window (indiabudget.gov.in), DGFT (blocked from runners, F-17).

**Local test DB moved to port 54433** (F-28).

## Desk scope and the Daily Macro Digest (2026-10-09, F-29)

**Why:** with streams briefly live on 9 Oct, the user found the per-release e-mails too thin (title, excerpt, link) and too many, and paused them (`EVENTS_BOT_LIVE=scheduled`). The desk uses the bot to track macro releases that might affect the overall book. Approved design: few e-mails, each detailed.

**Routing** (`events_bot/digest/scope.py`, applied in `Pipeline.deliver` after the source filter, LLM triage and enrichment):
- Realtime: FOMC and RBI MPC (scheduled, unchanged) and the data prints (MoSPI CPI/IIP/GDP, OEA WPI/ICI) with their validated print-vs-prior alerts.
- Daily digest (`india_eod`, 18:30 IST Mon–Fri): PIB macro policy, RBI rule changes and reports, SEBI circulars / consultation papers / market-rule press releases and Board outcomes, CBIC rate notifications, IMD monsoon forecasts, Nifty 50 / Nifty Bank changes, MoSPI/OEA non-print releases.
- Dropped (stored and logged in `filtered_items` as `out_of_scope`, never e-mailed): BSE notices, NSE ASM/GSM, CBDT, CBIC procedure, other index changes, the rest of PIB, RBI routine operations / entity actions / narrow-class rules, MPC documents already covered by the MPC alert.
- An item the LLM could not triage (model down) still reaches the digest, tagged unclassified.
- Items queued before these rules are re-scoped when the digest compiles.

**Digest item** (`digest/content.py`, `digest/select.py`, `digest/render.py`): the release is fetched and archived at compile time (RBI display page; SEBI page → embedded PDF; PIB release page; IMD/MoSPI/OEA/NSE Indices PDF; CBIC via `/api/cbic-*-msts/{id}` → `/content/pdf/<docFilePath>`, unverified from runners). Its text is a pure function of the bytes (stored on `documents.text`). Rendered per item:
- header: issuer, kind, posted time, reference numbers, the "comes into effect" sentence (not for press releases that list many dates);
- "What it says": 3–10 operative paragraphs picked by a deterministic score (directive verbs, figures, dates, the lede), in source order, from the first 40 paragraphs (Board outcomes: whole document); title repeats, sign-offs, reference lines, cover pages and Hindi blocks skipped; paragraphs over 1,100 chars cut at a sentence end and marked […];
- key figures (₹/Rs/US$ amounts, crore/lakh, %, bps, tonnes, GW) as validated extractions, one row per sentence with the sentence quoted;
- who it applies to (the addressee block after "To," or "All …" lines, "shall be applicable to …" sentences);
- links to the release and the PDF.
- No LLM is used in the digest; every quoted string is an exact substring of the archived release text (`tests/test_digest.py` checks this on 6 real releases).
- If a release cannot be fetched, the item says so and shows the poll-time opening paragraphs (PIB) instead.

**Size:** Gmail clips at about 102 KB. Blocks set the font once, and a long day is split into up to 3 numbered parts (`part 2 of 3`, refs `…:part2`) at full detail; only beyond that are the lowest-priority items shortened to 3 paragraphs.

**Send:** `cli digest` (workflow `digest.yml`, GitHub cron 13:00 UTC Mon–Fri as backup; cron-job.org trigger still to add). Sends to the desk only when `EVENTS_BOT_LIVE=true`; otherwise rendered to the artifact. One digest per date (`sends` unique on `digest:india_eod:<date>`); after a full send the items are marked `digested` with a `digests` row (migration 0004). `--sample-days N --to-operator` sends a review copy of the last N days to the operator only, with a SAMPLE subject, marking nothing.

**Backlog guard:** the stream `tick` keeps queuing digest items while dry, and dry digests do not mark them. A digest only takes items first seen in the last 4 days (a weekend plus a holiday); older queued items are logged to `filtered_items` as `stale`, so the first live digest is not weeks long.
