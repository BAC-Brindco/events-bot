"""Stage 1 (release alert) for stream items.

Phase 1 renders the deterministic parts only: verbatim title, an optional
verbatim excerpt, source URL and both timestamps. Key numbers (from validated
extractions) and LLM bullets (behind the number guard) arrive in Phases 2-4
through the same template slots.
"""
from __future__ import annotations

import html as htmllib
import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..core.models import Message, RawItem, SourceConfig
from ..core.timeutil import fmt_ist

TEMPLATES = Path(__file__).resolve().parents[1] / "deliver" / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["j2", "html"]),
                   trim_blocks=True, lstrip_blocks=True)

EXCERPT_CHARS = 400


def plain_excerpt(summary: str | None) -> str | None:
    """Verbatim text from the feed's description, tags stripped, cut on a word boundary."""
    if not summary:
        return None
    t = re.sub(r"<[^>]+>", " ", summary)
    t = re.sub(r"\s+", " ", htmllib.unescape(t)).strip()
    if not t:
        return None
    if len(t) <= EXCERPT_CHARS:
        return t
    cut = t[:EXCERPT_CHARS].rsplit(" ", 1)[0]
    return cut + " …"


def stage1_item(cfg: SourceConfig, item: RawItem, *, first_seen_at: datetime, tags: list[str],
                mode: str = "live") -> Message:
    subject = f"[{cfg.tag}] {cfg.event_name} | {item.title}"
    ctx = dict(subject=subject, ref=item.ref, stage="stage1", mode=mode, source_tag=cfg.tag,
               event_name=cfg.event_name, title=item.title, url=item.url, tags=tags,
               source_time=fmt_ist(item.source_published_at) if item.source_published_at
               else (item.published_raw or "not stated by source"),
               first_seen=fmt_ist(first_seen_at), key_numbers=[], bullets=[],
               excerpt=plain_excerpt(item.summary), links=[])
    body_html = _house_item(ctx)
    lines = [item.title, "", f"Source: {item.url}", f"Source time: {ctx['source_time']}",
             f"First seen: {ctx['first_seen']}"]
    if tags:
        lines.insert(1, "Tags: " + ", ".join(tags))
    if ctx["excerpt"]:
        lines += ["", ctx["excerpt"]]
    lines += ["", f"RAAS Research Capital · Economic Events Bot · {item.ref}"]
    return Message(ref=item.ref, stage="stage1", kind="realtime", subject=subject,
                   body_html=body_html, body_text="\n".join(lines))


def _house_item(ctx: dict) -> str:
    """Stream-item alert in the BAC house style (deliver/design.py), like the other bac-reports e-mails."""
    from html import escape as e

    from ..core.timeutil import IST, utcnow
    from ..deliver import design as dz
    from ..deliver import house as hs
    from .render import Doc

    body = dz.masthead(kicker=hs.KICKER, title=e(f"{ctx['source_tag']} {ctx['event_name']}"),
                       dateline=f"{utcnow().astimezone(IST):%a %d-%b-%Y} &middot; Release alert",
                       subline=f"source time {e(ctx['source_time'])} &middot; first seen {e(ctx['first_seen'])}")
    head = f"<strong>{e(ctx['title'])}</strong>"
    if ctx["tags"]:
        head += "".join(dz.badge(t) for t in ctx["tags"])
    body += dz.row(dz.callout(head, accent="gold", title="Release"), pad=dz.BLOCK_PAD)
    if ctx["excerpt"]:
        body += dz.row(dz.callout(e(ctx["excerpt"]), accent="navy", title="Opening of the source text, verbatim"),
                       pad=dz.BLOCK_PAD)
    body += dz.row(hs.section_caption("", "Source documents")
                   + hs.links([Doc("Release", ctx["url"])]), pad=dz.SECTION_PAD)
    prov = f"Compiled by the RAAS Events Bot. Ref {e(ctx['ref'])}."
    if ctx["mode"] != "live":
        prov += f" {dz.value(ctx['mode'].upper().replace('_', ' ') + ' — not sent', 'warn')}"
    body += dz.row(dz.colophon(prov, hs.DISCLAIMER), pad="26px 24px 26px 24px")
    return dz.doc_open(e(ctx["subject"]), e(ctx["title"])) + body + dz.DOC_CLOSE
