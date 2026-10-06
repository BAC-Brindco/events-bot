"""Number guard for all LLM text (BUILD_PROMPT §7).

check_bullets() takes candidate bullets plus the validated extractions for the item
(and for the prior item used in a diff) and returns only the bullets whose every
numeric token is backed by an extraction:

1. Numeric tokens: numbers, fractions (3-3/4), percents, bp, years, fiscal years
   (2026-27) and quarters (Q3, Q3:2026-27).
2. A number must equal (as a decimal, no rounding) a validated extraction's value. If a
   period token precedes it in the same clause, the extraction must be for that period
   (catches "wrong quarter" / "wrong year"). After "from / prior / previously / was / earlier"
   the number must come from the prior item's extractions, otherwise from the current item's
   (catches a prior value presented as current).
3. Years, fiscal years and quarters must appear in an extraction's field/period or value.
4. Quoted spans ("...") must be exact substrings of the source text.
If more than half of the bullets are dropped, the caller falls back to deterministic text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .base import HYPHENS, Extraction, parse_number

_HY = f"[{HYPHENS}]"
_TOKEN = re.compile(
    rf"(?P<q>\bQ[1-4](?::(?P<qfy>\d{{4}}{_HY}\d{{2}}))?\b)"
    rf"|(?P<fy>\b(?:FY\s?)?\d{{4}}{_HY}\d{{2}}\b)"
    rf"|(?P<year>\b(?:19|20)\d{{2}}\b(?!\.\d|\d))"
    rf"|(?P<num>\d+(?:\.\d+)?(?:\s*{_HY}\s*\d+/\d+)?(?:/\d+)?)(?P<unit>\s*(?:%|per\s?cent|percent|bps?\b|basis points))?")
_PRIOR_CUE = re.compile(r"\b(?:from|prior|previously|previous|was|were|earlier|last time|in (?:June|July|August|"
                        r"September|October|December|February|April|March|January|May|November))\s*$", re.I)
_CLAUSE_BREAK = re.compile(r"[;:.]\s|,\s(?:and|while|but)\s")
_QUOTE = re.compile(r"[\"“]([^\"”]{3,})[\"”]")


@dataclass
class GuardResult:
    kept: list[str] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)   # (bullet, reason)

    @property
    def fallback(self) -> bool:
        total = len(self.kept) + len(self.dropped)
        return total > 0 and len(self.dropped) * 2 > total


def _norm_fy(s: str) -> str:
    return re.sub(_HY, "-", s.replace("FY", "").strip())


def _periods_of(ex: Extraction) -> set[str]:
    """Period labels an extraction speaks for: 'Q2:2026-27' -> {'Q2', '2026-27', '2026', '2027', 'Q2:2026-27'}."""
    out: set[str] = set()
    for part in re.findall(rf"Q[1-4](?::\d{{4}}-\d{{2}})?|\d{{4}}-\d{{2}}|\b(?:19|20)\d{{2}}\b", ex.field + " " + (ex.period or "")):
        out.add(part)
        if ":" in part:
            q, fy = part.split(":")
            out |= {q, fy}
            part = fy
        if re.fullmatch(r"\d{4}-\d{2}", part):
            out |= {part[:4], "20" + part[5:]}
    if ex.unit == "text" and ex.value_text:
        out |= set(re.findall(r"\b(?:19|20)\d{2}\b", ex.value_text))
    return out


def _own_period(ex: Extraction) -> str | None:
    """The single period an extraction is *for*, from its field key: 'mpc.proj.cpi.Q2:2026-27' -> 'Q2:2026-27',
    'mpc.proj.cpi.FY2026-27' -> '2026-27', 'sep.median.pce.2027' -> '2027'. None for period-less fields."""
    last = ex.field.rsplit(".", 1)[-1]
    if re.fullmatch(r"Q[1-4]:\d{4}-\d{2}|\d{4}", last):
        return last
    if re.fullmatch(r"FY\d{4}-\d{2}", last):
        return last[2:]
    return None


def _period_ok(ex: Extraction, period: str | None) -> bool:
    own = _own_period(ex)
    if period is None or own is None:
        return True
    if own == period:
        return True
    return re.fullmatch(r"Q[1-4]", period) is not None and own.startswith(period + ":")


def _same_number(src: Extraction, value: Decimal, token: str) -> bool:
    """Equal value; for plain decimals also equal precision ('7' vs '7.0' is rounding drift)."""
    if src.value_norm is None or src.value_norm != value:
        return False
    if "/" in src.value_text or "/" in token:
        return True
    return src.value_norm.as_tuple().exponent == value.as_tuple().exponent


class NumberGuard:
    def __init__(self, current: list[Extraction], prior: list[Extraction] | None = None,
                 source_texts: list[str] | None = None):
        self.cur = [e for e in current if e.ok]
        self.pri = [e for e in (prior or []) if e.ok]
        self.sources = source_texts or []
        self.periods = set().union(*(_periods_of(e) for e in self.cur + self.pri)) if self.cur or self.pri else set()

    def _match(self, value: Decimal, token: str, pool: list[Extraction], period: str | None) -> bool:
        return any(_same_number(e, value, token) and _period_ok(e, period) for e in pool)

    def check_text(self, text: str) -> str | None:
        """None if every number in `text` is backed; else the reason it is not."""
        for q in _QUOTE.finditer(text):
            if not any(q.group(1) in s for s in self.sources):
                return f"quote not in source: {q.group(1)[:60]!r}"
        period: str | None = None
        last_break = 0
        for m in _TOKEN.finditer(text):
            # a clause break resets the period context
            if _CLAUSE_BREAK.search(text, last_break, m.start()):
                period = None
            last_break = m.end()
            if m.group("q"):
                tok = m.group("q")
                if tok not in self.periods and tok.split(":")[0] not in self.periods:
                    return f"quarter {tok} not in extractions"
                period = tok if ":" in tok else (tok + ":" + m.group("qfy") if m.group("qfy") else tok)
                if period not in self.periods:
                    period = tok.split(":")[0]
                continue
            if m.group("fy"):
                fy = _norm_fy(m.group("fy"))
                if fy not in self.periods:
                    return f"fiscal year {m.group('fy')} not in extractions"
                period = fy
                continue
            if m.group("year"):
                if m.group("year") not in self.periods:
                    return f"year {m.group('year')} not in extractions"
                continue
            value = parse_number(m.group("num"))
            if value is None:
                return f"unparseable number {m.group('num')!r}"
            prefix = text[max(0, m.start() - 40):m.start()]
            pool = self.pri if _PRIOR_CUE.search(prefix) else self.cur
            tok = m.group("num")
            if not self._match(value, tok, pool, period):
                if pool is self.cur and self._match(value, tok, self.pri, period):
                    return f"{tok} is the prior value presented as current"
                if period and self._match(value, tok, pool, None):
                    return f"{m.group('num')} does not belong to period {period}"
                return f"{m.group('num')} not in validated extractions"
        return None

    def check_bullets(self, bullets: list[str]) -> GuardResult:
        r = GuardResult()
        for b in bullets:
            why = self.check_text(b)
            if why is None:
                r.kept.append(b)
            else:
                r.dropped.append((b, why))
        return r
