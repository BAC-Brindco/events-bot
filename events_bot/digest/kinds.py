"""The three digests, one engine (digest/run.py + digest/render.py).

  india_eod         Daily Macro Digest, 18:30 IST Mon-Fri (F-29)
  us_morning_wrap   US Morning Wrap, 07:00 IST Tue-Sat: the prior US session's Fed, Treasury, White House /
                    USTR, Congress and FDA items (F-09). US data prints go out as separate real-time alerts.
  weekly            Weekly Digest, Saturday 09:00 IST: India + US Tier 3 items and the forward calendar of
                    scheduled events for the next 14 days (PROPOSAL §e).

A source's section and issuer come from its sources.yaml entry (`section`, `issuer`); the sources that
predate those fields are mapped in render.SECTION_OF / ISSUER. Sections not listed in a kind's `sections`
are appended after them in alphabetical order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(frozen=True)
class DigestKind:
    route: str
    title: str                       # masthead
    subject: str                     # subject prefix, e.g. "[MACRO] Daily digest"
    scope: str                       # the "at a glance" scope sentence
    sections: tuple[str, ...]        # display order
    standfirst: dict[str, str] = field(default_factory=dict)
    max_age: timedelta = timedelta(days=4)     # older queued items are logged as stale, not mailed
    forward_days: int = 0                      # >0: add the forward calendar of scheduled events
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)  # IST weekdays the scheduled run sends on


INDIA_EOD = DigestKind(
    route="india_eod", title="Daily Macro Digest", subject="[MACRO] Daily digest",
    scope=("Government, RBI, SEBI, regulator, statistical, weather and benchmark-index releases that can move the "
           "macro picture. Each item quotes the release itself: the operative paragraphs, the key figures with the "
           "clause they appear in, who it applies to and when it takes effect. Data prints (CPI, IIP, GDP, WPI, core "
           "industries) and FOMC / RBI MPC decisions are sent as separate detailed alerts."),
    sections=("Government", "RBI", "SEBI", "Trade", "Data", "Regulators", "Industry data", "Weather",
              "Index changes"),
    standfirst={
        "Government": "Press Information Bureau releases and CBIC duty / GST rate notifications",
        "RBI": "Directions, circulars and reports of the Reserve Bank of India",
        "SEBI": "Market-wide circulars, consultation papers and Board decisions",
        "Trade": "DGFT notifications, public notices and trade notices",
        "Data": "Statistical releases (the CPI / IIP / GDP / WPI / core prints go out as separate alerts)",
        "Regulators": "Sector regulators: telecom, insurance, pensions, payments, power, oil and gas, drugs",
        "Industry data": "Monthly auto sales and other industry statistics",
        "Weather": "India Meteorological Department monsoon and seasonal forecasts",
        "Index changes": "Nifty 50 and Nifty Bank constituent changes",
    })

US_MORNING_WRAP = DigestKind(
    route="us_morning_wrap", title="US Morning Wrap", subject="[US] Morning wrap",
    scope=("The prior US session's Federal Reserve documents and speeches, Treasury auctions and financing, White "
           "House and USTR actions, Congress fiscal events and FDA actions. Each item quotes the release itself. "
           "US data prints (CPI, payrolls, PPI, JOLTS, GDP, PCE, retail sales, durables), FOMC decisions and minutes, "
           "and tariff actions that touch India are sent as separate real-time alerts."),
    sections=("Federal Reserve", "Trade & tariffs", "White House", "Treasury", "Congress & fiscal", "Data", "FDA"),
    standfirst={
        "Federal Reserve": "Beige Book, Board and Reserve Bank speeches and testimony",
        "Trade & tariffs": "USTR releases and tariff proclamations not already sent in real time",
        "White House": "Executive orders, proclamations and memoranda",
        "Treasury": "Auction results, quarterly refunding, debt limit",
        "Congress & fiscal": "Enacted laws, funding deadlines and federal operating status",
        "Data": "Weekly jobless claims and other US statistical releases",
        "FDA": "Drug approvals, warning letters and recalls (Indian facilities first)",
    },
    max_age=timedelta(days=4), weekdays=(1, 2, 3, 4, 5))

WEEKLY = DigestKind(
    route="weekly", title="Weekly Digest", subject="[WEEKLY] Digest",
    scope=("Tier 3 items from India and the US (policy think-tanks, legislation, elections, finance commissions) "
           "and the scheduled events of the next two weeks."),
    sections=("India policy", "Legislation", "Elections", "US fiscal", "Other"),
    standfirst={
        "India policy": "NITI Aayog and Finance Commission",
        "Legislation": "Bills tracked by PRS Legislative Research",
        "Elections": "Election Commission of India",
        "US fiscal": "Congressional Budget Office and other US Tier 3 releases",
    },
    max_age=timedelta(days=9), forward_days=14, weekdays=(5,))

KINDS = {k.route: k for k in (INDIA_EOD, US_MORNING_WRAP, WEEKLY)}


def ordered_sections(kind: DigestKind, present: set[str]) -> list[str]:
    return [s for s in kind.sections if s in present] + sorted(present - set(kind.sections))
