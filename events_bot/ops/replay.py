"""Replay: re-run parsing and rendering from archived bytes, with no network and no sends.

  replay item:<source>:<ext_id>        the archived feed response the item came from
  replay --file <path> --source <id>   a fixture or any saved response (no DB needed)

Output goes to out/replay/ (Dispatcher mode "replay").
"""
from __future__ import annotations

from pathlib import Path

from ..core import registry
from ..core.app import App
from ..core.models import FetchResult, RawItem
from ..core.timeutil import utcnow
from ..deliver.interface import Dispatcher
from ..stage1.build import stage1_item


def _parse_bytes(app: App, source_id: str, content: bytes, url: str, url_name: str | None,
                 document_id: int | None = None) -> list[RawItem]:
    cfg = app.sources[source_id]
    adapter = registry.build(cfg, app.ctx)
    res = FetchResult(url=url, final_url=url, status=200, fetched_at=utcnow(), content=content,
                      document_id=document_id)
    name = url_name or next((k for k, v in cfg.urls.items() if v == url), next(iter(cfg.urls)))
    return adapter.parse(res, name)


def replay_ref(app: App, ref: str) -> list[Path]:
    item = app.db.get_item(ref)
    if item is None:
        raise SystemExit(f"unknown ref {ref}")
    doc = app.db.get_document(item["document_id"])
    content = app.archive.get(doc["storage_key"])
    items = _parse_bytes(app, item["source_id"], content, doc["url"], item["meta"].get("feed"), doc["id"])
    match = [i for i in items if i.ext_id == item["ext_id"]]
    if not match:
        raise SystemExit(f"{ref}: not found when re-parsing document {doc['id']}")
    disp = Dispatcher(None, [], "replay", app.settings.out_dir)
    cfg = app.sources[item["source_id"]]
    out = []
    for it in match:
        msg = stage1_item(cfg, it, first_seen_at=item["first_seen_at"], tags=list(item["tags"]), mode="replay")
        out.append(disp.dispatch(msg, app.settings.recipients).path)
    return out


def replay_file(app: App, source_id: str, path: Path) -> list[Path]:
    cfg = app.sources[source_id]
    items = _parse_bytes(app, source_id, path.read_bytes(), next(iter(cfg.urls.values())), None)
    disp = Dispatcher(None, [], "replay", app.settings.out_dir)
    now = utcnow()
    return [disp.dispatch(stage1_item(cfg, it, first_seen_at=now, tags=[], mode="replay"),
                          app.settings.recipients).path for it in items]


def replay_fomc(app: App, ref: str, from_dir: Path | None = None) -> list[Path]:
    """`fomc:YYYY-MM-DD`: Stage 1 + Stage 2 from archived documents (or a fixture directory)."""
    from datetime import date

    from ..sources.us import fed

    d = date.fromisoformat(ref.split(":", 1)[1])
    rows = fed.parse_calendar(_calendar_bytes(app, from_dir))
    dates = [r["date"] for r in rows]
    prior = dates[dates.index(d) - 1] if d in dates and dates.index(d) else None
    sep = next((r["sep"] for r in rows if r["date"] == d), True)

    def load(day: date, kinds: tuple[str, ...]) -> dict[str, bytes]:
        urls, got = fed.doc_urls(day), {}
        for k in kinds:
            if from_dir is not None:
                p = from_dir / urls[k].rsplit("/", 1)[-1]
                if p.exists():
                    got[k] = p.read_bytes()
            else:
                doc = app.db.latest_document(urls[k])
                if doc is not None:
                    got[k] = app.archive.get(doc["storage_key"])
        return got

    docs = load(d, ("statement", "impl") + (("sep",) if sep else ()))
    if "statement" not in docs:
        raise SystemExit(f"{ref}: no archived statement")
    prior_docs = load(prior, ("statement", "impl")) if prior else None
    ev = app.db.get_event(ref) if from_dir is None else None
    first_seen = (ev or {}).get("meta", {}).get("first_seen")
    from datetime import datetime
    m1, m2, _, _ = fed.build_messages(d, docs, prior, prior_docs, fed.doc_urls(d),
                                      first_seen=datetime.fromisoformat(first_seen) if first_seen
                                      else fed.release_at(d), mode="replay")
    disp = Dispatcher(None, [], "replay", app.settings.out_dir)
    return [disp.dispatch(m, app.settings.recipients).path for m in (m1, m2) if m is not None]


def _calendar_bytes(app: App, from_dir: Path | None) -> bytes:
    if from_dir is not None:
        cal = sorted(from_dir.glob("fomccalendars*.htm"))
        if cal:
            return cal[-1].read_bytes()
    doc = app.db.latest_document(app.sources["fomc"].urls["calendar"])
    if doc is None:
        raise SystemExit("no archived FOMC calendar; run `calendar` first or pass --from-dir")
    return app.archive.get(doc["storage_key"])
