"""APScheduler wiring (threads; each job is short, I/O-bound and has max_instances=1).

Jobs
  poll:<source>     every poll_interval (stream/both), skipped outside active_hours
  windows           every 30 s: scheduled events inside T-10..T+90 get polled at
                    window_poll_interval by their adapter (Phase 2 fills poll_event)
  calendars         daily 06:00 IST, and once at start
  health            every 5 min
  digests           cron from delivery.yaml (built in Phases 3, 6, 7)
"""
from __future__ import annotations

import random
import time as _time
from datetime import timedelta

import structlog
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .app import App
from .timeutil import IST, utcnow

log = structlog.get_logger()


def _in_active_hours(app: App, sid: str) -> bool:
    ah = app.sources[sid].active_hours
    return ah is None or ah.contains(utcnow().astimezone(IST).time())


def poll_job(app: App, sid: str) -> None:
    if _in_active_hours(app, sid):
        app.poll_source(sid)


class WindowPoller:
    """Polls scheduled events inside their window at the source's window_poll_interval."""

    def __init__(self, app: App):
        self.app = app
        self.last: dict[str, float] = {}

    def tick(self) -> None:
        now = utcnow()
        for ev in self.app.db.events_between(now - timedelta(hours=3), now + timedelta(minutes=15)):
            if not (ev["window_start"] <= now <= ev["window_end"]):
                continue
            cfg = self.app.sources.get(ev["source_id"])
            if cfg is None or not cfg.enabled:
                continue
            every = cfg.window_poll_interval or 60
            if _time.monotonic() - self.last.get(ev["ref"], 0) < every:
                continue
            self.last[ev["ref"]] = _time.monotonic()
            adapter = self.app.adapter(cfg.id)
            poll_event = getattr(adapter, "poll_event", None)
            if poll_event is None:
                log.warning("no_poll_event", source=cfg.id, ref=ev["ref"])
                continue
            try:
                poll_event(ev)
            except Exception as e:  # noqa: BLE001
                self.app.db.health_error(cfg.id, now, f"window {ev['ref']}: {type(e).__name__}: {e}")


def digest_job(kind: str) -> None:
    log.warning("digest_not_built", kind=kind, note="digests arrive in Phases 3 (US wrap), 6 (EOD), 7 (weekly)")


def build_scheduler(app: App) -> BlockingScheduler:
    s = BlockingScheduler(timezone=IST, executors={"default": ThreadPoolExecutor(8)},
                          job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120})
    for sid, cfg in app.sources.items():
        if not cfg.enabled or cfg.kind == "scheduled":
            continue
        # Stagger first runs so every source does not hit the network in the same second.
        s.add_job(poll_job, IntervalTrigger(seconds=cfg.poll_interval, jitter=min(10, cfg.poll_interval // 10)),
                  args=(app, sid), id=f"poll:{sid}",
                  next_run_time=utcnow() + timedelta(seconds=random.uniform(1, 15)))
    wp = WindowPoller(app)
    s.add_job(wp.tick, IntervalTrigger(seconds=30), id="windows")
    s.add_job(app.refresh_calendars, CronTrigger(hour=6, minute=0, timezone=IST), id="calendars",
              next_run_time=utcnow() + timedelta(seconds=5))
    s.add_job(app.check_health, IntervalTrigger(minutes=5), id="health")
    d = app.delivery
    for kind, spec in (("us_morning_wrap", d.us_morning_wrap), ("india_eod", d.india_eod), ("weekly", d.weekly)):
        s.add_job(digest_job, CronTrigger(timezone=IST, **spec.model_dump(exclude_none=True)),
                  args=(kind,), id=f"digest:{kind}")
    return s
