"""Base class for scheduled US data prints (BLS, BEA, Census, DOL).

A print adapter supplies two things:
  schedule()          -> [ScheduledRelease]: the agency calendar (ICS / JSON / HTML / rule), fetched live
  capture(rel, meta)  -> Capture | None: fetch the release; None while it is not posted yet, or while the
                         posted document is still the previous period (cached / pre-embargo page)
and a PRINTS map of kind -> stage1.usprint.PrintSpec.

The base turns the schedule into events (ref `usprint:<kind>:<YYYY-MM-DD>`), polls inside the window
(windows.yml, at the source's window_poll_interval), validates, renders the print-vs-prior alert and sends
it once (`stage1_at` on the event; `sends` is unique on ref too). A print still missing at window end is
reported by ops/health.py as `missed:<ref>`, like every other scheduled event.

Delivery gate (FLAGS F-08 / F-30): `delivery.us_realtime_prints` true sends the alert to the desk in real
time; false renders it in dry-run only (written to out/, never e-mailed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog

from ...core.models import ScheduledEvent
from ...core.registry import SourceAdapter
from ...core.timeutil import ET, utcnow
from ...deliver.interface import Dispatcher
from ...extract.validator import validate_all
from ...stage1 import usprint
from ...stage1.usprint import Capture, PrintSpec

log = structlog.get_logger()

# Browser header set for www.bls.gov (Akamai): a Chrome UA alone gets 403 (phase0/us_data.md flag 1).
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


@dataclass
class ScheduledRelease:
    kind: str                         # key of PRINTS
    release_at: datetime              # tz-aware official release time
    calendar_url: str
    title: str = ""                   # calendar's own name for the release, verbatim
    meta: dict = field(default_factory=dict)


def at_et(d, hh: int, mm: int) -> datetime:
    """Release time in America/New_York, so 08:30 ET is 18:00 IST in summer and 19:00 IST in winter."""
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)


class ScheduledPrint(SourceAdapter):
    PRINTS: dict[str, PrintSpec] = {}
    BEFORE = timedelta(minutes=10)
    AFTER = timedelta(minutes=60)
    HORIZON = timedelta(days=45)

    # ---- to implement -------------------------------------------------------
    def schedule(self) -> list[ScheduledRelease]:
        raise NotImplementedError

    def capture(self, rel: ScheduledRelease, meta: dict) -> Capture | None:
        raise NotImplementedError

    # ---- calendar -------------------------------------------------------------
    def calendar(self) -> list[ScheduledEvent]:
        now = utcnow()
        out, seen = [], set()
        for r in self.schedule():
            if r.kind not in self.PRINTS:
                continue
            if not (now - timedelta(days=1) <= r.release_at <= now + self.HORIZON):
                continue
            ref = f"usprint:{r.kind}:{r.release_at.astimezone(ET).date().isoformat()}"
            if ref in seen:
                continue
            seen.add(ref)
            spec = self.PRINTS[r.kind]
            out.append(ScheduledEvent(
                ref=ref, source_id=self.cfg.id, event_type=r.kind,
                title=f"{spec.name} ({r.release_at.astimezone(ET):%d %b %Y %H:%M} ET)",
                scheduled_at=r.release_at, window_start=r.release_at - self.BEFORE,
                window_end=r.release_at + self.AFTER, calendar_url=r.calendar_url,
                meta={"kind": r.kind, "calendar_title": r.title, **r.meta}))
        return out

    # ---- in the window ----------------------------------------------------------
    def poll_event(self, ev: dict) -> None:
        meta = ev.get("meta") or {}
        now = utcnow()
        if meta.get("stage1_at") or now < ev["scheduled_at"]:
            return                    # sent already, or still under embargo
        kind = ev["event_type"]
        rel = ScheduledRelease(kind=kind, release_at=ev["scheduled_at"], calendar_url=ev["calendar_url"],
                               title=meta.get("calendar_title") or "", meta=meta)
        cap = self.capture(rel, meta)
        if cap is None:
            return
        self.send(ev["ref"], kind, cap, first_seen=now)

    def send(self, ref: str, kind: str, cap: Capture, *, first_seen: datetime) -> None:
        app = self.ctx.app
        validate_all(cap.extractions, cap.texts)
        disp = app.dispatcher
        if not app.delivery.us_realtime_prints and disp.mode == "live":
            disp = Dispatcher(app.db, disp.channels, "dry_run", disp.out_dir)
        msg = usprint.build(self.PRINTS[kind], cap, ref=ref, first_seen=first_seen, mode=disp.mode)
        self._store(cap)
        res = disp.dispatch(msg, app.settings.recipients)
        log.info("us_print_sent", ref=ref, status=res.status, period=cap.period)
        if res.status not in ("failed", "no_recipients"):
            self.ctx.db.mark_event(ref, "captured", stage1_at=first_seen, period=cap.period)

    def _store(self, cap: Capture) -> None:
        for d in cap.docs:
            row = self.ctx.db.latest_document(d.url)
            if row is None:
                continue
            doc_key = next((k for k in cap.texts if any(e.doc == k for e in cap.extractions)), None)
            if doc_key is None:
                continue
            self.ctx.db.set_document_text(row["id"], cap.texts[doc_key])
            self.ctx.db.replace_extractions(row["id"], [e for e in cap.extractions if e.doc == doc_key])
            break
