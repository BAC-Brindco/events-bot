# Economic Events Bot: Consolidated Spec

This consolidated spec supersedes all earlier mails. Build against it.

## What it is

An internal bot that does three things:

1. Maintains a forward calendar of scheduled policy and economic events in India and the US.
2. Monitors official channels on event day.
3. Pushes structured updates in two stages.

India and US are parallel pipelines with the same architecture. US is a first-class track, not India commentary.

## Output: two stages per Tier 1 event

### Stage 1: release alert (within about 15 min of release)

- Key numbers extracted VERBATIM from the source document: rate decision, vote split, print vs prior
- Source URL plus release timestamp
- A 5 to 8 bullet summary covering what changed and the immediate takeaways

### Stage 2: post-event summary (within 2 to 3 hrs of full event close)

Full event close means after the presser, governor's statement or briefing.

1. **Decision or outcome.** Every key number, verbatim from the primary documents.
2. **Diff vs prior event.** This includes three parts:
   - a statement redline (old vs new wording)
   - vote split changes
   - revised projections

   This is mechanical to build for FOMC and MPC, and high value.
3. **Discussion points.** Themes from the presser or Q&A:
   - repeated questions
   - pushback
   - anything said beyond the written statement

   Use official transcripts where available. Transcribe the video if needed.
4. **Market reaction.** One line, tagged as market data. It gives the move in the relevant rate, index or currency from release time to summary time.

### Stage 2 sourcing is strict

- Primary sources only: statements, transcripts, published projections.
- No media coverage in the body. Media is permitted only for the market reaction line, and that line must be tagged.
- If a transcript is not out yet, send the summary without discussion points and follow up when it posts. Don't wait. Don't fabricate.

## Hard rules (both stages, all tiers)

- **No model-generated numbers.** Every figure must be extracted from the source document. If extraction fails, send the raw link flagged. A wrong repo rate in our inbox is worse than no bot.
- **Every output carries the source URL and timestamp.**

## Sources: India

### Tier 1 (real-time)

- **PIB.** Cabinet and CCEA decisions.
  - PIB is the single ingestion point for all GoI announcements. There are no separate scrapers for pmindia.gov.in or the ministries.
  - PIB pushes 100+ releases a day, so filtering by ministry tag plus keywords is a core build task. Seed keywords: MSP, subsidy, PLI, export, duty, disinvestment, FDI.
  - The build team owns expanding the keyword list as misses show up.
- **RBI.** MPC, circulars, master directions, draft regulations, bulletin.
- **SEBI.** Board outcomes, circulars, consultation papers.
- **GST Council.** Meeting outcomes.
- **MoF/DEA.** Borrowing calendar, Budget documents, CBDT/CBIC notifications.
- **DGFT.** Export bans and restrictions, duty tweaks. Historically these are the most abrupt market movers.
- **MoSPI/NSO.** CPI, IIP, GDP.
- **Office of Economic Adviser.** WPI, core sector.
- **CGA.** Monthly fiscal deficit.
- **IMD.** Monsoon forecasts.
- **NSE/BSE circulars.** Index changes, ASM/GSM additions. These are directly relevant to the SMID book.

### Tier 2 (EOD digest)

TRAI, IRDAI, PFRDA, NPCI, CERC/Power, PNGRB/PPAC, DoT, MeitY, CDSCO, and SIAM/FADA auto sales.

### Tier 3 (weekly digest)

NITI Aayog, PRS Legislative (bill tracking), Election Commission, Finance Commission.

## Sources: US

### Tier 1 (real-time)

- **Fed.** FOMC decisions, minutes, SEP/dot plot, Powell pressers, governor and regional president speeches (from the public calendar), and the Beige Book on release days.
- **BLS.** NFP, CPI, PPI, JOLTS.
- **BEA.** GDP, PCE.
- **DOL.** Weekly jobless claims.
- **US Treasury.** Quarterly refunding, auction results.
- **Census.** Retail sales, durable goods.
- **White House/USTR.** Executive orders, tariff actions.
- **Congress fiscal events.** Debt ceiling, shutdown deadlines, major bills.
- **US FDA.** Approvals and warning letters, covering US names and Indian facilities.

### Tier 2 (EOD digest)

ISM PMIs, UMich and Conference Board sentiment, ADP, housing (starts and permits, Case-Shiller, NAHB), SEC rulemaking and enforcement, Commerce/BIS export controls, EIA inventories.

### Tier 3 (weekly digest)

CBO projections, Treasury TIC flows.

### US timing

Most US Tier 1 releases land between 6 PM and midnight IST.

- **Default:** a 7 AM IST "US morning wrap" covering the prior session.
- **FOMC days:** true real-time.
- Flag any disagreement with this default.

## Delivery

- Email to the three recipients to start. Slack or Telegram come later.
- Three cadences:
  - real-time (Tier 1)
  - EOD digest (Tier 2)
  - weekly digest (Tier 3)

## Build order

1. RBI MPC and FOMC, both stages. These two cover most of the value.
2. US Tier 1 data prints from BLS and BEA. They have clean release APIs and are the easiest wins.
3. PIB/Cabinet with filters.
4. Remaining sources by tier.

## Before you start, send

- (a) the proposed stack
- (b) the feed you will use for each source. Prefer official RSS or APIs over scraping, because scrapers break silently and we miss a policy day.
- (c) a realistic timeline for each build phase
- (d) any source without a reliable feed. Flag it. Don't build a fragile scraper around it.
