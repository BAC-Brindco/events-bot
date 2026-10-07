"""Wires settings, DB, archive, fetcher, registry, pipeline and delivery together."""
from __future__ import annotations

import structlog

from ..deliver.email import EmailChannel
from ..deliver.interface import Dispatcher, Mode
from ..filter.keywords import KeywordFilter
from ..filter.watchlist import Watchlist
from ..ops import health
from . import registry
from .archive import Archive, DbArchive
from .config import load_delivery, load_sources
from .db import Database
from .fetch import FetchError, Fetcher
from .models import SourceConfig
from .pipeline import PollStats, Pipeline
from .settings import Settings
from .timeutil import utcnow

log = structlog.get_logger()


class App:
    def __init__(self, settings: Settings, mode: Mode = "live"):
        self.settings, self.mode = settings, mode
        self.db = Database(settings.dsn, settings.db_schema)
        self.archive = DbArchive(self.db) if settings.archive_backend == "db" else Archive(settings.archive_dir)
        self.fetcher = Fetcher(self.db, self.archive, settings.user_agent)
        self.sources = load_sources(settings.config_dir)
        self.delivery = load_delivery(settings.config_dir)
        self.ctx = registry.Context(settings=settings, db=self.db, fetcher=self.fetcher, archive=self.archive)
        self.dispatcher = Dispatcher(self.db, [EmailChannel(settings)], mode, settings.out_dir)
        # Operator alerts (health, missed events) go only to the operator, so they are sent for real
        # whenever SMTP is configured, even while desk alerts are still in dry run.
        ops_mode: Mode = "live" if (mode == "dry_run" and settings.smtp_host and settings.operator_emails
                                    and settings.ops_live) else mode
        self.ops_dispatcher = Dispatcher(self.db, [EmailChannel(settings)], ops_mode, settings.out_dir)
        self.pipeline = Pipeline(self.db, self.delivery, KeywordFilter(settings.config_dir),
                                 Watchlist(settings.config_dir / "watchlist.csv"), self.dispatcher,
                                 settings.recipients)
        self.ctx.app = self

    def setup(self) -> list[str]:
        applied = self.db.migrate(self.settings.migrations_dir)
        self.db.sync_sources(self.sources)
        return applied

    def adapter(self, source_id: str) -> registry.SourceAdapter:
        return registry.build(self.sources[source_id], self.ctx)

    def poll_source(self, source_id: str) -> PollStats | None:
        cfg: SourceConfig = self.sources[source_id]
        at = utcnow()
        try:
            adapter = self.adapter(source_id)
            items = adapter.poll()
            if cfg.health_rules.min_items is not None and 0 < len(items) < cfg.health_rules.min_items:
                raise FetchError(next(iter(cfg.urls.values())), "shape",
                                 f"parse yielded {len(items)} rows < min_items {cfg.health_rules.min_items}")
            stats = self.pipeline.process(cfg, items, enrich=getattr(adapter, "enrich", None),
                                          render=getattr(adapter, "render_stage1", None))
        except Exception as e:  # noqa: BLE001  a source failure must never kill the scheduler
            n = self.db.health_error(source_id, at, f"{type(e).__name__}: {e}")
            log.error("poll_failed", source=source_id, consecutive=n, error=str(e)[:300])
            return None
        self.db.health_ok(source_id, at, "ok", stats.new - stats.baseline)
        log.info("poll_ok", source=source_id, **{k: v for k, v in stats.__dict__.items() if k != "refs"})
        return stats

    def refresh_calendars(self) -> int:
        n = 0
        for sid, cfg in self.sources.items():
            if not cfg.enabled or cfg.kind == "stream":
                continue
            at = utcnow()
            try:
                evs = self.adapter(sid).calendar()
                for ev in evs:
                    self.db.upsert_event(ev, at)
                n += len(evs)
            except Exception as e:  # noqa: BLE001
                self.db.raise_alert(sid, "calendar_refresh", f"{type(e).__name__}: {e}"[:500])
                log.error("calendar_failed", source=sid, error=str(e)[:300])
            else:
                self.db.clear_alert(sid, "calendar_refresh")
        return n

    def tick(self) -> dict[str, str]:
        """One pass over every enabled stream source, then health. For cron-driven hosts
        (GitHub Actions), where each run is a fresh process: poll_interval is ignored
        because the trigger cadence sets the pace; active_hours still applies."""
        from .timeutil import IST
        now_ist = utcnow().astimezone(IST).time()
        out: dict[str, str] = {}
        for sid, cfg in self.sources.items():
            if not cfg.enabled or cfg.kind == "scheduled":
                continue
            if cfg.active_hours is not None and not cfg.active_hours.contains(now_ist):
                out[sid] = "off-hours"
                continue
            st = self.poll_source(sid)
            out[sid] = "error" if st is None else f"new {st.new} realtime {st.realtime} queued {st.queued} baseline {st.baseline}"
        self.check_health()
        return out

    def check_health(self) -> int:
        fresh = health.sync_alerts(self.db, health.evaluate(self.db, self.sources))
        for b in fresh:
            self.ops_dispatcher.dispatch(health.alert_message(b), self.settings.operator_emails)
        return len(fresh)

    def close(self) -> None:
        self.fetcher.close()
        self.db.close()
