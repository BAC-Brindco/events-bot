"""Shared Stage 1 / Stage 2 rendering for scheduled events (HTML + plain text from one structure).

Builders (stage1/fomc.py, stage1/mpc.py, ...) decide *what* goes in -- only validated
extraction strings and labelled computed values -- and these functions lay it out.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import Message
from .build import _env

FOOTER = "RAAS Research Capital · Economic Events Bot · {ref}"


@dataclass
class Doc:
    name: str
    url: str


@dataclass
class Table:
    title: str
    cols: list[str]
    rows: list[dict] = field(default_factory=list)     # stage 1: {"label", "cells"}; stage 2: list of cells
    note: str = ""
    bold_col: int = 0                                  # stage 2: 1-based column to bold (current values)


def stage1(*, ref: str, subject: str, source_tag: str, event_name: str, title: str, key: list[dict],
           tables: list[Table], bullets: list[str], source_time: str, first_seen: str, docs: list[Doc],
           failed: list[str], mode: str) -> Message:
    body_html = _env.get_template("event_stage1.html.j2").render(
        subject=subject, ref=ref, stage="stage1", mode=mode, source_tag=source_tag, event_name=event_name,
        title=title, key_numbers=key, tables=tables, bullets=bullets, source_time=source_time,
        first_seen=first_seen, docs=docs, failed=failed)
    lines = [title, ""]
    lines += [f"{k['label']}: {k['value_text']}" + (f" ({k['note']})" if k.get("note") else "") for k in key]
    for t in tables:
        lines += ["", f"{t.title}: " + " | ".join(t.cols)]
        lines += [f"{r['label']}: " + " | ".join(r["cells"]) for r in t.rows]
        if t.note:
            lines.append(t.note)
    if bullets:
        lines += [""] + [f"- {b}" for b in bullets]
    if failed:
        lines += ["", "EXTRACTION FAILED: " + "; ".join(failed)]
    lines += ["", f"Release: {source_time}", f"First seen: {first_seen}"]
    lines += [f"{d.name}: {d.url}" for d in docs]
    lines += ["", FOOTER.format(ref=ref)]
    return Message(ref=ref, stage="stage1", kind="realtime", subject=subject, body_html=body_html,
                   body_text="\n".join(lines))


def stage2(*, ref: str, subject: str, source_tag: str, event_name: str, title: str, redline_title: str,
           prior_label: str | None, redline: str, red_stats, vote: dict, tables: list[Table],
           transcript_status: str, market_line: str | None, docs: list[Doc], mode: str) -> Message:
    body_html = _env.get_template("event_stage2.html.j2").render(
        subject=subject, ref=ref, stage="stage2", mode=mode, source_tag=source_tag, event_name=event_name,
        title=title, redline_title=redline_title, prior_label=prior_label, redline=redline, red_stats=red_stats,
        vote=vote, tables=tables, transcript_status=transcript_status, market_line=market_line, docs=docs)
    lines = [title, f"{redline_title} vs {prior_label or 'prior'}: "
             + (f"{red_stats.changed} sentences changed, {red_stats.added} added, {red_stats.removed} removed"
                if red_stats else "prior document not available"),
             f"Vote: {vote['now']} (prior {vote['prior']})"]
    lines += [f"- {l}" for l in vote["lines"]]
    for t in tables:
        lines += ["", f"{t.title} ({' | '.join(t.cols)}):"]
        lines += [" | ".join(c for c in r) for r in t.rows]
        if t.note:
            lines.append(t.note)
    lines += ["", f"Press conference: {transcript_status}"]
    if market_line:
        lines.append(market_line)
    lines += [""] + [f"{d.name}: {d.url}" for d in docs] + ["", FOOTER.format(ref=ref)]
    return Message(ref=ref, stage="stage2", kind="realtime", subject=subject, body_html=body_html,
                   body_text="\n".join(lines))


def change(a, b) -> str:
    """b - a for two validated extractions, at the larger source precision; '' if either is missing."""
    from decimal import Decimal
    if not (a and b) or a.value_norm is None or b.value_norm is None:
        return ""
    d = b.value_norm - a.value_norm
    if d == 0:
        return "0"
    places = max(-a.value_norm.as_tuple().exponent, -b.value_norm.as_tuple().exponent, 0)
    return f"{d.quantize(Decimal(1).scaleb(-places)):+f}"
