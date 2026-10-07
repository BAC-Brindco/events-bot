"""Stream pipeline: every new item goes insert -> dedupe -> filter -> classify -> tag -> route.

Routing:
  realtime          Stage 1 now (India Tier 1; US items per F-08 toggles)
  us_morning_wrap   queued for the 07:00 IST wrap
  india_eod         queued for the 19:30 IST digest
  weekly            queued for the Saturday digest

Two guards stop floods of old items:
  * baseline  - the first successful poll of a source records what is already in the
                feed without alerting.
  * max age   - a realtime item whose source time is older than
                delivery.realtime_max_age_hours (e.g. after an outage) is queued for the
                country's digest instead of alerting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import structlog

from ..deliver.interface import Dispatcher
from ..filter.classifier import Classifier, PassThrough
from ..filter.keywords import KeywordFilter
from ..filter.watchlist import Watchlist
from ..stage1.build import stage1_item
from . import dedupe
from .config import DeliveryConfig
from .db import Database
from .models import RawItem, SourceConfig
from .timeutil import utcnow

log = structlog.get_logger()

DIGEST_FOR_COUNTRY = {"IN": "india_eod", "US": "us_morning_wrap"}


@dataclass
class PollStats:
    seen: int = 0
    new: int = 0
    baseline: int = 0
    duplicate: int = 0
    filtered: int = 0
    realtime: int = 0
    queued: int = 0
    send_failed: int = 0
    pending_llm: int = 0
    refs: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, db: Database, delivery: DeliveryConfig, keyword_filter: KeywordFilter,
                 watchlist: Watchlist, dispatcher: Dispatcher, recipients: list[str],
                 classifier: Classifier | None = None):
        self.db, self.delivery, self.kf, self.watchlist = db, delivery, keyword_filter, watchlist
        self.dispatcher, self.recipients = dispatcher, recipients
        self.classifier = classifier or PassThrough()

    def is_baseline(self, cfg: SourceConfig) -> bool:
        row = self.db.one("select exists(select 1 from items where source_id = %s) as has", (cfg.id,))
        return not row["has"]

    def process(self, cfg: SourceConfig, items: list[RawItem], enrich=None, render=None) -> PollStats:
        st = PollStats(seen=len(items))
        baseline = self.is_baseline(cfg)
        for it in items:
            now = utcnow()
            item_id = self.db.insert_item(it, url_norm=dedupe.normalise_url(it.url),
                                          title_norm=dedupe.normalise_title(it.title),
                                          tier=cfg.tier, first_seen_at=now)
            if item_id is None:
                continue
            st.new += 1
            st.refs.append(it.ref)
            if baseline:
                self.db.update_item(item_id, status="baseline")
                st.baseline += 1
                continue
            d = dedupe.check(self.db, item_id=item_id, source_id=cfg.id,
                             url_norm=dedupe.normalise_url(it.url),
                             title_norm=dedupe.normalise_title(it.title), first_seen_at=now)
            if d.is_dup:
                self.db.update_item(item_id, status="duplicate", dup_of=d.dup_of, dup_rule=d.rule)
                st.duplicate += 1
                continue
            dec = self.kf.decide(cfg, it)
            if dec.keep:
                dec = self.classifier.classify(cfg, it)
            if not dec.keep and dec.rule == "uncertain":
                self.db.update_item(item_id, status="pending_llm", meta={**it.meta, "filter": dec.reason})
                st.pending_llm += 1
                continue
            if not dec.keep:
                self.db.insert_filtered(item_id, cfg.id, it.title, it.url, dec.rule, dec.reason)
                self.db.update_item(item_id, status="filtered")
                st.filtered += 1
                continue
            self.deliver(cfg, item_id, it, now, st, enrich, render=render)
        return st

    def deliver(self, cfg: SourceConfig, item_id: int, it: RawItem, now, st: PollStats, enrich=None,
                route: str | None = None, extra_tags: list[str] | None = None, render=None) -> None:
        """Route one kept item: optional enrichment (detail page), tags, realtime send or digest queue."""
        if enrich is not None:
            try:
                it = enrich(it)
            except Exception as e:  # noqa: BLE001  a detail-page failure must not lose the item
                log.warning("enrich_failed", ref=it.ref, error=str(e)[:200])
        tags = self.watchlist.tags(it) + list(extra_tags or []) + list(it.meta.get("priority_tags") or [])
        route = route or self.delivery.route_for(cfg)
        published = it.source_published_at
        if published is not None and it.meta.get("date_only"):
            published += timedelta(days=1)          # a date-only stamp could mean any time that day
        if route == "realtime" and published is not None and \
                now - published > timedelta(hours=self.delivery.realtime_max_age_hours):
            route = DIGEST_FOR_COUNTRY[cfg.country]
            log.warning("item_too_old_for_realtime", ref=it.ref, published=str(it.source_published_at))
        if route != "realtime":
            self.db.update_item(item_id, status="queued", route=route, tags=tags, meta=it.meta)
            st.queued += 1
            return
        msg = render(it, first_seen=now, mode=self.dispatcher.mode) if render is not None else None
        msg = msg or stage1_item(cfg, it, first_seen_at=now, tags=tags, mode=self.dispatcher.mode)
        res = self.dispatcher.dispatch(msg, self.recipients)
        if res.status in ("failed", "no_recipients"):
            self.db.update_item(item_id, status="error", route=route, tags=tags,
                                meta={**it.meta, "dispatch": res.status, "detail": res.detail})
            st.send_failed += 1
        else:
            self.db.update_item(item_id, status="routed", route=route, tags=tags, meta=it.meta)
            st.realtime += 1
