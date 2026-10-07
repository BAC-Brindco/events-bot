"""Shared Stage 1 / Stage 2 rendering for scheduled events (HTML + plain text from one structure).

Builders (stage1/fomc.py, stage1/mpc.py, ...) decide *what* goes in -- only validated
extraction strings and labelled computed values -- and these functions lay it out in the
BAC house style (deliver/design.py, the module the bac-reports deals and announcements
e-mails use): masthead, KPI cards, gold callouts, house tables with source lines, colophon.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from html import escape as _e

from ..core.models import Message
from ..core.timeutil import fmt_ist, utcnow
from ..deliver import design as dz
from ..deliver import house as hs

FOOTER = "RAAS Research Capital · Events Desk · {ref}"
SOURCES = {"RBI": ("Reserve Bank of India", "MPC"), "FED": ("Federal Reserve", "FOMC"),
           "MOSPI": ("Ministry of Statistics and Programme Implementation (NSO)", "MoSPI"),
           "OEA": ("Office of the Economic Adviser, DPIIT", "OEA")}


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


def _meta(ref: str, source_tag: str) -> tuple[str, str, date | None]:
    name, short = SOURCES.get(source_tag, (source_tag, source_tag))
    try:
        d = date.fromisoformat(ref.split(":", 1)[1])
    except (IndexError, ValueError):
        d = None
    return name, short, d


def _provenance(source_name: str, ref: str, mode: str) -> str:
    now = utcnow()
    p = (f"Compiled by the RAAS Events Bot at {fmt_ist(now, with_date=False).replace(' IST', '')} IST on "
         f"{now.strftime('%d %B %Y')}. Source: {_e(source_name)} official releases, linked above. Ref {_e(ref)}.")
    if mode != "live":
        p += f" {dz.value(mode.upper().replace('_', ' ') + ' — not sent', 'warn')}"
    return p


def _split_bullet(b: str) -> tuple[str, str]:
    head, sep, rest = b.partition(": ")
    if sep and len(head) <= 40:
        return _e(head) + ":", _e(rest)
    return "", _e(b)


def _cell(v: str) -> str:
    if v == "EXTRACTION FAILED":
        return dz.value(v, "bad")
    return dz.value(_e(v)) if v != "" else dz.value("")


def stage1(*, ref: str, subject: str, source_tag: str, event_name: str, title: str, key: list[dict],
           tables: list[Table], bullets: list[str], source_time: str, first_seen: str, docs: list[Doc],
           failed: list[str], mode: str, heading: str | None = None, callout_title: str = "The decision",
           ev_date: date | None = None) -> Message:
    source_name, short, ref_date = _meta(ref, source_tag)
    ev_date = ev_date or ref_date
    body = dz.masthead(
        kicker=hs.KICKER,
        title=_e(heading) if heading else f"{short} Decision",
        dateline=(ev_date.strftime("%a %d-%b-%Y") if ev_date else "") + " &middot; Release alert",
        subline=f"{_e(source_name)} &middot; released {_e(source_time)} &middot; first seen {_e(first_seen)}",
    )
    body += dz.row(dz.callout(f"<strong>{_e(title)}</strong>", accent="gold", title=callout_title),
                   pad=dz.BLOCK_PAD)
    cards = []
    for k in key:
        v = k["value_text"]
        cards.append({"label": _e(k["label"]), "value": _e(v) if v != "EXTRACTION FAILED" else dz.value(v, "bad"),
                      "sub": _e(k.get("note", "")), "flag": "bad" if v == "EXTRACTION FAILED" else None})
    body += dz.row(dz.kpi_grid(cards, per_row=3), pad=dz.BLOCK_PAD)
    if failed:
        body += dz.row(hs.failed_panel(failed), pad=dz.BLOCK_PAD)
    if bullets:
        body += dz.row(dz.callout(dz.numbered_list([_split_bullet(b) for b in bullets]), accent="gold",
                                  title="Key points"), pad=dz.BLOCK_PAD)
    for i, t in enumerate(tables, 1):
        body += dz.row(
            hs.section_caption(hs.ROMAN[i], _e(t.title))
            + dz.datatable([""] + [_e(c) for c in t.cols],
                           [[_e(r["label"])] + [_cell(c) for c in r["cells"]] for r in t.rows],
                           align=["l"] + ["r"] * len(t.cols),
                           source=f"{_e(source_name)} &middot; official release",
                           caption=_e(t.note)),
            pad=dz.SECTION_PAD)
    body += dz.row(hs.section_caption("", "Source documents") + hs.links(docs), pad=dz.SECTION_PAD)
    body += dz.row(dz.colophon(_provenance(source_name, ref, mode), hs.DISCLAIMER), pad="26px 24px 26px 24px")
    body_html = dz.doc_open(_e(subject), _e(title)) + body + dz.DOC_CLOSE

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
    source_name, short, ev_date = _meta(ref, source_tag)
    body = dz.masthead(
        kicker=hs.KICKER,
        title=f"{short} Review",
        dateline=(ev_date.strftime("%a %d-%b-%Y") if ev_date else "") + " &middot; Post-meeting summary",
        subline=f"{_e(source_name)} &middot; compared with {_e(prior_label or 'the prior meeting')}",
    )
    body += dz.row(dz.callout(f"<strong>{_e(title)}</strong>", accent="gold", title="The decision"),
                   pad=dz.BLOCK_PAD)
    n = 0
    # Vote
    n += 1
    vote_html = dz.strip([("This meeting", f"<strong>{_e(vote['now'])}</strong> "
                                           f"<span style=\"color:{dz.INK_FAINT};\">{_e(vote['now_note'])}</span>"),
                          (_e(prior_label or "Prior"), f"{_e(vote['prior'])} "
                                                       f"<span style=\"color:{dz.INK_FAINT};\">{_e(vote['prior_note'])}</span>")])
    vote_html += ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
                  '<tr><td style="padding:14px 0 0 0;">'
                  + dz.numbered_list([_split_bullet(l) for l in vote["lines"]]) + "</td></tr></table>")
    body += dz.row(hs.section_caption(hs.ROMAN[n], "Vote and dissent") + vote_html, pad=dz.SECTION_PAD)
    # Tables
    for t in tables:
        n += 1
        rows = []
        for r in t.rows:
            cells = [_cell(c) for c in r]
            if t.bold_col and t.bold_col - 1 < len(cells) and r[t.bold_col - 1]:
                cells[t.bold_col - 1] = f"<strong>{cells[t.bold_col - 1]}</strong>"
            rows.append(cells)
        body += dz.row(
            hs.section_caption(hs.ROMAN[n], _e(t.title))
            + dz.datatable([_e(c) for c in t.cols], rows, align=["l"] + ["r"] * (len(t.cols) - 1),
                           source=f"{_e(source_name)} &middot; this and the prior official release",
                           caption=_e(t.note)),
            pad=dz.SECTION_PAD)
    # Redline
    n += 1
    stats = (f"{red_stats.changed} sentences changed &middot; {red_stats.added} added &middot; "
             f"{red_stats.removed} removed &middot; {red_stats.unchanged} unchanged" if red_stats
             else "prior document not available")
    red = (redline.replace("<del>", f'<del style="{hs.DEL_STYLE}">').replace("<ins>", f'<ins style="{hs.INS_STYLE}">')
           if redline else "")
    body += dz.row(hs.section_caption(hs.ROMAN[n], f"{_e(redline_title)} vs {_e(prior_label or 'prior')}", stats)
                   + (hs.redline_block(red) if red else ""), pad=dz.SECTION_PAD)
    # Press conference + market
    body += dz.row(dz.callout(_e(transcript_status) + (f"<br /><br />{_e(market_line)}" if market_line else ""),
                              accent="navy", title="Press conference"), pad=dz.BLOCK_PAD)
    body += dz.row(hs.section_caption("", "Source documents") + hs.links(docs), pad=dz.SECTION_PAD)
    body += dz.row(dz.colophon(_provenance(source_name, ref, mode), hs.DISCLAIMER), pad="26px 24px 26px 24px")
    body_html = dz.doc_open(_e(subject), _e(title)) + body + dz.DOC_CLOSE

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
