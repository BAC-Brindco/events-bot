"""BSE notices: parsing, today+yesterday polling through the impersonated client, routine-notice filter."""
from datetime import date, datetime, timezone

import httpx

from events_bot.core.models import FetchResult
from events_bot.sources.india import bse

from .conftest import FIXTURES, mock_fetcher, requires_db

FX = FIXTURES / "bse"


def _res(day: str) -> FetchResult:
    return FetchResult(url="u", final_url="u", status=200, fetched_at=datetime.now(timezone.utc),
                       content=(FX / f"notices_{day}.json").read_bytes())


def test_parse_notices():
    rows = bse.parse_notices(_res("20261007").content)
    assert len(rows) == 49
    assert rows[0]["no"] == "20261007-49" and rows[0]["date"] == date(2026, 10, 7)
    buy = [r for r in bse.parse_notices(_res("20261005").content) if "Buyback" in r["subject"]]
    assert buy[0]["pdf"].startswith("https://www.bseindia.com/downloads/UploadDocs/Notices/")


@requires_db
def test_filter_keeps_market_moving_notices_only(make_app):
    app = make_app()
    a, cfg, kf = app.adapter("bse_notices"), app.sources["bse_notices"], app.pipeline.kf
    items = [i for d in ("20261005", "20261006", "20261007") for i in a.parse(_res(d), d)]
    kept = {i.title for i in items if kf.decide(cfg, i).keep}
    assert 8 <= len(kept) <= 20 and len(items) == 138
    assert any("Tender Offer (Buyback)" in t and "TRANSPORT CORPORATION" in t for t in kept)
    assert "Additions to the BSE Indices" in kept and "Compulsory Delisting of Companies" in kept
    assert not any(t.startswith("Listing of Equity Shares") or t == "Daily Bulletin" for t in kept)
    assert not any("BSE SME IPO Index" in t for t in kept)
    idx = next(i for i in items if i.title == "Additions to the BSE Indices")
    assert idx.meta["priority_tags"] == ["bse:index"]


@requires_db
def test_poll_reads_today_and_yesterday_impersonated(make_app):
    app = make_app()
    asked = []

    def handler(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url).split("flag=")[1])
        return httpx.Response(200, content=(FX / "notices_20261007.json").read_bytes())

    mock_fetcher(app, handler)
    items = app.adapter("bse_notices").poll()
    assert asked[0] == "" and len(asked[1]) == 8 and len(items) == 98
