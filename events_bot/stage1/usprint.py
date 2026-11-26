"""US data-print alerts (BLS, BEA, Census, DOL): print vs prior, in the house style.

Same contract as stage1/mospi.py: every figure is an extraction's exact source string, validated in
place; changes are arithmetic on two extracted values and labelled as computed; 'key points' are the
agency's own sentences, verbatim. A required field that is not found is shown as EXTRACTION FAILED.

Each US print adapter describes its alert with a `PrintSpec` and hands over a `Capture` (archived texts,
extractions, documents). Nothing source-specific lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..core.models import Message
from ..core.timeutil import fmt_ist
from ..extract.base import Extraction
from ..extract.validator import first, good
from . import render
from .render import Doc, Table, change

FAILED = "EXTRACTION FAILED"


@dataclass(frozen=True)
class Card:
    label: str
    field: str                        # extraction field of the current value
    prior: str | None = None          # extraction field of the prior value (enables "change (computed)")
    unit: str = "per cent"            # shown under the figure


@dataclass(frozen=True)
class PrintSpec:
    kind: str                         # "cpi", "nfp", ... (event_type and ref prefix)
    name: str                         # "CPI inflation"
    tag: str                          # subject tag and render.SOURCES key: "BLS", "BEA", "CENSUS", "DOL"
    headline: tuple[tuple[str, str], ...]   # ((label, field), ...): subject line, in order
    cards: tuple[Card, ...]
    required: tuple[str, ...] = ()
    table_title: str | None = None    # when set, every card with a prior becomes a prior/current/change row


@dataclass
class Capture:
    """One release, fetched and parsed. `texts` are keyed by Extraction.doc."""
    period: str                       # reference period as the release states it, e.g. "October 2026"
    texts: dict[str, str]
    extractions: list[Extraction]
    docs: list[Doc]
    bullets: list[str] = field(default_factory=list)     # verbatim sentences of the release
    release_at: datetime | None = None                   # official release time (embargo line / calendar)


def headline(spec: PrintSpec, ex: list[Extraction]) -> str:
    parts = []
    for label, f in spec.headline:
        e = good(ex, f)
        if e is not None:
            parts.append(f"{label} {e.value_text}")
    return "; ".join(parts) if parts else FAILED


def _cards(spec: PrintSpec, ex: list[Extraction]) -> list[dict]:
    out = []
    for c in spec.cards:
        cur = first(ex, c.field)
        if cur is None:
            continue
        pri = good(ex, c.prior) if c.prior else None
        note = c.unit
        if pri is not None and cur.ok:
            note = f"{c.unit} · prior {pri.value_text} · change {change(pri, cur)} (computed)"
        out.append({"label": c.label, "value_text": cur.value_text if cur.ok else FAILED, "note": note})
    return out


def _table(spec: PrintSpec, ex: list[Extraction]) -> Table | None:
    if not spec.table_title:
        return None
    rows, cur_period, pri_period = [], None, None
    for c in spec.cards:
        cur = first(ex, c.field)
        pri = good(ex, c.prior) if c.prior else None
        if cur is None or pri is None:
            continue
        cur_period, pri_period = cur_period or cur.period, pri_period or pri.period
        rows.append({"label": c.label, "cells": [pri.value_text, cur.value_text if cur.ok else FAILED,
                                                  change(pri, cur) if cur.ok else ""]})
    if not rows:
        return None
    return Table(spec.table_title, [pri_period or "Prior", cur_period or "Current", "Change*"], rows,
                 note="* Computed: current minus prior, both as printed in the release.")


def build(spec: PrintSpec, cap: Capture, *, ref: str, first_seen: datetime, mode: str = "live") -> Message:
    ex = cap.extractions
    head = headline(spec, ex)
    missing = [f"{f}: not found in source" for f in spec.required if first(ex, f) is None]
    failed = [f"{e.field}: {e.validation_error}" for e in ex if not e.ok] + missing
    table = _table(spec, ex)
    title = f"{spec.name}, {cap.period}: {head}" if head != FAILED else f"{spec.name}, {cap.period}: {FAILED}"
    return render.stage1(
        ref=ref, subject=f"[{spec.tag}] {spec.name} | {cap.period} | {head}", source_tag=spec.tag,
        event_name=spec.name, title=title, key=_cards(spec, ex), tables=[table] if table else [],
        bullets=cap.bullets[:8], source_time=fmt_ist(cap.release_at) if cap.release_at else "per agency calendar",
        first_seen=fmt_ist(first_seen), docs=cap.docs, failed=failed, mode=mode, heading=spec.name,
        callout_title="The print", ev_date=(cap.release_at or first_seen).date())
