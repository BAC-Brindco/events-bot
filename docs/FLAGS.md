# Flags

These are open issues from Phase 0. The status column says what unblocks each one. The phase0 group files hold the evidence.

## Decisions needed from you

| id | flag | recommendation | status |
|---|---|---|---|
| F-01 | **API keys pending:** BLS v2, BEA, Census, EIA, api.data.gov (congress.gov and openFDA). Signing up through Playwright was blocked by Claude Code's permission check, and BLS and BEA use CAPTCHAs. | You register the keys and put them in `.env`. Without keys: BLS is capped at 25 queries/day, Census redirects to `missing_key.html`, BEA returns HTTP 200 with an error in the body, EIA returns 403 (DEMO_KEY allows 10/hr), and congress.gov DEMO_KEY allows 10/hr. | **blocks Phase 3** |
| F-02 | Spec: "PIB is the single ingestion point for GoI announcements". In practice PIB does not carry DGFT, CBDT, CBIC, IMD forecasts, IRDAI, PFRDA, CERC, PNGRB, PPAC, CDSCO, NPCI, SIAM, FADA, or Power/DoT orders (scan of 263 English PIB releases, 26 Sep–1 Oct). | Keep PIB as the primary channel where it does carry the source (GST Council, CGA, NITI, ECI, TRAI echo, Cabinet). Add direct adapters for the rest, per PROPOSAL §d. | needs sign-off |
| F-03 | **FOMC presser transcripts post 8–13 days after the meeting** (by file Last-Modified, last 4 meetings). The spec assumes the official transcript is available for Stage 2. | Run faster-whisper on the Fed's own presser video, label the output "Machine transcription, pending official transcript", then follow up when the PDF posts. This is the same path as RBI. Re-check live at the 28 Oct FOMC. | needs sign-off |
| F-04 | Scrapers need approval. The prompt bars HTML/undocumented-endpoint adapters without your OK. | List and per-source recommendation in PROPOSAL §d. | needs approval |
| F-05 | Sources reachable only in a headless browser: NPCI (Akamai), ADP (rendered by JavaScript), CBDT (403). | Do not run a browser in production. NPCI: manual once a month. ADP: link only. CBDT: PIB echo plus manual. | needs sign-off |
| F-06 | New dependencies outside the approved list: `curl_cffi` (BSE notices, already used in-house), `truststore` (fixes broken TLS chains on dea.gov.in, cbic.gov.in, egazette.gov.in and cercind.gov.in through the OS trust store). | Approve both. Never use `verify=False`. | needs approval |
| F-07 | FDA warning letters post weeks to months after issue. The spec puts them in Tier 1 real-time. | Move them to the India EOD digest. Keep FDA press RSS approvals in Tier 1. | needs sign-off |
| F-08 | US timing. The spec's default sends Tier 1 in a 07:00 IST wrap and only FOMC in real time. Two classes look too slow for that: (a) White House tariff proclamations and executive orders that hit India or the book, and (b) NFP and CPI at 18:00 IST, while the India desk is still online. | Make (a) real-time on a keyword or watchlist match, and make (b) real-time as an opt-in toggle in `delivery.yaml`. The spec asks us to flag disagreement with its default; this is that flag. | needs sign-off |
| F-09 | Spec and prompt conflict: the spec sends Tier 2 to an EOD digest, while the prompt puts US Tier 2 in the 07:00 IST morning wrap. | Use the morning wrap. US Tier 2 prints land during IST night, so a 19:30 IST EOD would carry them almost a day late. Logged in PROGRESS. | needs sign-off |
| F-10 | Recipients, SMTP, Supabase project, Anthropic key, FMP key, BQL host, deploy host (systemd means Linux, while BQL needs the Windows terminal box). | These need your input. See PROPOSAL §g. | **blocks Phase 1 delivery/db** |
| F-11 | CBO and the congress.gov web pages are behind bot walls (DataDome, Cloudflare). | CBO: manual weekly (it is Tier 3), or re-test from a GitHub or Azure runner, since egress can differ. Appropriations status: Congress API plus manual CR dates. | needs sign-off |
| F-12 | Drops and link-only sources: Finance Commission (nothing scheduled, page shows the wrong content), eGazette (no feed, broken TLS), ISM (reCAPTCHA), Case-Shiller (403). | FC: manual. Gazette: drop. ISM: link only, no number. Case-Shiller: link only, or a FRED mirror figure tagged "via FRED (mirror)" if you accept mirrors in Tier 2. | needs sign-off |
| F-13 | Watchlist and Indian pharma facility list are not provided. | You supply `config/watchlist.csv` and the pharma/facility list. I'll seed the facility list from openFDA enforcement records for India. | needed by Phase 4 |

