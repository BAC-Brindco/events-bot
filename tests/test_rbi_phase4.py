"""RBI Phase 4: title classification, routine-operation filter, display-page parsing, id-gap backfill."""
from datetime import date, datetime, timezone

import httpx

from events_bot.core.models import FetchResult, RawItem
from events_bot.sources.india import rbi

from .conftest import FIXTURES, mock_fetcher, requires_db

FX = FIXTURES / "rbi"


def test_classify_titles_from_7_oct_burst():
    assert rbi.classify("Reserve Bank of India (Commercial Banks – Credit Valuation Adjustment Framework) "
                        "Directions, 2026") == ["rbi:directions"]
    assert rbi.classify("Reserve Bank of India (Payments Banks – Prudential Norms on Capital Adequacy) "
                        "Third Amendment Directions, 2026") == ["rbi:amendment_directions"]
    assert "rbi:draft" in rbi.classify("RBI invites comments on the Draft Directions on Co-lending")
    assert rbi.classify("Liquidity Adjustment Facility - Change in rates") == ["rbi:policy_rates"]
    assert rbi.classify("Penal Interest on shortfall in CRR and SLR requirements - Change in Bank Rate") \
        == ["rbi:policy_rates"]
    assert rbi.classify("Money Market Operations as on October 07, 2026") == []


def test_parse_pages():
    n = rbi.parse_page((FX / "notif_page_13734.html").read_bytes())
    assert n["title"].startswith("Reserve Bank of India (Payments Banks – Prudential Norms")
    assert n["date"] == date(2026, 10, 7)
    p = rbi.parse_page((FX / "pr_page_63757.html").read_bytes())
    assert p["title"].startswith("Underwriting Auction for sale of Government Securities")
    assert p["date"] == date(2026, 10, 8)
    assert rbi.parse_page((FX / "notif_page_unpublished.html").read_bytes()) is None


def _it(source: str, ext: str, title: str = "t") -> RawItem:
    return RawItem(source_id=source, ext_id=ext, url=f"https://x/{ext}", title=title)


@requires_db
def test_routine_press_releases_filtered(make_app):
    app = make_app()
    kf, cfg = app.pipeline.kf, app.sources["rbi_pr"]
    feed = app.adapter("rbi_pr").parse(FetchResult(
        url="u", final_url="u", status=200, fetched_at=datetime.now(timezone.utc),
        content=(FX / "pressreleases_rss_2026-10-08.xml").read_bytes()), "press_releases")
    kept = [i.title for i in feed if kf.decide(cfg, i).keep]
    dropped = [i.title for i in feed if not kf.decide(cfg, i).keep]
    assert any("Credit Valuation Adjustment" in t for t in kept)
    assert any("Paytm Payments Bank" in t for t in kept)
    assert any(t.startswith("Money Market Operations") for t in dropped)
    assert all("VRRR" in t or "Reverse Repo" in t or "Money Market" in t or "Underwriting" in t
               or "Reserve Money" in t for t in dropped)


@requires_db
def test_gap_backfill_fetches_missing_notification_ids(make_app):
    app = make_app()
    cfg = app.sources["rbi_notif"]
    # Previous poll stored ids up to 13720; the 8 Oct feed starts at 13726, so 13721-13725 were missed.
    for n in (13719, 13720):
        app.db.insert_item(_it("rbi_notif", f"id:{n}"), url_norm=f"x{n}", title_norm=f"t{n}", tier=1,
                           first_seen_at=datetime.now(timezone.utc))
    asked: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.endswith("notifications_rss.xml"):
            return httpx.Response(200, content=(FX / "notifications_rss_2026-10-08.xml").read_bytes())
        asked.append(url)
        page = "notif_page_13734.html" if "Id=13723" in url else "notif_page_unpublished.html"
        return httpx.Response(200, content=(FX / page).read_bytes())

    mock_fetcher(app, handler)
    items = app.adapter("rbi_notif").poll()
    assert [u.split("Id=")[1].split("&")[0] for u in asked] == ["13721", "13722", "13723", "13724", "13725"]
    back = [i for i in items if i.meta.get("feed") == "backfill"]
    assert [i.ext_id for i in back] == ["id:13723"]
    assert back[0].meta["date_only"] and "rbi:amendment_directions" in back[0].meta["priority_tags"]

    # Next poll: nothing missing any more, no page fetches.
    asked.clear()
    app.pipeline.process(cfg, items)
    app.adapter("rbi_notif").poll()
    assert asked == []


@requires_db
def test_no_backfill_on_baseline(make_app):
    app = make_app()
    asked: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url).endswith("pressreleases_rss.xml"):
            return httpx.Response(200, content=(FX / "pressreleases_rss_2026-10-08.xml").read_bytes())
        asked.append(str(req.url))
        return httpx.Response(500)

    mock_fetcher(app, handler)
    assert len(app.adapter("rbi_pr").poll()) == 10 and asked == []
