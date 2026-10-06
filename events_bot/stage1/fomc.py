"""FOMC Stage 1 (release alert). Pure: validated extractions in, Message out.

Every number shown is an extraction's exact source string. A field that failed
validation is shown as "EXTRACTION FAILED" with the source link, never as a number.
Bullets are deterministic sentences assembled from extractions (no LLM yet; when the
Anthropic key arrives, LLM bullets go through the number guard and fall back to these).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.models import Message
from ..core.timeutil import fmt_ist
from ..extract.base import Extraction
from ..extract.validator import first, good
from .build import _env

SEP_LABELS = {"gdp": "Real GDP", "unemployment": "Unemployment rate", "pce": "PCE inflation",
              "core_pce": "Core PCE inflation", "fed_funds": "Fed funds rate"}
FAILED = "EXTRACTION FAILED"


@dataclass
class Doc:
    name: str
    url: str
    fetched_at: datetime | None = None


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


def sep_table(ex: list[Extraction], kind: str = "median") -> tuple[list[str], list[dict]]:
    years: list[str] = []
    rows: dict[str, dict] = {}
    for e in ex:
        if not e.field.startswith(f"sep.{kind}."):
            continue
        _, _, var, year = e.field.split(".")
        if year not in years:
            years.append(year)
        rows.setdefault(var, {"label": SEP_LABELS.get(var, var), "cells": {}})["cells"][year] = (
            e.value_text if e.ok else FAILED)
    return years, list(rows.values())


def build(meeting_ref: str, ex: list[Extraction], docs: list[Doc], *, release_at: datetime,
          first_seen: datetime, mode: str = "live") -> Message:
    head = headline(ex)
    subject = f"[FED] FOMC decision | {head}"
    vote, vote_note = vote_line(ex)
    key = [{"label": "Decision", "value_text": head},
           {"label": "Vote", "value_text": vote, "note": vote_note}]
    for field, label, suffix in (("fomc.iorb", "Interest on reserve balances", " percent"),
                                 ("fomc.onrrp_rate", "ON RRP offering rate", " percent"),
                                 ("fomc.onrrp_cap_bn", "ON RRP per-counterparty limit ($ billion per day)", ""),
                                 ("fomc.srp_rate", "Standing repo rate", " percent"),
                                 ("fomc.primary_credit", "Primary credit rate", " percent"),
                                 ("fomc.runoff_cap_treasury_bn", "Treasury runoff cap ($ billion per month)", ""),
                                 ("fomc.runoff_cap_agency_bn", "Agency MBS runoff cap ($ billion per month)", "")):
        v = _v(ex, field, suffix)
        if v:
            key.append({"label": label, "value_text": v})
    bullets = dissent_bullets(ex)
    if not bullets and good(ex, "fomc.votes_against_count") and good(ex, "fomc.votes_against_count").value_norm == 0:
        bullets.append("No dissents.")
    bullets += [f"Balance sheet: {e.value_text}" for e in ex if e.field == "fomc.balance_sheet_line" and e.ok][:2]
    years, sep_rows = sep_table(ex)
    bad = [e for e in ex if not e.ok]
    ctx = dict(subject=subject, ref=meeting_ref, stage="stage1", mode=mode, source_tag="FED",
               event_name="FOMC decision", title=head, key_numbers=key, bullets=bullets[:8],
               sep_years=years, sep_rows=sep_rows,
               source_time=fmt_ist(release_at) + " (scheduled 14:00 ET)", first_seen=fmt_ist(first_seen),
               docs=docs, failed=[f"{e.field}: {e.validation_error}" for e in bad])
    body_html = _env.get_template("event_stage1.html.j2").render(**ctx)
    lines = [head, f"Vote: {vote}" + (f" ({vote_note})" if vote_note else "")]
    lines += [f"{k['label']}: {k['value_text']}" for k in key[2:]]
    lines += [""] + [f"- {b}" for b in bullets[:8]]
    if sep_rows:
        lines += ["", "SEP medians: " + " | ".join("Longer run" if y == "longer_run" else y for y in years)]
        lines += [f"{r['label']}: " + " | ".join(r["cells"].get(y, "") for y in years) for r in sep_rows]
    lines += ["", f"Release: {ctx['source_time']}", f"First seen: {ctx['first_seen']}"]
    lines += [f"{d.name}: {d.url}" for d in docs]
    lines += ["", f"RAAS Research Capital · Economic Events Bot · {meeting_ref}"]
    return Message(ref=meeting_ref, stage="stage1", kind="realtime", subject=subject,
                   body_html=body_html, body_text="\n".join(lines))
