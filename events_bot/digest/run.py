"""Compile and send the Daily Macro Digest (route india_eod).

  digest            everything queued for india_eod since the last digest; sent once per date
                    (sends is unique on ref/stage/kind, ref 'digest:india_eod:<YYYY-MM-DD>'), then the items
                    are marked digested with the digests row id.
  digest --sample   a review copy: items first seen in [since, until) from any status, re-scoped with the
                    current rules, sent only to the operator with a SAMPLE subject; nothing is marked.

Each item's release is fetched (and archived) at compile time, so the write-up quotes the full text even
when the poll only saw a title.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import structlog

from ..core.models import RawItem
from ..core.timeutil import IST, utcnow
from ..deliver.interface import Dispatcher, safe_name
from . import content, scope, select
from .render import PARA_CAP, SECTIONS, DigestItem, build

log = structlog.get_logger()

GMAIL_SAFE = 90_000            # bytes of HTML; Gmail clips at about 102 KB
SECTION_WEIGHT = {"RBI": 3, "SEBI": 3, "Government": 3, "Index changes": 2, "Data": 2, "Weather": 1}
NO_SINGLE_EFFECTIVE = {"pib", "mospi", "oea", "imd"}
DIGEST_SOURCES = ["pib", "cbic", "rbi_pr", "rbi_notif", "sebi_circ", "sebi_pr", "mospi", "oea", "imd",
                  "nifty_indices"]


@dataclass
class Result:
    message_path: Path | None
    status: str
    items: int
    size: int
    trimmed: int
    ref: str


def _raw(row: dict) -> RawItem:
    return RawItem(source_id=row["source_id"], ext_id=row["ext_id"], url=row["url"], title=row["title"],
                   source_published_at=row["source_published_at"], published_raw=row["published_raw"],
                   document_id=row["document_id"], meta=row["meta"] or {})


def collect(app, *, since: datetime | None = None, until: datetime | None = None, sample: bool = False) -> list[dict]:
    if not sample:
        return app.db.q("select * from items where status = 'queued' and route = 'india_eod' and digest_id is null "
                        "order by first_seen_at")
    rows = app.db.q("select * from items where first_seen_at >= %s and first_seen_at < %s and source_id = any(%s) "
                    "and status in ('routed', 'queued', 'error', 'digested') order by first_seen_at",
                    (since, until or utcnow(), DIGEST_SOURCES))
    out = []
    for r in rows:
        cfg = app.sources.get(r["source_id"])
        if cfg is not None and scope.decide(cfg, _raw(r)).route == "india_eod":
            out.append(r)
    return out


def _priority(it: DigestItem) -> int:
    p = SECTION_WEIGHT.get(it.section, 1)
    tags = set(it.tags or []) | set((it.meta or {}).get("priority_tags") or [])
    if tags & {"rbi:directions", "rbi:amendment_directions", "rbi:master_direction", "rbi:draft",
               "rbi:policy_rates", "board_outcome", "cbic:rate"}:
        p += 2
    return p


def compile_item(app, row: dict) -> DigestItem:
    meta = row["meta"] or {}
    it = DigestItem(item_id=row["id"], ref=row["ref"], source_id=row["source_id"], title=row["title"], url=row["url"],
                    kind=meta.get("kind") or (app.sources[row["source_id"]].event_name
                                              if row["source_id"] in app.sources else "Release"),
                    published=row["source_published_at"], published_raw=row["published_raw"],
                    date_only=bool(meta.get("date_only")), first_seen=row["first_seen_at"],
                    tags=list(row["tags"] or []), meta=meta)
    if it.source_id == "pib" and meta.get("ministry"):
        it.kind = meta["ministry"]
    n = 10 if "board_outcome" in (meta.get("priority_tags") or []) else 6
    try:
        body = content.fetch(app.ctx, _raw(row))
        it.body = body
        if body.document_id is not None:
            app.db.set_document_text(body.document_id, body.text)
        it.who = [w for w in select.applies_to(body.text) if w in body.text]
        it.paras = [p for p in select.operative(body.paragraphs, n=n, title=it.title, exclude=it.who)
                    if p in body.text]
        it.figs = select.figures(body.text, it.paras, limit=8)
        # A press release lists many measures with their own dates; one "effective" line would mislead.
        eff = select.effective(body.text) if it.source_id not in NO_SINGLE_EFFECTIVE else None
        it.eff = eff if eff and eff in body.text else None
        it.refs = select.reference(body.text)
        if not it.refs and meta.get("number"):
            it.refs = [meta["number"]]
    except Exception as e:  # noqa: BLE001  one bad release must not stop the digest
        log.warning("digest_fetch_failed", ref=row["ref"], error=str(e)[:200])
        it.error = f"{type(e).__name__}: {e}"[:300]
        paras = [p for p in (meta.get("paragraphs") or []) if len(p) >= content.MIN_PARA]
        it.paras = paras[:4]           # PIB: the release page read at poll time (enrich), verbatim
    if it.source_id == "nifty_indices":
        it.rows = [r for r in (meta.get("rows") or []) if r.get("index") in scope.BENCHMARK_INDICES]
    it.priority = _priority(it)
    return it


def fit(items: list[DigestItem], render) -> tuple[object, int]:
    """Render; while the HTML is over GMAIL_SAFE, shorten the lowest-priority items to 3 paragraphs (never
    fewer), then tighten the paragraph cap. Returns (message, number of items shortened)."""
    limit = PARA_CAP
    msg = render(limit)
    trimmed = 0
    order = sorted(items, key=lambda i: (i.priority, -len(i.paras)))
    for it in order:
        if len(msg.body_html.encode()) <= GMAIL_SAFE:
            break
        if len(it.paras) > 3 or len(it.figs) > 4:
            it.paras, it.figs, it.trimmed = it.paras[:3], it.figs[:4], True
            trimmed += 1
            msg = render(limit)
    while len(msg.body_html.encode()) > GMAIL_SAFE and limit > 500:
        limit -= 200
        msg = render(limit)
    return msg, trimmed


def run(app, *, day: datetime | None = None, since: datetime | None = None, until: datetime | None = None,
        sample: bool = False, to_operator: bool = False, mode: str | None = None, out_dir: Path | None = None) -> Result:
    now = utcnow()
    day = (day or now).astimezone(IST)
    rows = collect(app, since=since, until=until, sample=sample)
    ref = f"digest:india_eod:{day:%Y-%m-%d}" + (f":sample:{now:%H%M%S}" if sample else "")
    if not rows:
        log.info("digest_empty", ref=ref)
        return Result(None, "empty", 0, 0, 0, ref)
    items = [compile_item(app, r) for r in rows]
    items.sort(key=lambda i: (SECTIONS.index(i.section), -i.priority, i.first_seen))
    p_start = since or min(r["first_seen_at"] for r in rows)
    p_end = until or now
    mode = mode or app.dispatcher.mode
    if sample and not to_operator:
        mode = "dry_run"               # a review copy never goes to the desk

    def render(limit: int):
        return build(items, ref=ref, day=day, period_start=p_start, period_end=p_end, mode=mode, sample=sample,
                     limit=limit)

    msg, trimmed = fit(items, render)
    size = len(msg.body_html.encode())
    out = (out_dir or app.settings.out_dir) / "digest"
    out.mkdir(parents=True, exist_ok=True)
    path = out / (safe_name(ref.replace(":", "_")) + ".html")
    path.write_text(msg.body_html, encoding="utf-8")
    path.with_suffix(".txt").write_text(f"Subject: {msg.subject}\n\n{msg.body_text}", encoding="utf-8")

    if to_operator:
        # Operator copies (samples, checks) are always real sends to the operator only, under their own ref.
        if not sample:
            msg = msg.model_copy(update={"ref": ref + f":operator:{now:%H%M%S}"})
        recipients, disp = app.settings.operator_emails, Dispatcher(app.db, app.dispatcher.channels, "live",
                                                                       app.settings.out_dir)
    else:
        recipients, disp = app.settings.recipients, Dispatcher(app.db, app.dispatcher.channels, mode,
                                                                  app.settings.out_dir)
    res = disp.dispatch(msg, recipients)
    log.info("digest_dispatch", ref=ref, status=res.status, items=len(items), bytes=size, trimmed=trimmed,
             to_operator=to_operator)
    if res.status == "sent" and not sample and not to_operator:
        send = app.db.one("select id from sends where ref = %s and stage = 'digest' and kind = 'india_eod'", (ref,))
        d = app.db.one("insert into digests (kind, period_start, period_end, item_ids, send_id, rendered_at) "
                       "values ('india_eod', %s, %s, %s, %s, now()) returning id",
                       (p_start, p_end, [i.item_id for i in items], send["id"] if send else None))
        app.db.q("update items set digest_id = %s, status = 'digested' where id = any(%s)",
                 (d["id"], [i.item_id for i in items]))
    return Result(path, res.status, len(items), size, trimmed, ref)


def default_window(day: datetime, days: int = 1) -> tuple[datetime, datetime]:
    d = day.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=days - 1), d + timedelta(days=1)
