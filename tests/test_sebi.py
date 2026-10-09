"""SEBI listings: parsing, board-outcome tagging, date-only freshness."""
from datetime import date, datetime, timedelta, timezone

from events_bot.core.models import FetchResult
from events_bot.sources.india import sebi

from .conftest import FIXTURES

FX = FIXTURES / "sebi"


def _res(name: str) -> FetchResult:
    return FetchResult(url="u", final_url="u", status=200, fetched_at=datetime.now(timezone.utc),
                       content=(FX / f"listing_{name}_2026-10-07.html").read_bytes())


def test_listings_parse_ids_dates_titles():
    for name in ("circulars", "press", "consultation"):
        rows = sebi.parse_listing(_res(name).content)
        assert len(rows) == 25, name
        assert all(r["id"] > 100000 and r["date"] for r in rows)
    c = sebi.parse_listing(_res("consultation").content)
    assert not any("Click here" in r["title"] for r in c)
    p = sebi.parse_listing(_res("press").content)
    assert p[0]["number"] == "62/2026" and p[0]["date"] == date(2026, 10, 5)


def test_board_outcome_tagged_and_kind(make_app):
    app = make_app()
    items = app.adapter("sebi_pr").parse(_res("press"), "press")
    board = [i for i in items if "board_outcome" in i.meta["priority_tags"]]
    assert board and "Key decisions taken in the SEBI Board Meeting" in board[0].title
    assert all(i.meta["kind"] == "Press release" and i.meta["date_only"] for i in items)


def test_circular_goes_to_the_daily_digest(make_app):
    """F-29: SEBI circulars are digest items (detailed write-up at 18:30), not realtime alerts."""
    from events_bot.core.pipeline import PollStats
    app = make_app("dry_run")
    it = app.adapter("sebi_circ").parse(_res("circulars"), "circulars")[0]
    late_evening = it.source_published_at + timedelta(hours=19, minutes=30)
    iid = app.db.insert_item(it, url_norm=it.url, title_norm=it.title.lower(), tier=1, first_seen_at=late_evening)
    st = PollStats()
    app.pipeline.deliver(app.sources["sebi_circ"], iid, it, late_evening, st)
    assert st.realtime == 0 and st.queued == 1
    assert app.db.one("select route from items where id = %s", (iid,))["route"] == "india_eod"
