"""Scheduled windows for India's data prints: MoSPI CPI / IIP / GDP and OEA WPI / Eight Core Industries.

The prints themselves are found and rendered by the `mospi` and `oea` stream adapters (validated extraction,
print-vs-prior alert). This source only turns the release calendars into events, so `calendars` arms a burst
job and `windows` polls the publisher every 30 s around release time instead of waiting for a 15-min tick.

Calendars:
- MoSPI: the Advance Release Calendar PDF. `GET /api/documents/get-latest-release-calender` returns the
  current file (re-issued monthly, e.g. "ARC_2026_27_Sept_2026.pdf"). Its table rows are
  (month, "12th Oct", "All India Consumer Price Index (CPI)", ...). Footnote: "In case of holidays, reports
  release on next working day"; weekends are moved to Monday here, gazetted holidays are not known, so a
  print that slips past its window is matched later by `reconcile` (and the missed: alert clears).
  CPI, IIP and quarterly GDP release at 16:00 IST.
- OEA publishes no calendar. Rule from its own archive file names: WPI on the 14th at 12:00 IST, Eight
  Core Industries on the 20th at 17:00 IST, each moved to Monday when it falls on a weekend.

Sends: windows.yml runs live when EVENTS_BOT_LIVE is "scheduled", but these prints are stream items, so they
follow the stream switch: unless EVENTS_BOT_LIVE is "true" the window run renders them in dry run.
"""
from __future__ import annotations

import io
import json
import os
import re
from datetime import date, datetime, time, timedelta

import pdfplumber

from ...core.models import ScheduledEvent
from ...core.registry import SourceAdapter
from ...core.timeutil import IST, utcnow
from ...deliver.interface import Dispatcher

ARC_API = "https://www.mospi.gov.in/api/documents/get-latest-release-calender"
MOSPI = "https://www.mospi.gov.in/"
OEA_HOME = "https://eaindustry.nic.in/"
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
SPEC = {   # kind -> (stream source, release time IST, label)
    "cpi": ("mospi", time(16, 0), "CPI inflation (MoSPI)"),
    "iip": ("mospi", time(16, 0), "Industrial production, IIP (MoSPI)"),
    "gdp": ("mospi", time(16, 0), "GDP quarterly estimates (MoSPI)"),
    "wpi": ("oea", time(12, 0), "WPI inflation (OEA)"),
    "ici": ("oea", time(17, 0), "Eight Core Industries (OEA)"),
}
BEFORE, AFTER = timedelta(minutes=10), timedelta(minutes=45)
HORIZON_DAYS = 62


def held_kinds(db) -> set[str]:
    """Data-print kinds under embargo now: a release scheduled within the next 12 h (FLAGS T-09). MoSPI and
    OEA files appear up to ~16 min before the official time, so the stream adapters hold back new items of
    these kinds until the release time passes; the next poll (window or tick) then picks them up."""
    if db is None:
        return set()
    return {r["event_type"] for r in db.q(
        "select distinct event_type from events where source_id = 'data_prints' "
        "and scheduled_at > now() and scheduled_at < now() + interval '12 hours'")}


def arc_kind(title: str) -> str | None:
    t = title.lower()
    if "consumer price index" in t and "industrial workers" not in t:
        return "cpi"
    if "index of industrial production" in t and "first press release" not in t:
        return "iip"
    if "quarterly estimates of gdp" in t:
        return "gdp"
    return None


