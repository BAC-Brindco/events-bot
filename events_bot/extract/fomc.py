"""FOMC documents -> extractions: statement, implementation note, SEP table 1.

Both statement formats are handled:
- through Jan 2026: "Voting for the monetary policy action were A, Chair; B; ... Voting against
  this action were X, who preferred ...".
- from mid-2026: "The Federal Open Market Committee approved the following statement for release
  by a 9 – 3 vote:" plus a "Voting against" paragraph naming only the dissenters.
Names of members voting for are extracted only when the statement lists them; they are never
inferred from the roster.
"""
from __future__ import annotations

import re
from datetime import date

from selectolax.parser import HTMLParser

from .base import _HY, Extraction, find, html_text, span, table_text

NUM = rf"\d+(?:\.\d+)?(?:\s*{_HY}\s*\d+/\d+)?|\d+/\d+"   # 3.90 | 3-3/4 | 3‑1/2 | 1/4 | 4
MONTHS = ("January February March April May June July August September October November December").split()


def statement_text(content: bytes) -> str:
    return html_text(content, "#article")


def statement_body(text: str) -> str:
    """The statement proper: paragraphs after the release-time line, up to the media contact."""
    lines = text.split("\n")
    start = next((i + 1 for i, l in enumerate(lines) if l.startswith("For release at")), 0)
    end = next((i for i, l in enumerate(lines) if l.startswith("For media inquiries")), len(lines))
    return "\n".join(lines[start:end])


def impl_note_text(content: bytes) -> str:
    return html_text(content, "#content")


