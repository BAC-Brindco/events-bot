"""Source group `fed`: make_app + mock_fetcher end to end (poll -> scope -> realtime / digest queue / drop)."""
from datetime import datetime, timezone

import httpx
import pytest

from events_bot.core import pipeline as pipeline_mod
from events_bot.core.models import RawItem
from events_bot.core.timeutil import utcnow
from events_bot.sources.us import fed_docs

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "fed" / "docs"


def seed_baseline(app, sid):
    app.pipeline.process(app.sources[sid], [RawItem(source_id=sid, ext_id="baseline:0", url="https://x/0",
                                                    title="baseline row", source_published_at=utcnow())])


def files(routes: dict[str, str], seen: list | None = None):
    """Handler serving fixture files by URL path suffix; anything else 404."""
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req)
        for suffix, name in routes.items():
            if str(req.url).endswith(suffix):
                return httpx.Response(200, content=(FX / name).read_bytes())
        return httpx.Response(404, content=b"<html>not found</html>")
    return handler


def seed_fomc_meeting(app, at_utc: datetime):
    from datetime import timedelta
    from events_bot.core.models import ScheduledEvent
    app.db.upsert_event(ScheduledEvent(ref=f"fomc:{at_utc.date().isoformat()}", source_id="fomc",
                                       event_type="fomc_decision", title="FOMC decision", scheduled_at=at_utc,
                                       window_start=at_utc - timedelta(minutes=10),
                                       window_end=at_utc + timedelta(minutes=90), calendar_url="https://x/"), utcnow())


@pytest.fixture
def at(monkeypatch):
    def _set(dt: datetime):
        monkeypatch.setattr(pipeline_mod, "utcnow", lambda: dt)
        monkeypatch.setattr(fed_docs, "utcnow", lambda: dt)
    return _set


def test_fed_press_minutes_realtime_statement_dropped(make_app, at):
    app = make_app("dry_run")
    seed_baseline(app, "fed_press")
    seed_fomc_meeting(app, datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc))
    at(datetime(2026, 11, 18, 19, 6, tzinfo=timezone.utc))     # 6 min after the minutes' 14:00 EST release
    mock_fetcher(app, files({"press_monetary.xml": "press_monetary_2026-11-26.xml",
                             "monetary20261118a.htm": "monetary20261118a.htm",
                             "fomcminutes20261028.htm": "fomcminutes20261028.htm"}))
    st = app.poll_source("fed_press")
    assert st.realtime == 1
    m = app.db.get_item("item:fed_press:monetary20261118a.htm")
    assert m["status"] == "routed" and m["route"] == "realtime"
    st_row = app.db.get_item("item:fed_press:monetary20261028a.htm")
    assert st_row["status"] == "filtered"
    assert any(r["title"] == "Federal Reserve issues FOMC statement" and "fomc source" in r["reason"]
               for r in app.db.recent_filtered(50, "fed_press"))
    dr = app.db.get_item("item:fed_press:monetary20261124a.htm")
    assert dr["status"] == "queued" and dr["route"] == "us_morning_wrap"
    old = app.db.get_item("item:fed_press:monetary20261007a.htm")          # September minutes: too old for realtime
    assert old["route"] == "us_morning_wrap"
    out = list((app.settings.out_dir / "dry_run").glob("*.txt"))
    assert len(out) == 1
    body = out[0].read_text(encoding="utf-8")
    assert "FOMC minutes | Minutes of the Federal Open Market Committee, October 27-28, 2026" in body
    assert "In their consideration of monetary policy at this meeting, most participants expressed support" in body
    assert "fomcminutes20261028.pdf" in body


def test_minutes_render_falls_back_to_generic_when_documents_missing(make_app):
    app = make_app("dry_run")
    mock_fetcher(app, files({}))
    a = app.adapter("fed_press")
    it = RawItem(source_id="fed_press", ext_id="monetary20261118a.htm",
                 url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261118a.htm",
                 title="Minutes of the Federal Open Market Committee, October 27-28, 2026",
                 meta={"kind": "FOMC minutes", "route_hint": "realtime"})
    assert a.render_stage1(it, first_seen=utcnow(), mode="dry_run") is None


