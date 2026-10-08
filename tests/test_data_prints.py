"""Data-print windows: MoSPI ARC parsing, OEA rule dates, calendar events, window run, reconcile, dry-run gate."""
import json
from datetime import date, datetime, timezone

import httpx
import pytest

from events_bot.core.timeutil import IST
from events_bot.sources.india import data_prints as dp

from .conftest import FIXTURES, mock_fetcher, requires_db

FX = FIXTURES / "data_prints"
ARC = FX / "ARC_2026_27_Sept_2026.pdf"


def test_arc_rows_and_weekend_shift_match_actual_releases():
    rows = [r for r in dp.parse_arc(ARC.read_bytes()) if r["kind"]]
    planned = {(r["kind"], r["planned"]) for r in rows}
    assert ("cpi", date(2026, 10, 12)) in planned and ("iip", date(2026, 10, 28)) in planned
    assert ("gdp", date(2026, 11, 30)) in planned and ("cpi", date(2027, 3, 12)) in planned
    # The ARC planned these on weekends; MoSPI released them on the Monday (PIB links in the same PDF).
    assert dp.next_working_day(date(2026, 9, 12)) == date(2026, 9, 14)      # CPI Aug 2026
    assert dp.next_working_day(date(2026, 7, 12)) == date(2026, 7, 13)      # CPI Jun 2026
    assert dp.next_working_day(date(2026, 6, 28)) == date(2026, 6, 29)      # IIP May 2026
    # Not data prints: PLFS, flash reports, first advance estimates.
    assert not any("PLFS" in r["title"] or "Flash" in r["title"] for r in rows)
    assert dp.arc_kind("First Advance Estimates of GDP for FY 2026-27") is None


def test_oea_rule_dates():
    d = dict(((k, x.month), x) for k, x in dp.oea_dates(date(2026, 10, 8), months=3))
    assert d[("wpi", 10)] == date(2026, 10, 14) and d[("ici", 10)] == date(2026, 10, 20)
    assert d[("wpi", 11)] == date(2026, 11, 16)          # 14 Nov 2026 is a Saturday
    assert d[("ici", 12)] == date(2026, 12, 21)          # 20 Dec 2026 is a Sunday


def _arc_handler(req: httpx.Request) -> httpx.Response:
    if str(req.url) == dp.ARC_API:
        return httpx.Response(200, content=(FX / "arc_pointer_2026-10-08.json").read_bytes())
    assert "releaseCalender" in str(req.url)
    return httpx.Response(200, content=ARC.read_bytes())


@requires_db
def test_calendar_events(make_app, monkeypatch):
    monkeypatch.setattr(dp, "utcnow", lambda: datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc))
    app = make_app()
    mock_fetcher(app, _arc_handler)
    evs = {e.ref: e for e in app.adapter("data_prints").calendar()}
    cpi = evs["data:cpi:2026-10-12"]
    assert cpi.scheduled_at == datetime(2026, 10, 12, 16, 0, tzinfo=IST)
    assert cpi.window_start == datetime(2026, 10, 12, 15, 50, tzinfo=IST)
    assert cpi.meta["stream_source"] == "mospi"
    assert {"data:iip:2026-10-28", "data:cpi:2026-11-12", "data:iip:2026-11-30", "data:gdp:2026-11-30",
            "data:wpi:2026-10-14", "data:ici:2026-10-20", "data:wpi:2026-11-16"} <= set(evs)
    assert evs["data:wpi:2026-10-14"].scheduled_at == datetime(2026, 10, 14, 12, 0, tzinfo=IST)
    assert not any(e.scheduled_at.date() > date(2026, 12, 9) for e in evs.values())   # 62-day horizon
    assert app.refresh_calendars() >= len(evs)


@requires_db
@pytest.mark.parametrize("stream_live", [None, "true"])
def test_window_run_finds_print_marks_event_and_follows_stream_switch(make_app, monkeypatch, stream_live):
    if stream_live:
        monkeypatch.setenv("EVENTS_BOT_LIVE", stream_live)
    else:
        monkeypatch.delenv("EVENTS_BOT_LIVE", raising=False)
    app = make_app("live")
    payload = json.loads((FIXTURES / "mospi" / "latest_2026-10-07.json").read_bytes())
    today = datetime.now(IST).date().isoformat()
    for r in payload["data"]:
        if str(r["id"]) == "3606":
            r["published_year"] = today            # make the CPI row fresh enough for realtime
    cpi_pdf = (FIXTURES / "mospi" / "cpi_2026-08.pdf").read_bytes()
    seen_modes = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, content=json.dumps(payload).encode())
        return httpx.Response(200, content=cpi_pdf)

    mock_fetcher(app, handler)
    orig = app.pipeline.dispatcher.__class__.dispatch

    def spy(self, msg, recipients):
        seen_modes.append(self.mode)
        if self.mode == "live":
            from events_bot.deliver.interface import DispatchResult
            return DispatchResult(status="sent")
        return orig(self, msg, recipients)

    monkeypatch.setattr(app.pipeline.dispatcher.__class__, "dispatch", spy)
    # mospi has been polled before (not a baseline), and an armed CPI window is open.
    app.db.health_ok("mospi", datetime.now(timezone.utc), "ok", 0)
    from events_bot.core.models import RawItem
    app.db.insert_item(RawItem(source_id="mospi", ext_id="id:1", url="https://x/1", title="old"),
                       url_norm="x1", title_norm="old", tier=1, first_seen_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    now = datetime.now(timezone.utc)
    ev = dp.event_for("cpi", date.today(), date.today(), dp.ARC_API, "CPI")
    ev = ev.model_copy(update={"scheduled_at": now, "window_start": now, "window_end": now})
    app.db.upsert_event(ev, now)

    app.adapter("data_prints").poll_event(app.db.get_event(ev.ref))

    assert seen_modes == ["dry_run" if stream_live is None else "live"]
    got = app.db.get_event(ev.ref)
    assert got["status"] == "captured" and got["meta"]["item"] == "id:3606" and got["meta"].get("stage1_at")
    assert app.pipeline.dispatcher.mode == "live"          # restored after the window run


@requires_db
def test_embargo_holds_print_until_release_time(make_app):
    """MoSPI/OEA files show up minutes before 16:00 / 12:00 (T-09): hold them back until the release."""
    app = make_app()
    payload = (FIXTURES / "mospi" / "latest_2026-10-07.json").read_bytes()
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.method)
        return httpx.Response(200, content=payload)

    mock_fetcher(app, handler)
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    ev = dp.event_for("cpi", date.today(), date.today(), dp.ARC_API, "CPI")
    ev = ev.model_copy(update={"scheduled_at": now + timedelta(minutes=20),
                               "window_start": now - timedelta(minutes=1), "window_end": now + timedelta(hours=1)})
    app.db.upsert_event(ev, now)
    assert dp.held_kinds(app.db) == {"cpi"}
    kinds = {i.meta["kind"] for i in app.adapter("mospi").poll()}
    assert "cpi" not in kinds and "iip" in kinds
    calls.clear()
    app.adapter("data_prints").poll_event(app.db.get_event(ev.ref))     # window open, release not yet
    assert calls == []
