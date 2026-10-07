"""NSE Indices: listing, filter, PDF rows (semi-annual + single-index), alert size and watchlist."""
from datetime import datetime, timezone

import httpx

from events_bot.core.models import RawItem
from events_bot.extract import nifty_indices as nx
from events_bot.sources.india import nifty_indices as src

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "nse"


def test_semi_annual_rows_verbatim_and_balanced():
    r = nx.parse_changes((FX / "ind_prs10082026.pdf").read_bytes())
    assert len(r["rows"]) == 980 and r["effective"] == "September 30, 2026 (close of September 29, 2026)"
    n50 = [x for x in r["rows"] if x["index"] == "Nifty 50"]
    assert n50 == [{"index": "Nifty 50", "action": "excluded", "company": "Wipro Ltd.", "symbol": "WIPRO"},
                   {"index": "Nifty 50", "action": "included", "company": "BSE Ltd.", "symbol": "BSE"}]
    assert all(x["company"] in r["text"] and x["symbol"] in r["text"] for x in r["rows"])


def test_single_index_notice_takes_index_from_title():
    r = nx.parse_changes((FX / "ind_prs23092026_2.pdf").read_bytes())
    assert r["rows"] == [{"index": "Nifty SME Emerge", "action": "excluded", "company": "Parin Enterprises Ltd.",
                          "symbol": "PARIN"}]


def test_listing_and_filter(make_app):
    app = make_app()
    rows = src.parse_listing((FX / "niftyindices_pr_2026-10-07.html").read_bytes())
    assert len(rows) > 1000 and rows[0]["url"].endswith("ind_prs06102026.pdf")
    cfg = app.sources["nifty_indices"]
    kf = app.pipeline.kf
    dec = lambda t: kf.decide(cfg, RawItem(source_id="x", ext_id="x", url="u", title=t))  # noqa: E731
    assert dec("Replacements in indices").keep
    assert not dec("Inclusions in Nifty IPO w.e.f. October 09, 2026").keep
    assert not dec("Changes in Nifty Fixed Income indices w.e.f. September 30, 2026").keep


def test_alert_stays_under_gmail_clip_and_flags_watchlist(make_app, tmp_path):
    app = make_app("dry_run")
    wl = tmp_path / "w.csv"
    wl.write_text("ticker,name,aliases\nBSE,BSE Ltd.,\n", encoding="utf-8")
    app.pipeline.watchlist.path = wl
    app.pipeline.watchlist.reload()
    mock_fetcher(app, lambda req: httpx.Response(200, content=(FX / "ind_prs10082026.pdf").read_bytes()))
    a = app.adapter("nifty_indices")
    it = RawItem(source_id="nifty_indices", ext_id="pr:x", url="https://www.niftyindices.com/Press_Release/ind_prs10082026.pdf",
                 title="Replacements in indices", published_raw="Aug 10, 2026", meta={"date_only": True})
    it = a.enrich(it)
    assert app.pipeline.watchlist.tags(it) == ["watch:BSE"]
    msg = a.render_stage1(it, first_seen=datetime(2026, 8, 10, 16, 0, tzinfo=timezone.utc), mode="dry_run")
    assert msg.subject.endswith("| WATCHLIST") and "WIPRO" in msg.body_html
    assert len(msg.body_html.encode()) < 100_000, len(msg.body_html.encode())