def test_fed_press_digest_body_discount_minutes(make_app):
    app = make_app()
    mock_fetcher(app, files({"monetary20261124a.htm": "monetary20261124a.htm"}))
    it = RawItem(source_id="fed_press", ext_id="monetary20261124a.htm",
                 url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261124a.htm",
                 title="Minutes of the Board's discount rate meetings on October 19 and October 28, 2026",
                 meta={"kind": "Discount-rate minutes"})
    body = app.adapter("fed_press").digest_body(it)
    assert body.paragraphs and all(p in body.text for p in body.paragraphs)
    assert any("discount rates" in p for p in body.paragraphs)


def test_beige_baseline_then_404_then_pdf_live_with_304_calendar(make_app, at):
    app = make_app()
    cal = (FX / "calendar_2026-11-26.json").read_bytes()
    state = {"pdf": False}
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path.endswith("calendar.json"):
            if req.headers.get("if-none-match") == '"cal1"':
                return httpx.Response(304, headers={"etag": '"cal1"'})
            return httpx.Response(200, content=cal, headers={"etag": '"cal1"'})
        if req.url.path.endswith("BeigeBook_20261125.pdf") and state["pdf"]:
            return httpx.Response(200, content=(FX / "BeigeBook_20261125.pdf").read_bytes())
        return httpx.Response(404, content=b"<html>Page not found</html>")

    mock_fetcher(app, handler)
    at(datetime(2026, 11, 25, 18, 50, tzinfo=timezone.utc))        # 13:50 ET: before the release
    st = app.poll_source("fed_beige")
    assert st.baseline == 55 and not any("BeigeBook" in str(r.url) for r in seen)   # no probe before 14:00 ET
    at(datetime(2026, 11, 25, 19, 0, 5, tzinfo=timezone.utc))      # 14:00:05 ET: still 404
    st = app.poll_source("fed_beige")
    assert st.new == 0 and app.db.get_item("item:fed_beige:beige:20261125") is None
    assert seen[-1].url.path.endswith("BeigeBook_20261125.pdf")
    state["pdf"] = True
    at(datetime(2026, 11, 25, 19, 15, tzinfo=timezone.utc))
    st = app.poll_source("fed_beige")                               # calendar 304 -> archived copy re-read
    assert any(r.url.path.endswith("calendar.json") and r.headers.get("if-none-match") for r in seen)
    assert (st.new, st.queued, st.realtime) == (1, 1, 0)
    row = app.db.get_item("item:fed_beige:beige:20261125")
    assert row["route"] == "us_morning_wrap" and row["title"] == "Beige Book, November 25, 2026"
    body = app.adapter("fed_beige").digest_body(RawItem(**{k: row[k] for k in ("source_id", "ext_id", "url", "title")}))
    assert body.paragraphs[0].startswith("Eight of the twelve Federal Reserve Districts reported growth")
    assert all(p in body.text.split("\n") for p in body.paragraphs)


def test_board_speeches_kept_and_dropped(make_app):
    app = make_app()
    seed_baseline(app, "fed_speeches")
    mock_fetcher(app, files({"speeches_and_testimony.xml": "speeches_and_testimony_2026-11-26.xml",
                             "jefferson20261119a.htm": "jefferson20261119a.htm"}))
    st = app.poll_source("fed_speeches")
    assert st.realtime == 0 and st.queued == 14 and st.filtered == 1     # Warsh's Opening Remarks: Chair, kept
    assert app.db.get_item("item:fed_speeches:jefferson20261119a.htm")["route"] == "us_morning_wrap"
    assert app.db.get_item("item:fed_speeches:waller20261120a.htm")["status"] == "filtered"
    it = RawItem(source_id="fed_speeches", ext_id="jefferson20261119a.htm",
                 url="https://www.federalreserve.gov/newsevents/speech/jefferson20261119a.htm",
                 title="Jefferson, Monetary Policy amid Frequent and Complex Shocks")
    body = app.adapter("fed_speeches").digest_body(it)
    assert len(body.paragraphs) >= 5 and all(p in body.text for p in body.paragraphs)


