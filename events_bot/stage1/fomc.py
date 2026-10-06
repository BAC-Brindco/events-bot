"""FOMC Stage 1 (release alert). Pure: validated extractions in, Message out.

Every number shown is an extraction's exact source string. A field that failed
validation is shown as "EXTRACTION FAILED" with the source link, never as a number.
Bullets are deterministic sentences assembled from extractions (no LLM yet; when the
Anthropic key arrives, LLM bullets go through the number guard and fall back to these).
"""
from __future__ import annotations

from datetime import datetime

from ..core.models import Message
from ..core.timeutil import fmt_ist
from ..extract.base import Extraction
from ..extract.validator import first, good
from . import render
from .render import Doc, Table

__all__ = ["Doc", "build", "headline", "vote_line", "FAILED", "SEP_LABELS"]

SEP_LABELS = {"gdp": "Real GDP", "unemployment": "Unemployment rate", "pce": "PCE inflation",
              "core_pce": "Core PCE inflation", "fed_funds": "Fed funds rate"}
FAILED = "EXTRACTION FAILED"
SETTINGS = (("fomc.iorb", "Interest on reserve balances", " percent"),
            ("fomc.onrrp_rate", "ON RRP offering rate", " percent"),
            ("fomc.onrrp_cap_bn", "ON RRP per-counterparty limit ($ billion per day)", ""),
            ("fomc.srp_rate", "Standing repo rate", " percent"),
            ("fomc.primary_credit", "Primary credit rate", " percent"),
            ("fomc.runoff_cap_treasury_bn", "Treasury runoff cap ($ billion per month)", ""),
            ("fomc.runoff_cap_agency_bn", "Agency MBS runoff cap ($ billion per month)", ""))


def _v(ex: list[Extraction], field: str, suffix: str = "") -> str:
    e = first(ex, field)
    if e is None:
        return ""
    return e.value_text + suffix if e.ok else FAILED


def headline(ex: list[Extraction]) -> str:
    act, lo, hi = good(ex, "fomc.action"), good(ex, "fomc.target_low"), good(ex, "fomc.target_high")
    if not (act and lo and hi):
        return FAILED
    chg = good(ex, "fomc.change_pp")
    verb = {"raise": "Raises", "lower": "Lowers", "maintain": "Holds"}[act.value_text]
    if act.value_text == "maintain":
        return f"{verb} target range at {lo.value_text} to {hi.value_text} percent"
    by = f" by {chg.value_text} pp" if chg else ""
    return f"{verb} target range{by} to {lo.value_text} to {hi.value_text} percent"


def vote_line(ex: list[Extraction]) -> tuple[str, str]:
    f, a = good(ex, "fomc.votes_for_count"), good(ex, "fomc.votes_against_count")
    if f and a:
        return f"{f.value_text} – {a.value_text}", "tally as stated"
    fors = [e for e in ex if e.field == "fomc.vote_for" and e.ok]
    against = [e for e in ex if e.field == "fomc.vote_against" and e.ok]
    if fors:
        return f"{len(fors)} – {len(against)}", "computed: count of members named"
    return FAILED, ""


def dissent_bullets(ex: list[Extraction]) -> list[str]:
    groups: dict[str, list[str]] = {}
    for e in ex:
        if e.field == "fomc.vote_against" and e.ok:
            groups.setdefault(e.meta["preferred"], []).append(e.value_text)
    return [f"Dissent: {', '.join(names)} preferred {pref}." for pref, names in groups.items()]


def sep_table(ex: list[Extraction]) -> Table | None:
    years: list[str] = []
    rows: dict[str, dict] = {}
    for e in ex:
        if not e.field.startswith("sep.median."):
            continue
        _, _, var, year = e.field.split(".")
        if year not in years:
            years.append(year)
        rows.setdefault(var, {})[year] = e.value_text if e.ok else FAILED
    if not rows:
        return None
    return Table("Summary of Economic Projections, medians",
                 ["Longer run" if y == "longer_run" else y for y in years],
                 [{"label": SEP_LABELS.get(v, v), "cells": [c.get(y, "") for y in years]} for v, c in rows.items()])


def build(meeting_ref: str, ex: list[Extraction], docs: list[Doc], *, release_at: datetime,
          first_seen: datetime, mode: str = "live") -> Message:
    head = headline(ex)
    vote, vote_note = vote_line(ex)
    key = [{"label": "Vote", "value_text": vote, "note": vote_note}]
    key += [{"label": label, "value_text": v} for field, label, suffix in SETTINGS if (v := _v(ex, field, suffix))]
    bullets = dissent_bullets(ex)
    against = good(ex, "fomc.votes_against_count")
    if not bullets and against is not None and against.value_norm == 0:
        bullets.append("No dissents.")
    bullets += [f"Balance sheet: {e.value_text}" for e in ex if e.field == "fomc.balance_sheet_line" and e.ok][:2]
    sep = sep_table(ex)
    return render.stage1(ref=meeting_ref, subject=f"[FED] FOMC decision | {head}", source_tag="FED",
                         event_name="FOMC decision", title=head, key=key, tables=[sep] if sep else [],
                         bullets=bullets[:8], source_time=fmt_ist(release_at) + " (scheduled 14:00 ET)",
                         first_seen=fmt_ist(first_seen), docs=docs,
                         failed=[f"{e.field}: {e.validation_error}" for e in ex if not e.ok], mode=mode)
