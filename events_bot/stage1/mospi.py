"""MoSPI data-print alerts (CPI, IIP, GDP): print vs prior, in the house style.

Data prints get Stage 1 only (BUILD_PROMPT §3). Every figure is an extraction's exact source
string; changes are arithmetic on two extracted values and labelled as computed. 'Key points'
are MoSPI's own highlight sentences, verbatim. A required field that is not found is shown as
EXTRACTION FAILED, never silently dropped.
"""
from __future__ import annotations

from datetime import date, datetime

from ..core.models import Message
from ..core.timeutil import fmt_ist
from ..extract import mospi as mx
from ..extract.base import Extraction
from ..extract.validator import first, good
from . import render
from .render import Doc, Table, change

FAILED = "EXTRACTION FAILED"
REQUIRED = {
    "cpi": ("mospi.cpi.combined.current", "mospi.cpi.combined.prior", "mospi.cfpi.combined.current"),
    "iip": ("mospi.iip.growth.current", "mospi.iip.growth.prior"),
    "gdp": ("mospi.gdp.real.current", "mospi.gdp.nominal.current"),
}
NAMES = {"cpi": "CPI inflation", "iip": "Industrial production (IIP)", "gdp": "GDP"}


def _v(ex, f) -> str:
    e = first(ex, f)
    return "" if e is None else (e.value_text if e.ok else FAILED)


def headline(kind: str, ex: list[Extraction]) -> str:
    if kind == "cpi":
        c, p = good(ex, "mospi.cpi.combined.current"), good(ex, "mospi.cpi.combined.prior")
        if not c:
            return FAILED
        s = f"CPI inflation {c.value_text}% in {c.period}"
        return s + (f"; prior {p.value_text}% ({p.period})" if p else "")
    if kind == "iip":
        c, p = good(ex, "mospi.iip.growth.current"), good(ex, "mospi.iip.growth.prior")
        if not c:
            return FAILED
        s = f"IIP growth {c.value_text}% in {c.period}"
        return s + (f"; prior {p.value_text}% in {p.period} ({p.meta.get('estimate', '')})" if p else "")
    if kind == "gdp":
        c, y = good(ex, "mospi.gdp.real.current"), good(ex, "mospi.gdp.real.year_ago")
        if not c:
            return FAILED
        s = f"Real GDP growth {c.value_text}% in {c.period}"
        return s + (f"; {y.value_text}% in {y.period}" if y else "")
    return FAILED


def _cards(kind: str, ex: list[Extraction]) -> list[dict]:
    def card(label, field, prior_field=None, note=""):
        cur = first(ex, field)
        if cur is None:
            return None
        pri = good(ex, prior_field) if prior_field else None
        sub = note
        if pri is not None and cur.ok:
            sub = f"per cent · prior {pri.value_text} · change {change(pri, cur)} (computed)"
        return {"label": label, "value_text": cur.value_text if cur.ok else FAILED, "note": sub or "per cent"}

    spec = {
        "cpi": [("CPI, combined", "mospi.cpi.combined.current", "mospi.cpi.combined.prior"),
                ("Food (CFPI), combined", "mospi.cfpi.combined.current", "mospi.cfpi.combined.prior"),
                ("CPI, rural", "mospi.cpi.rural.current", "mospi.cpi.rural.prior"),
                ("CPI, urban", "mospi.cpi.urban.current", "mospi.cpi.urban.prior")],
        "iip": [("IIP growth", "mospi.iip.growth.current", "mospi.iip.growth.prior"),
                ("Manufacturing", "mospi.iip.manufacturing.current", None),
                ("Mining & quarrying", "mospi.iip.mining.current", None),
                ("Electricity & gas", "mospi.iip.electricity.current", None),
                ("Water supply & waste", "mospi.iip.water.current", None)],
        "gdp": [("Real GDP growth", "mospi.gdp.real.current", "mospi.gdp.real.year_ago"),
                ("Nominal GDP growth", "mospi.gdp.nominal.current", "mospi.gdp.nominal.year_ago"),
                ("Real GVA growth", "mospi.gva.real.current", None),
                ("Nominal GVA growth", "mospi.gva.nominal.current", None),
                ("GFCF growth (real)", "mospi.gfcf.real.current", None),
                ("PFCE growth (real)", "mospi.pfce.real.current", None)],
    }[kind]
    out = []
    for label, f, pf in spec:
        c = card(label, f, pf, "per cent, y-o-y" if pf is None else "")
        if c:
            out.append(c)
    return out


def _cpi_table(ex: list[Extraction]) -> Table | None:
    rows = []
    for series, label in (("cpi", "CPI (General)"), ("cfpi", "Food (CFPI)")):
        for area in ("rural", "urban", "combined"):
            cur, pri = first(ex, f"mospi.{series}.{area}.current"), good(ex, f"mospi.{series}.{area}.prior")
            if cur is None:
                continue
            rows.append({"label": f"{label}, {area}",
                         "cells": [pri.value_text if pri else "", cur.value_text if cur.ok else FAILED,
                                   change(pri, cur) if cur.ok else ""]})
    if not rows:
        return None
    cur = first(ex, "mospi.cpi.combined.current")
    pri = first(ex, "mospi.cpi.combined.prior")
    return Table("Year-on-year inflation, per cent",
                 [pri.period if pri else "Prior", cur.period if cur else "Current", "Change*"], rows,
                 note="* Computed: current minus prior, both as printed in the release table.")


def build(kind: str, ref: str, ex: list[Extraction], texts: dict[str, str], *, release_title: str, docs: list[Doc],
          published: datetime | None, first_seen: datetime, mode: str = "live") -> Message:
    head = headline(kind, ex)
    missing = [f"{f}: not found in source" for f in REQUIRED.get(kind, ()) if first(ex, f) is None]
    failed = [f"{e.field}: {e.validation_error}" for e in ex if not e.ok] + missing
    tables = [t for t in ([_cpi_table(ex)] if kind == "cpi" else []) if t]
    bullets = mx.highlights(kind, texts)
    return render.stage1(
        ref=ref, subject=f"[MoSPI] {NAMES[kind]} | {head}", source_tag="MOSPI", event_name=NAMES[kind],
        title=head, key=_cards(kind, ex), tables=tables, bullets=bullets[:8],
        source_time=fmt_ist(published) if published else "per MoSPI listing", first_seen=fmt_ist(first_seen),
        docs=docs, failed=failed, mode=mode, heading=NAMES[kind], callout_title="The print",
        ev_date=published.date() if published else date.today())