## Technical hazards (handled in design, no decision needed)

| id | hazard | handling |
|---|---|---|
| T-01 | BLS publishes its RSS (pubDate ~07:50) and PDFs (Last-Modified ~07:07) before the 08:30 release, and sometimes replaces the PDF afterwards. | The release time is the official time from the ICS or the embargo line, plus our first-seen time. Hash the first copy we see. |
| T-02 | Fed RSS pubDate, Census `indicator.xml` pubDate and BEA RSS pubDate all equal the *scheduled* time. | Record both the source time and the first-seen time, as the prompt requires. |
| T-03 | RBI RSS pubDate has no timezone (it is IST), so feedparser returns None. | Parse it explicitly as Asia/Kolkata, with a unit test. |
| T-04 | Several feeds hold ≤20 items: PIB RSS 20 (about 1 h on busy days), RBI 10 (about 1 day), TRAI 10, NITI 10. | Poll intervals are sized to feed depth. For PIB, use `allRel.aspx` to fill gaps. For RBI, backfill by sequential prid. |
| T-05 | Some responses are HTTP 200 but contain an error: rbidocs bot challenge, Census `missing_key.html`, BEA body error, BLS Akamai pages. | Validators check content type and magic bytes (`%PDF`), not just status. Health rules count these as errors. |
| T-06 | Stale official feeds: SEBI RSS, EIA TWIP RSS (2025), SEC rules RSS (2023), Boston/Atlanta Fed, TIC `mfh.txt`. | Every stream source gets a "newest item older than N" staleness rule. |
| T-07 | Decimal hazard: a naive sentence split turned "5.25 per cent" into "5." in the probe. | Extraction is token-based. The redline sentence splitter must protect decimals. Covered by tests. |
| T-08 | Some files are re-uploaded after release (IIP, RBI Governor's statement, DGFT RoDTEP, Census marts, BLS Empsit). | Store the first-seen sha256. A later change is a new `documents` row linked to its parent. |
| T-09 | MoSPI and OEA files appear before the embargo lifts. | Never alert before the official release time, even if the file is already visible. |
| T-10 | Scanned PDFs (some DGFT and OEA archive files). | If there is no text layer, send the link flagged `EXTRACTION FAILED`. No OCR numbers without validation (OCR is out of the current stack). |
| T-11 | Undocumented JSON endpoints (Fed calendar.json, MoSPI POST, NIC WP-JSON, ECI encrypted params, CDSCO NSQ, NSE). | Each gets a schema-drift check against fixtures plus a freshness check. |
| T-12 | Federal Register public inspection is blank from 00:00 to 08:45 ET, and the agency filter is ignored when combined with a date. | Filter on our side. Don't alert on the expected blank window. |
| T-13 | Calendars run out: the BLS ICS ends 2026-12-30 and the BEA ICS ends 2026-12-23. | Add a "calendar horizon < 60 days" health alert. |
| T-14 | No June 2026 RBI SDRP was published. | Stage 2 "diff vs prior" handles a missing prior document. |
| T-15 | NSE circulars API returns `null` file links on some rows. | Fall back to `{DEPT}{num}.pdf`, or skip and log. |
| T-16 | Treasury tail vs WI: no primary source for the when-issued yield. | Omit, per the prompt. |

## Unverified (seen but not fetched, or could not be measured)

- Per-governor Fed RSS feeds, USTR and White House item pages, TreasuryDirect `A_*.xml` announcement files.
- PIB echo for MoSPI CPI/IIP/GDP, OEA, IMD forecasts, CBDT and CBIC. Will confirm live: CPI on 12 Oct and WPI on 14 Oct.
- Historical RBI MPC release-to-RSS lag (the Wayback Machine was down). Will measure live on **7 Oct 2026**.
- Historical BLS lag (BLS stamps files early). Will measure live on **2 Oct 2026** (Employment Situation).
- ISM's PR Newswire distribution, the FDA Data Dashboard API, a second SEBI board-outcome example and a second CGA PIB example.
