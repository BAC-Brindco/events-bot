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
