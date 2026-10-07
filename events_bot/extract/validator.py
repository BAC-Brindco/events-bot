"""Re-check every extraction before it is used (BUILD_PROMPT §6).

1. Verbatim: the value must sit at its stored location in the document text.
2. Bounds: per unit. Policy rates 0-20 %; a rate move > 100 bp must be stated in the source.
3. Period: the document must be for the expected period (catches stale/cached pages).

A failed extraction keeps its row (for audit) but is never rendered as a number.
"""
from __future__ import annotations

from decimal import Decimal

from .base import Extraction

RATE_UNITS = {"percent"}
RATE_BOUNDS = (Decimal(0), Decimal(20))
GROWTH_BOUNDS = (Decimal(-60), Decimal(100))     # y-o-y growth / inflation prints (unit "growth_pct")


def validate(ex: Extraction, text: str, *, expected_period: str | None = None) -> Extraction:
    errs: list[str] = []
    if ex.char_start is None or ex.char_end is None:
        errs.append("no location")
    elif text[ex.char_start:ex.char_end] != ex.value_text:
        errs.append(f"not verbatim at {ex.char_start}:{ex.char_end}")
    if ex.unit in RATE_UNITS and ex.value_norm is not None:
        lo, hi = RATE_BOUNDS
        if not (lo <= ex.value_norm <= hi):
            errs.append(f"rate {ex.value_norm} outside {lo}-{hi}")
    if ex.unit == "growth_pct" and ex.value_norm is not None:
        lo, hi = GROWTH_BOUNDS
        if not (lo <= ex.value_norm <= hi):
            errs.append(f"growth {ex.value_norm} outside {lo}-{hi}")
    if ex.unit is not None and ex.value_norm is None and ex.unit != "text":
        errs.append("unparseable number")
    if expected_period is not None and ex.period is not None and ex.period != expected_period:
        errs.append(f"period {ex.period} != expected {expected_period}")
    ex.validated = True
    ex.validation_error = "; ".join(errs) or None
    return ex


def validate_all(exs: list[Extraction], texts: dict[str, str], *, expected_period: str | None = None) -> list[Extraction]:
    for ex in exs:
        validate(ex, texts[ex.doc], expected_period=expected_period)
    return exs


def check_large_move(new: Extraction, prior: Extraction, stated_change: Extraction | None,
                     *, limit: Decimal = Decimal("1.00")) -> None:
    """A policy-rate move over `limit` points passes only if the source itself states that size."""
    if not (new.ok and prior.ok) or new.value_norm is None or prior.value_norm is None:
        return
    move = abs(new.value_norm - prior.value_norm)
    if move <= limit:
        return
    if stated_change is not None and stated_change.ok and stated_change.value_norm == move:
        return
    new.validation_error = f"move of {move} pts vs prior exceeds {limit} and is not stated in the source"


def first(exs: list[Extraction], field: str) -> Extraction | None:
    return next((e for e in exs if e.field == field), None)


def good(exs: list[Extraction], field: str) -> Extraction | None:
    e = first(exs, field)
    return e if e is not None and e.ok else None