def next_working_day(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


_DAY = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s*([A-Za-z]{3,9})")
_SECTION = re.compile(r"([A-Za-z]{3,9})\s*(\d{4})")


def parse_arc(pdf_bytes: bytes) -> list[dict]:
    """ARC table -> [{planned: date, title, kind}] for every row; kind None for non-print releases."""
    out, year, sec_month = [], None, None
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    cells = [re.sub(r"\s+", " ", c or "").strip() for c in row]
                    for c in cells:                      # "Oct 2026" section header in the month column
                        m = _SECTION.fullmatch(c)
                        if m and m.group(1)[:3].lower() in MONTHS:
                            sec_month, year = MONTHS[m.group(1)[:3].lower()], int(m.group(2))
                    day_cell = next((c for c in cells if _DAY.fullmatch(c) and
                                     _DAY.fullmatch(c).group(2)[:3].lower() in MONTHS), None)
                    if day_cell is None or year is None:
                        continue
                    title = max((c for c in cells if c and c != day_cell and not _SECTION.fullmatch(c)
                                 and not c.startswith("Released on")), key=len, default="")
                    m = _DAY.fullmatch(day_cell)
                    month = MONTHS[m.group(2)[:3].lower()]
                    y = year + 1 if sec_month and month < sec_month and sec_month - month > 6 else year
                    try:
                        planned = date(y, month, int(m.group(1)))
                    except ValueError:
                        continue
                    out.append({"planned": planned, "title": title, "kind": arc_kind(title)})
    return out


def oea_dates(today: date, months: int = 3) -> list[tuple[str, date]]:
    out = []
    y, m = today.year, today.month
    for _ in range(months):
        out += [("wpi", next_working_day(date(y, m, 14))), ("ici", next_working_day(date(y, m, 20)))]
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def event_for(kind: str, planned: date, release_day: date, calendar_url: str, title: str) -> ScheduledEvent:
    source, at, label = SPEC[kind]
    release = datetime.combine(release_day, at, tzinfo=IST)
    return ScheduledEvent(
        ref=f"data:{kind}:{release_day.isoformat()}", source_id="data_prints", event_type=kind,
        title=f"{label} {release_day:%d %b %Y}", scheduled_at=release, window_start=release - BEFORE,
        window_end=release + AFTER, calendar_url=calendar_url,
        meta={"kind": kind, "stream_source": source, "planned": planned.isoformat(), "calendar_title": title})


class DataPrints(SourceAdapter):
    doc_type = "data_prints"

    def calendar(self) -> list[ScheduledEvent]:
        today = utcnow().astimezone(IST).date()
        horizon = today + timedelta(days=HORIZON_DAYS)
        evs: list[ScheduledEvent] = []
        ptr = self.ctx.fetcher.get(ARC_API, source_id=self.cfg.id, doc_type="data_prints:arc_pointer",
                                   expect="json", conditional=False)
        path = (json.loads(ptr.content).get("data") or {}).get("url")
        if not path:
            raise ValueError("MoSPI ARC pointer has no url")
        arc_url = MOSPI + path.lstrip("/")
        pdf = self.ctx.fetcher.get_with_retry(arc_url, tries=3, base_delay=5, source_id=self.cfg.id,
                                              doc_type="data_prints:arc_pdf", expect="pdf", conditional=False,
                                              parent_id=ptr.document_id)
        seen: set[str] = set()
        for r in parse_arc(pdf.content):
            if r["kind"] is None:
                continue
            day = next_working_day(r["planned"])
            if not (today - timedelta(days=1) <= day <= horizon):
                continue
            ev = event_for(r["kind"], r["planned"], day, arc_url, r["title"])
            if ev.ref not in seen:
                seen.add(ev.ref)
                evs.append(ev)
        for kind, day in oea_dates(today):
            if today - timedelta(days=1) <= day <= horizon:
                evs.append(event_for(kind, day, day, OEA_HOME, "rule: WPI 14th 12:00 / ICI 20th 17:00 IST"))
        return evs

    # ---- in the window ------------------------------------------------------
    def poll_event(self, ev: dict) -> None:
        app = self.ctx.app
        meta = ev.get("meta") or {}
        if meta.get("stage1_at") or utcnow() < ev["scheduled_at"]:
            return          # done, or still under embargo (the window opens 10 min early only to be ready)
        src = meta.get("stream_source") or SPEC[ev["event_type"]][0]
        orig = app.pipeline.dispatcher
        if orig.mode == "live" and os.environ.get("EVENTS_BOT_LIVE", "") != "true":
            app.pipeline.dispatcher = Dispatcher(app.db, orig.channels, "dry_run", orig.out_dir)
        try:
            app.poll_source(src)
        finally:
            app.pipeline.dispatcher = orig
        self.reconcile()

    def reconcile(self) -> int:
        """Mark open data-print events whose print has arrived through the stream (window or tick)."""
        db = self.ctx.db
        open_evs = db.q("""select ref, event_type, scheduled_at, meta from events
                           where source_id = %s and not (meta ? 'stage1_at')
                             and window_start < now() and window_start > now() - interval '10 days'""",
                        (self.cfg.id,))
        n = 0
        for e in open_evs:
            src = (e["meta"] or {}).get("stream_source") or SPEC[e["event_type"]][0]
            hit = db.one("""select first_seen_at, ext_id from items
                            where source_id = %s and meta->>'kind' = %s
                              and status not in ('baseline', 'duplicate')
                              and first_seen_at > %s - interval '1 day'
                            order by first_seen_at limit 1""",
                         (src, e["event_type"], e["scheduled_at"]))
            if hit:
                db.mark_event(e["ref"], status="captured", stage1_at=hit["first_seen_at"], item=hit["ext_id"])
                n += 1
        return n