def long_date(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


# ---- statement ---------------------------------------------------------------

_DECISION = re.compile(
    rf"decided to (?P<action>raise|lower|maintain) the target range for the federal funds rate"
    rf"(?: by (?P<chg>{NUM}) percentage point)? (?:to|at) (?P<lo>{NUM}) to (?P<hi>{NUM}) percent")
_TALLY = re.compile(r"approved the following statement for release by a (?P<f>\d+) [–\-] (?P<a>\d+) vote")
_FOR = re.compile(r"Voting for the monetary policy action were (?P<names>.+?)\.(?= Voting against|$)", re.M)
_AGAINST = re.compile(r"Voting against (?:this|the monetary policy) action (?:was|were) (?P<body>.+)$", re.M)
_TITLE = re.compile(r",\s*(?:Vice\s+)?Chair\b")


def _split_names(text: str, base: int) -> list[tuple[str, int, int]]:
    """'A, Chair; B; and C' or 'A, B, and C' -> [(name, start, end)] with absolute offsets."""
    out = []
    for m in re.finditer(r"[A-Z][A-Za-z'\-]+(?: [A-Z]\.)?(?: [A-Z][A-Za-z'\-]+)+", text):
        name = m.group(0)
        if name in ("Vice Chair", "Chair"):
            continue
        out.append((name, base + m.start(), base + m.end()))
    return out


def parse_statement(content: bytes, meeting: date) -> tuple[str, list[Extraction]]:
    text = statement_text(content)
    period = meeting.isoformat()
    ex: list[Extraction] = []
    d = find(text, re.escape(long_date(meeting)), "fomc.doc_date", group=0, norm=False, doc="statement",
             period=period, unit="text")
    if d:
        ex.append(d)
    m = _DECISION.search(text)
    if m:
        ex.append(span(text, m.start("action"), m.end("action"), "fomc.action", unit="text", norm=False,
                       doc="statement", period=period))
        if m.group("chg"):
            ex.append(span(text, m.start("chg"), m.end("chg"), "fomc.change_pp", unit="pp",
                           doc="statement", period=period))
        ex.append(span(text, m.start("lo"), m.end("lo"), "fomc.target_low", unit="percent",
                       doc="statement", period=period))
        ex.append(span(text, m.start("hi"), m.end("hi"), "fomc.target_high", unit="percent",
                       doc="statement", period=period))
    t = _TALLY.search(text)
    if t:
        ex.append(span(text, t.start("f"), t.end("f"), "fomc.votes_for_count", unit="count",
                       doc="statement", period=period))
        ex.append(span(text, t.start("a"), t.end("a"), "fomc.votes_against_count", unit="count",
                       doc="statement", period=period))
    f = _FOR.search(text)
    if f:
        for name, a, b in _split_names(_TITLE.sub(lambda x: " " * len(x.group(0)), f.group("names")),
                                       f.start("names")):
            ex.append(span(text, a, b, "fomc.vote_for", unit="text", norm=False, doc="statement", period=period))
    ag = _AGAINST.search(text)
    if ag:
        body, base = ag.group("body"), ag.start("body")
        # segments: "<names>, who preferred <pref>" separated by "; "
        for seg in re.finditer(r"(?:and )?(?P<names>.+?), who preferred (?P<pref>.+?)(?:;\s|\.$|$)", body):
            pref_a, pref_b = base + seg.start("pref"), base + seg.end("pref")
            for name, a, b in _split_names(seg.group("names"), base + seg.start("names")):
                e = span(text, a, b, "fomc.vote_against", unit="text", norm=False, doc="statement", period=period)
                e.meta["preferred"] = text[pref_a:pref_b]
                ex.append(e)
                p = span(text, pref_a, pref_b, "fomc.dissent_pref", unit="text", norm=False,
                         doc="statement", period=period)
                p.meta["member"] = name
                ex.append(p)
    return text, ex


# ---- implementation note ------------------------------------------------------------

_IMPL = [
    ("fomc.iorb", rf"interest rate paid on reserve balances (?:to|at) (?:the current level of )?({NUM}) percent", "percent"),
    ("fomc.srp_rate", rf"standing overnight repurchase agreement operations at (?:a|an) (?:minimum bid )?rate of ({NUM}) percent", "percent"),
    ("fomc.onrrp_rate", rf"standing overnight reverse repurchase agreement operations at an offering rate of ({NUM}) percent", "percent"),
    ("fomc.onrrp_cap_bn", r"per-counterparty limit of \$(\d+(?:\.\d+)?) billion per day", "usd_bn"),
    ("fomc.primary_credit", rf"primary credit rate (?:to|at|at the existing level of) ({NUM}) percent", "percent"),
    ("fomc.directive_target_low", rf"target range of ({NUM}) to (?:{NUM}) percent", "percent"),
    ("fomc.directive_target_high", rf"target range of (?:{NUM}) to ({NUM}) percent", "percent"),
    ("fomc.runoff_cap_treasury_bn", r"Treasury securities[^.\n]*?(?:cap|in excess of)[^.\n]*?\$(\d+(?:\.\d+)?) billion per month", "usd_bn"),
    ("fomc.runoff_cap_agency_bn", r"agency (?:debt and agency )?mortgage-backed securities[^.\n]*?(?:cap|in excess of)[^.\n]*?\$(\d+(?:\.\d+)?) billion per month", "usd_bn"),
]


def parse_impl_note(content: bytes, meeting: date) -> tuple[str, list[Extraction]]:
    text = impl_note_text(content)
    period = meeting.isoformat()
    ex: list[Extraction] = []
    d = find(text, r"Implementation Note issued (" + re.escape(long_date(meeting)) + ")", "fomc.impl_doc_date",
             norm=False, doc="impl", period=period, unit="text")
    if d:
        ex.append(d)
    for field, pat, unit in _IMPL:
        e = find(text, pat, field, unit=unit, doc="impl", period=period)
        if e:
            ex.append(e)
    # The balance-sheet directive lines, verbatim, for the redline/Stage 2 (no numbers derived from them).
    for m in re.finditer(r"^(?:Increase|Reduce|Roll over|Reinvest|When appropriate, increase)[^\n]*$", text, re.M):
        ex.append(span(text, m.start(), m.end(), "fomc.balance_sheet_line", unit="text", norm=False,
                       doc="impl", period=period))
    return text, ex


# ---- SEP (projection table 1) -----------------------------------------------------------

SEP_VARS = {
    "Change in real GDP": "gdp",
    "Unemployment rate": "unemployment",
    "PCE inflation": "pce",
    "Core PCE inflation": "core_pce",
    "Federal funds rate": "fed_funds",
}


def sep_rows(content: bytes) -> list[list[str]]:
    tree = HTMLParser(content.decode("utf-8-sig", "replace"))
    t = tree.css_first("table")
    if t is None:
        raise ValueError("no table in SEP page")
    rows = []
    for tr in t.css("tr"):
        rows.append([re.sub(r"\s+", " ", c.text(strip=True)) for c in tr.css("th, td")])
    return rows


def parse_sep(content: bytes, meeting: date) -> tuple[str, list[Extraction]]:
    """Medians only (the Phase 2 field list), current and the table's own prior-projection row."""
    rows = sep_rows(content)
    text = table_text(rows)
    period = meeting.isoformat()
    years: list[str] | None = None
    ex: list[Extraction] = []
    # absolute offset of each line in `text`
    offs, pos = [], 0
    for r in rows:
        offs.append(pos)
        pos += len(" | ".join(r)) + 1
    current: str | None = None
    for i, r in enumerate(rows):
        if years is None and r and re.fullmatch(r"\d{4}", r[0]):
            # the header row repeats the year set for median / central tendency / range
            years = r[: r.index("Longer run") + 1] if "Longer run" in r else r
            years = ["longer_run" if c == "Longer run" else c for c in years]
            continue
        if not r or years is None:
            continue
        label = re.sub(r"\d+$", "", r[0]).strip()       # footnote digits: "Core PCE inflation4"
        if label in SEP_VARS:
            current, kind = SEP_VARS[label], "median"
        elif current and re.fullmatch(r"[A-Z][a-z]+ projection", label):
            kind = "prior_median"
        else:
            continue
        cells = r[1:1 + len(years)]
        # cell k starts after "label | c1 | ... | c(k-1) | "
        col = offs[i] + len(r[0]) + 3
        for y, c in zip(years, cells):
            if c:
                e = span(text, col, col + len(c), f"sep.{kind}.{current}.{y}", unit="percent",
                         doc="sep", period=period)
                if kind == "prior_median":
                    e.meta["label"] = label
                ex.append(e)
            col += len(c) + 3
    return text, ex


# ---- cross-document checks -------------------------------------------------------------

def consistency(ex: list[Extraction], prior: list[Extraction] | None = None) -> list[str]:
    """Mark extractions that disagree across the meeting's documents. Returns the problems."""
    from .validator import check_large_move, first, good

    problems: list[str] = []

    def bad(e: Extraction | None, why: str) -> None:
        if e is not None:
            e.validation_error = (e.validation_error + "; " if e.validation_error else "") + why
        problems.append(why)

    for side in ("low", "high"):
        s, d = good(ex, f"fomc.target_{side}"), good(ex, f"fomc.directive_target_{side}")
        if s and d and s.value_norm != d.value_norm:
            bad(s, f"statement target_{side} {s.value_text} != directive {d.value_text}")
    lo, hi, iorb = good(ex, "fomc.target_low"), good(ex, "fomc.target_high"), good(ex, "fomc.iorb")
    if lo and hi and iorb and not (lo.value_norm <= iorb.value_norm <= hi.value_norm):
        bad(iorb, f"IORB {iorb.value_text} outside target range")
    n_against = good(ex, "fomc.votes_against_count")
    named = [e for e in ex if e.field == "fomc.vote_against" and e.ok]
    if n_against is not None and named and len(named) != n_against.value_norm:
        bad(n_against, f"{len(named)} dissenters named but tally says {n_against.value_text}")
    if prior:
        chg = good(ex, "fomc.change_pp")
        for side in ("low", "high"):
            new, old = first(ex, f"fomc.target_{side}"), first(prior, f"fomc.target_{side}")
            if new is not None and old is not None:
                before = new.validation_error
                check_large_move(new, old, chg)
                if new.validation_error != before:
                    problems.append(new.validation_error or "")
    return problems
