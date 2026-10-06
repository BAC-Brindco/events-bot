"""Adapter base class and resolution from `config/sources.yaml`.

Adding a source = one YAML entry + one adapter module under events_bot/sources.
Adapters split fetching from parsing so `--replay` can re-run the parse on
archived bytes without touching the network.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from .models import FetchResult, RawItem, ScheduledEvent, SourceConfig

if TYPE_CHECKING:
    from .archive import Archive
    from .db import Database
    from .fetch import Fetcher
    from .app import App
    from .settings import Settings


@dataclass
class Context:
    settings: "Settings"
    db: "Database"
    fetcher: "Fetcher"
    archive: "Archive"
    app: "App | None" = None          # scheduled adapters dispatch Stage 1/2 through the app


class SourceAdapter:
    """Base for every source. Override what the source supports."""

    doc_type: ClassVar[str] = "feed"

    def __init__(self, cfg: SourceConfig, ctx: Context):
        self.cfg, self.ctx = cfg, ctx

    # ---- stream -----------------------------------------------------------
    def poll(self) -> list[RawItem]:
        """Fetch (archiving every response) and parse. Default: GET each url, parse it."""
        items: list[RawItem] = []
        for name, url in self.cfg.urls.items():
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"{self.doc_type}:{name}",
                                       expect=self.cfg.health_rules.expect)
            if res.not_modified:
                continue
            items += self.parse(res, name)
        return items

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        """Pure: bytes in, items out. Must not do network I/O (replay depends on it)."""
        raise NotImplementedError

    # ---- scheduled --------------------------------------------------------
    def calendar(self) -> list[ScheduledEvent]:
        return []


def resolve(adapter_path: str) -> type[SourceAdapter]:
    mod, cls = adapter_path.split(":")
    m = importlib.import_module(f"events_bot.sources.{mod}")
    klass = getattr(m, cls)
    if not issubclass(klass, SourceAdapter):
        raise TypeError(f"{adapter_path} is not a SourceAdapter")
    return klass


def build(cfg: SourceConfig, ctx: Context) -> SourceAdapter:
    return resolve(cfg.adapter)(cfg, ctx)
