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

from dataclasses import dataclass, field
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
MAX_PARTS = 3                  # a long day goes out as up to 3 e-mails before anything is shortened
MAX_AGE = timedelta(days=4)    # Friday's evening to Monday's, plus one holiday
SECTION_WEIGHT = {"RBI": 3, "SEBI": 3, "Government": 3, "Index changes": 2, "Data": 2, "Weather": 1}
NO_SINGLE_EFFECTIVE = {"pib", "mospi", "oea", "imd"}
DIGEST_SOURCES = ["pib", "cbic", "rbi_pr", "rbi_notif", "sebi_circ", "sebi_pr", "mospi", "oea", "imd",
                  "nifty_indices"]


@dataclass
class Result:
    message_path: Path | None
    status: str
    items: int
    size: int                  # largest part, bytes of HTML
    trimmed: int
    ref: str
    parts: int = 0
    paths: list = field(default_factory=list)


def _raw(row: dict) -> RawItem:
    return RawItem(source_id=row["source_id"], ext_id=row["ext_id"], url=row["url"], title=row["title"],
                   source_published_at=row["source_published_at"], published_raw=row["published_raw"],
                   document_id=row["document_id"], meta=row["meta"] or {})


def collect(app, *, since: datetime | None = None, until: datetime | None = None, sample: bool = False) -> list[dict]:
    if not sample:
        rows = app.db.q("select * from items where status = 'queued' and route = 'india_eod' and digest_id is null "
                        "order by first_seen_at")
        keep = []
        oldest = utcnow() - MAX_AGE
        for r in rows:
            if r["first_seen_at"] < oldest:
                # a backlog (e.g. weeks of dry-run queueing before go-live) is logged, not mailed
                app.db.insert_filtered(r["id"], r["source_id"], r["title"], r["url"], "stale",
                                       f"queued more than {MAX_AGE.days} days before the digest")
                app.db.update_item(r["id"], status="filtered")
                continue
            cfg = app.sources.get(r["source_id"])
            sc = scope.decide(cfg, _raw(r)) if cfg is not None else None
            if sc is not None and not sc.keep and "unclassified" not in (r["tags"] or []):
                # queued before the current scope rules (F-29): log it like any other out-of-scope item
                app.db.insert_filtered(r["id"], r["source_id"], r["title"], r["url"], "out_of_scope", sc.reason)
                app.db.update_item(r["id"], status="filtered")
                continue
            keep.append(r)
        return keep
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
        window = 400 if n > 6 else select.WINDOW          # Board outcomes run through the whole document
        # A press release lists many measures with their own dates; one "effective" line would mislead.
        eff = select.effective(body.text) if it.source_id not in NO_SINGLE_EFFECTIVE else None
        it.eff = eff if eff and eff in body.text else None
        shown_eff = [p for p in body.paragraphs if it.eff and p == it.eff]     # already in the header
        it.paras = [p for p in select.operative(body.paragraphs, n=n, title=it.title,
                                                exclude=list(it.who) + shown_eff, window=window) if p in body.text]
        it.figs = select.figures(body.text, it.paras, limit=8)
        it.refs = select.reference(body.text)
        if not it.refs and meta.get("number"):
            it.refs = [meta["number"]]
        if it.published is None and body.posted is not None:
            it.published, it.date_only = body.posted, False        # PIB's own "Posted On" time
    except Exception as e:  # noqa: BLE001  one bad release must not stop the digest
        log.warning("digest_fetch_failed", ref=row["ref"], error=str(e)[:200])
        it.error = f"{type(e).__name__}: {e}"[:300]
        paras = [content.tidy(p) for p in (meta.get("paragraphs") or []) if len(p) >= content.MIN_PARA]
        it.paras = paras[:4]           # PIB: the release page read at poll time (enrich), verbatim
    if it.source_id == "nifty_indices":
        it.rows = [r for r in (meta.get("rows") or []) if r.get("index") in scope.BENCHMARK_INDICES]
    it.priority = _priority(it)
    return it


def _size(msg) -> int:
    return len(msg.body_html.encode())


def pack(items: list[DigestItem], render, limit: int) -> list[list[DigestItem]]:
    """Greedy split, in digest order, into parts whose HTML stays under GMAIL_SAFE."""
    parts: list[list[DigestItem]] = []
    cur: list[DigestItem] = []
    start = 1
    for it in items:
        trial = cur + [it]
        if cur and _size(render(trial, (1, 2), start, limit)) > GMAIL_SAFE:
            parts.append(cur)
            start += len(cur)
            cur = [it]
        else:
            cur = trial
    if cur:
        parts.append(cur)
    return parts