def test_treasury_auctions_coupon_queued_with_xml_figures_bill_dropped(make_app):
    app = make_app()
    seed_baseline(app, "treasury_auctions")
    mock_fetcher(app, files({"auctioned?format=json&days=7": "td_auctioned_2026-11-26.json",
                             "R_20261125_3.xml": "R_20261125_3.xml", "R_20261124_3.xml": "R_20261125_3.xml",
                             "R_20261124_4.xml": "R_20261124_4.xml", "R_20261123_3.xml": "R_20261125_3.xml",
                             "R_20261119_3.xml": "R_20261119_3.xml"}))
    st = app.poll_source("treasury_auctions")
    assert (st.queued, st.filtered, st.realtime) == (5, 10, 0)
    note = app.db.get_item("item:treasury_auctions:91282CSA0:2026-11-25")
    assert note["route"] == "us_morning_wrap"
    assert note["meta"]["results"]["HighYield"] == "4.708" and note["meta"]["results"]["BidToCoverRatio"] == "2.43"
    bill = app.db.get_item("item:treasury_auctions:912797WH6:2026-11-25")
    assert bill["status"] == "filtered"
    body = app.adapter("treasury_auctions").digest_body(
        RawItem(source_id="treasury_auctions", ext_id="x", url=note["url"], title=note["title"], meta=note["meta"]))
    assert body.paragraphs[1].startswith("High yield 4.708 percent")
    assert all(p in body.text.split("\n") for p in body.paragraphs)


def test_treasury_press_and_debt_limit(make_app):
    app = make_app()
    seed_baseline(app, "treasury_press")
    seed_baseline(app, "treasury_debt_limit")
    mock_fetcher(app, files({"feed.rss": "treasury_govdelivery_2026-11-26.xml",
                             "bulletins/43161a8": "govdelivery_43161a8.html",
                             "/debt-limit": "debt_limit_2026-11-26.html",
                             "Debt-Limit-Letter-to-Congress-06-25-2025.pdf": "Debt-Limit-Letter-to-Congress-06-25-2025.pdf"}))
    st = app.poll_source("treasury_press")
    assert (st.queued, st.filtered) == (4, 21)
    row = app.db.get_item("item:treasury_press:43161a8")
    assert row["title"] == "Treasury Announces Marketable Borrowing Estimates" and row["route"] == "us_morning_wrap"
    assert app.db.get_item("item:treasury_press:433f774")["status"] == "filtered"
    body = app.adapter("treasury_press").digest_body(
        RawItem(source_id="treasury_press", ext_id="43161a8", url=row["url"], title=row["title"]))
    assert any("privately-held net marketable borrowing" in p for p in body.paragraphs)

    st = app.poll_source("treasury_debt_limit")
    assert st.queued == st.new and st.queued > 30
    letter = app.db.get_item("item:treasury_debt_limit:Debt-Limit-Letter-to-Congress-06-25-2025.pdf")
    assert letter["route"] == "us_morning_wrap"
    body = app.adapter("treasury_debt_limit").digest_body(
        RawItem(source_id="treasury_debt_limit", ext_id="x", url=letter["url"], title=letter["title"]))
    assert body.pdf_url == letter["url"] and body.notes          # signed scan: no text layer, said so


def test_unscheduled_fomc_statement_goes_realtime(make_app, at):
    """No fomc calendar event within a day of the statement = an intermeeting action: alert, don't drop."""
    app = make_app("dry_run")
    seed_baseline(app, "fed_press")
    at(datetime(2026, 11, 18, 19, 6, tzinfo=timezone.utc))
    mock_fetcher(app, files({"press_monetary.xml": "press_monetary_2026-11-26.xml",
                             "monetary20261118a.htm": "monetary20261118a.htm",
                             "fomcminutes20261028.htm": "fomcminutes20261028.htm"}))
    app.poll_source("fed_press")
    st_row = app.db.get_item("item:fed_press:monetary20261028a.htm")
    assert st_row["status"] != "filtered" and "intermeeting" in st_row["meta"]["scope_reason"]
