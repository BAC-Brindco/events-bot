"""Desk scope (FLAGS F-29, user 2026-10-09): which stream items reach the desk, and how.

The desk uses this bot to track macro releases and reports that might affect the overall book. So:

  realtime   only the data prints with a validated print-vs-prior alert (MoSPI CPI/IIP/GDP, OEA WPI/ICI).
             FOMC and RBI MPC are scheduled sources and never pass through here.
  india_eod  the daily macro digest (18:30 IST, working days): a detailed write-up per item.
  drop       logged to filtered_items with rule 'out_of_scope' and the reason; still stored, still
             reviewable with `cli rejected`, never e-mailed.

Scope is decided after the source's own keyword filter / LLM triage and after enrichment, so it can use
the release page (PIB ministry, NSE Indices rows). Rules are title-based and deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.models import RawItem, SourceConfig

PRINT_KINDS = {"cpi", "iip", "gdp", "wpi", "ici"}

# Sources that never reach the desk e-mail (still polled, stored and logged).
DROPPED_SOURCES = {
    "bse_notices": "BSE notices are company corporate actions, not macro",
    "nse_surv": "ASM/GSM list changes are stock-specific surveillance",
    "cbdt": "CBDT notifications are mostly forms and procedure",
}

# ---- PIB: macro policy only ----------------------------------------------------------------
_PIB_KEEP = re.compile(
    r"\bGST\b|Goods and Services Tax|GST Council|\bCabinet\b|\bCCEA\b|"
    r"customs dut|excise dut|\bduty\b|\bduties\b|\btariff|anti-dumping|safeguard dut|"
    r"\bexports?\b|\bimports?\b|trade data|trade deficit|merchandise trade|foreign trade|"
    r"Free Trade|\bFTA\b|\bTEPA\b|trade agreement|"
    r"fiscal|Accounts of (?:the )?Union Government|\bbudget\b|market borrowing|borrowing programme|"
    r"disinvestment|\bdivestment\b|\bFDI\b|\bPLI\b|"
    r"\binflation\b|Consumer Price Index|Wholesale Price Index|\bGDP\b|Index of Industrial Production|"
    r"Eight Core|core industries|Index of Services Production|"
    r"\bMSP\b|Minimum Support Price|procurement of (?:paddy|wheat)|stock limit|"
    r"\bincome[- ]tax\b|direct tax collections?|tax collections?|"
    r"\bRBI\b|Reserve Bank|monetary policy|interest rate|"
    r"crude oil|fuel prices?|petrol|diesel|\bLPG\b|coal (?:production|supply)|power demand|"
    r"monsoon|kharif|rabi|sowing|food prices?|onion|pulses",
    re.I)
_PIB_DROP = re.compile(r"\bCCI approves\b|inaugurates|felicitat|chairs .*meeting of|lays foundation|"
                       r"visits?\b|addresses|interacts|meets\b|calls on|review meeting|workshop|conclave|"
                       r"\bsummit\b|webinar|exhibition|awareness|campaign", re.I)
_PIB_MINISTRIES = re.compile(r"Ministry of Finance|Ministry of Commerce|Cabinet|CCEA|"
                             r"Ministry of Statistics|Department of Economic Affairs", re.I)

# ---- RBI ---------------------------------------------------------------------------------
_RBI_MPC_DOCS = re.compile(r"Monetary Policy Statement|Resolution of the Monetary Policy Committee|"
                           r"Governor.s Statement|Statement on Developmental and Regulatory Policies", re.I)
_RBI_PR_KEEP = re.compile(
    r"\bDirections?\b|\bdraft\b|Master Direction|\bcircular\b|"
    r"Bulletin|Forward Looking Surveys|Consumer Confidence|Inflation Expectations|"
    r"Financial Stability Report|Annual Report|Report on Currency and Finance|"
    r"Monetary Policy Report|Sectoral Deployment of Bank Credit|"
    r"Balance of Payments|External Debt|Foreign Exchange Reserves|Trade in Services|"
    r"\brepo rate\b|"
    r"\bCRR\b|\bSLR\b|Cash Reserve Ratio|Statutory Liquidity Ratio|liquidity|"
    r"policy repo rate|change in (?:the )?(?:bank )?rates?|"
    r"\bFEMA\b|Foreign Exchange Management|\bECB\b|External Commercial Borrowing|"
    r"\bKYC\b|Know Your Customer|payment system|\bUPI\b|"
    r"discussion paper|framework|guidelines",
    re.I)
_RBI_PR_DROP = re.compile(r"auction|Money Market Operations|Reserve Money|\bVRR\b|\bVRRR\b|"
                          r"surrender .*Certificates? of Registration|cancels? .*Certificate|"
                          r"monetary penalty|imposes|Second Schedule|"
                          r"Foreign Exchange Turnover|Weekly Statistical Supplement|Lending and Deposit Rates|"
                          r"Sectoral Deployment of Credit by NBFC|"
                          r"exclusion of .* from the Second Schedule|inclusion of .* in the Second Schedule",
                          re.I)
# Directions for one narrow class of lender change no market-wide rule.
_RBI_NARROW = re.compile(r"\b(?:Payments? Banks?|Small Finance Banks?|Local Area Banks?|Regional Rural Banks?|"
                         r"Co-operative|Cooperative|All India Financial Institutions|AIFIs?|"
                         r"Asset Reconstruction Compan|Credit Information Compan)", re.I)

# ---- SEBI --------------------------------------------------------------------------------
_SEBI_PR_KEEP = re.compile(r"Key decisions taken in the SEBI Board Meeting|consultation paper|"
                           r"\bF&O\b|derivatives?|margin|settlement|\bFPIs?\b|Foreign Portfolio|"
                           r"mutual funds?|\bAIFs?\b|Alternative Investment|\bREITs?\b|\bInvITs?\b|"
                           r"\bIPOs?\b|listing|disclosure|framework|norms|regulations",
                           re.I)

# ---- IMD -----------------------------------------------------------------------------------
_IMD_KEEP = re.compile(r"monsoon|long range forecast|seasonal outlook|monthly outlook|rainfall forecast", re.I)

# ---- NSE Indices: only the two benchmark indices -----------------------------------------
BENCHMARK_INDICES = ("Nifty 50", "Nifty Bank")


@dataclass(frozen=True)
class Scope:
    route: str | None          # 'realtime' | 'india_eod' | None (dropped)
    reason: str

    @property
    def keep(self) -> bool:
        return self.route is not None


def _hay(it: RawItem) -> str:
    return f"{it.title} {it.summary or ''}"


def decide(cfg: SourceConfig, it: RawItem) -> Scope:
    sid = cfg.id
    meta = it.meta or {}
    if sid in DROPPED_SOURCES:
        return Scope(None, DROPPED_SOURCES[sid])

    if sid in ("mospi", "oea"):
        if meta.get("kind") in PRINT_KINDS:
            return Scope("realtime", f"data print: {meta['kind']}")
        return Scope("india_eod", "statistical release")

    if sid == "pib":
        if _PIB_DROP.search(it.title) and not re.search(r"\bCabinet\b|\bGST Council\b", it.title, re.I):
            return Scope(None, "PIB event / ceremony release")
        if _PIB_KEEP.search(_hay(it)):
            return Scope("india_eod", "PIB macro policy")
        if _PIB_MINISTRIES.search(meta.get("ministry") or "") and re.search(r"₹|crore|per cent|%", _hay(it)):
            return Scope("india_eod", f"PIB {meta.get('ministry')}")
        return Scope(None, "PIB release outside macro scope")

    if sid == "rbi_pr":
        if _RBI_MPC_DOCS.search(it.title):
            return Scope(None, "covered by the MPC alert")
        if _RBI_PR_DROP.search(it.title):
            return Scope(None, "RBI routine operation / entity-specific action")
        if _RBI_NARROW.search(it.title):
            return Scope(None, "RBI rule for one narrow class of lender")
        if _RBI_PR_KEEP.search(it.title):
            return Scope("india_eod", "RBI rule change / report")
        return Scope(None, "RBI press release outside macro scope")

    if sid == "rbi_notif":
        if _RBI_NARROW.search(it.title):
            return Scope(None, "RBI rule for one narrow class of lender")
        return Scope("india_eod", "RBI notification")

    if sid == "sebi_circ":
        return Scope("india_eod", "SEBI market-wide circular / consultation")

    if sid == "sebi_pr":
        if "board_outcome" in (meta.get("priority_tags") or []) or _SEBI_PR_KEEP.search(it.title):
            return Scope("india_eod", "SEBI market rule / board outcome")
        return Scope(None, "SEBI press release outside macro scope")

    if sid == "cbic":
        if "cbic:rate" in (meta.get("priority_tags") or []):
            return Scope("india_eod", "CBIC duty / GST rate notification")
        return Scope(None, "CBIC procedural notification")

    if sid == "imd":
        if _IMD_KEEP.search(it.title):
            return Scope("india_eod", "IMD monsoon / seasonal forecast")
        return Scope(None, "IMD release other than monsoon forecasts")

    if sid == "nifty_indices":
        rows = meta.get("rows")
        if rows is None:            # enrichment failed: decide on the title
            ok = any(re.search(rf"\b{re.escape(i)}\b", it.title, re.I) for i in BENCHMARK_INDICES)
        else:
            ok = any(r.get("index") in BENCHMARK_INDICES for r in rows)
        return Scope("india_eod", "benchmark index change") if ok else Scope(None, "not Nifty 50 / Nifty Bank")

    return Scope("default", "no desk-scope rule: the source's configured route")
