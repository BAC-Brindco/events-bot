"""IMD press-release list: English rows only, monsoon/seasonal kept, daily bulletin dropped; tick throttle."""
from datetime import date, datetime, timedelta, timezone

import httpx

from events_bot.core.models import FetchResult
from events_bot.sources.india import imd

from .conftest import FIXTURES, mock_fetcher, requires_db

FX = FIXTURES / "imd" / "press_release_mausam_head_2026-10-08.html"


def _res() -> FetchResult:
    return FetchResult(url="u", final_url="u", status=200, fetched_at=datetime.now(timezone.utc),
                       content=FX.read_bytes())


def test_parse_list_newest_first_with_pdf_links():
    rows = imd.parse_list(FX.read_bytes())
    assert rows[0]["id"] == 5386 and rows[0]["hindi"] and rows[0]["date"] == date(2026, 10, 7)
    assert rows[1]["url"] == "https://internal.imd.gov.in/press_release/20261007_pr_5385.pdf"
    assert len(rows) == imd.NEWEST


@requires_db
def test_filter_keeps_monsoon_and_outlooks(make_app):
    app = make_app()
    a, cfg, kf = app.adapter("imd"), app.sources["imd"], app.pipeline.kf
    items = a.parse(_res(), "press")
    assert items and not any(imd._DEVANAGARI.search(i.title) for i in items)
    kept = {i.ext_id for i in items if kf.decide(cfg, i).keep}
    assert {"pr:5356", "pr:5361", "pr:5326", "pr:5324"} <= kept          # outlook, salient features, withdrawal
    assert "pr:5385" not in kept                                          # daily bulletin
    assert len(kept) <= 8


@requires_db
def test_tick_throttles_imd_to_its_interval(make_app):
    app = make_app()
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, content=b"<rss></rss>" if req.url.path.endswith(".xml") else b"{}")

    mock_fetcher(app, handler)
    for sid, cfg in app.sources.items():           # only IMD in this tick
        if sid != "imd":
            app.sources[sid] = cfg.model_copy(update={"enabled": False})
    app.sources["imd"] = app.sources["imd"].model_copy(update={"active_hours": None})
    app.db.sync_sources(app.sources)
    now = datetime.now(timezone.utc)
    app.db.health_ok("imd", now - timedelta(minutes=20), "ok", 0)
    assert app.tick()["imd"] == "throttled" and calls == []
    app.db.health_ok("imd", now - timedelta(minutes=59), "ok", 0)
    app.tick()
    assert calls == [imd.PAGE]