def fit(items: list[DigestItem], render) -> tuple[list, int]:
    """Messages for the digest, each under GMAIL_SAFE, with full detail where possible.

    Detail is never cut first: a long day is split into up to MAX_PARTS e-mails. Only when even that is not
    enough are the lowest-priority items shortened to 3 paragraphs (never fewer), then the per-paragraph
    cap is tightened. Returns (messages, number of items shortened)."""
    limit = PARA_CAP
    trimmed = 0
    parts = pack(items, render, limit)
    order = sorted(items, key=lambda i: (i.priority, -len(i.paras)))
    for it in order:
        if len(parts) <= MAX_PARTS:
            break
        if len(it.paras) > 3 or len(it.figs) > 4:
            it.paras, it.figs, it.trimmed = it.paras[:3], it.figs[:4], True
            trimmed += 1
            parts = pack(items, render, limit)
    while len(parts) > MAX_PARTS and limit > 500:
        limit -= 200
        parts = pack(items, render, limit)
    msgs, start = [], 1
    for i, p in enumerate(parts, 1):
        m = render(p, (i, len(parts)), start, limit)
        # a single oversized item (never seen in practice): shorten it rather than let Gmail clip it
        if _size(m) > GMAIL_SAFE and len(p) == 1 and not p[0].trimmed:
            p[0].paras, p[0].figs, p[0].trimmed = p[0].paras[:3], p[0].figs[:4], True
            trimmed += 1
            m = render(p, (i, len(parts)), start, limit)
        msgs.append(m)
        start += len(p)
    return msgs, trimmed


def run(app, *, day: datetime | None = None, since: datetime | None = None, until: datetime | None = None,
        sample: bool = False, to_operator: bool = False, mode: str | None = None, out_dir: Path | None = None,
        sample_label: str = "SAMPLE") -> Result:
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

    counts = {s: sum(1 for i in items if i.section == s) for s in SECTIONS}

    def render(part_items, part, start, limit):
        return build(part_items, ref=ref, day=day, period_start=p_start, period_end=p_end, mode=mode, sample=sample,
                     limit=limit, part=part, start_no=start, total_items=len(items), section_counts=counts,
                     sample_label=sample_label)

    msgs, trimmed = fit(items, render)
    out = (out_dir or app.settings.out_dir) / "digest"
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for m in msgs:
        p = out / (safe_name(m.ref.replace(":", "_")) + ".html")
        p.write_text(m.body_html, encoding="utf-8")
        p.with_suffix(".txt").write_text(f"Subject: {m.subject}\n\n{m.body_text}", encoding="utf-8")
        paths.append(p)

    if to_operator:
        # Operator copies (samples, checks) are always real sends to the operator only, under their own ref.
        if not sample:
            msgs = [m.model_copy(update={"ref": m.ref.replace(ref, ref + f":operator:{now:%H%M%S}")}) for m in msgs]
        recipients, disp = app.settings.operator_emails, Dispatcher(app.db, app.dispatcher.channels, "live",
                                                                       app.settings.out_dir)
    else:
        recipients, disp = app.settings.recipients, Dispatcher(app.db, app.dispatcher.channels, mode,
                                                                  app.settings.out_dir)
    statuses = [disp.dispatch(m, recipients).status for m in msgs]
    status = next((s for s in ("failed", "no_recipients", "already_sent", "sent", "written") if s in statuses),
                  statuses[0])
    sizes = [_size(m) for m in msgs]
    log.info("digest_dispatch", ref=ref, status=status, parts=len(msgs), items=len(items), bytes=sizes,
             trimmed=trimmed, to_operator=to_operator)
    if all(s == "sent" for s in statuses) and not sample and not to_operator:
        send = app.db.one("select id from sends where ref = %s and stage = 'digest' and kind = 'india_eod'", (ref,))
        d = app.db.one("insert into digests (kind, period_start, period_end, item_ids, send_id, rendered_at) "
                       "values ('india_eod', %s, %s, %s, %s, now()) returning id",
                       (p_start, p_end, [i.item_id for i in items], send["id"] if send else None))
        app.db.q("update items set digest_id = %s, status = 'digested' where id = any(%s)",
                 (d["id"], [i.item_id for i in items]))
    return Result(paths[0], status, len(items), max(sizes), trimmed, ref, parts=len(msgs), paths=paths)


def default_window(day: datetime, days: int = 1) -> tuple[datetime, datetime]:
    d = day.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=days - 1), d + timedelta(days=1)
