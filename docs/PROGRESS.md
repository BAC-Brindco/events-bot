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

**Status:** **STOPPED. Waiting for your go** and the inputs in PROPOSAL §g.
