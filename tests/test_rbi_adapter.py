from datetime import datetime, timezone

from events_bot.core.models import FetchResult
from events_bot.core.registry import Context
from events_bot.core.config import load_sources
from events_bot.core.settings import ROOT
from events_bot.sources.india.rbi import RbiRss

from .conftest import FIXTURES


def _parse(name: str, source_id: str):
    cfg = load_sources(ROOT / "config")[source_id]
    a = RbiRss(cfg, Context(settings=None, db=None, fetcher=None, archive=None))
    res = FetchResult(url=next(iter(cfg.urls.values())), final_url="x", status=200,
                      fetched_at=datetime.now(timezone.utc), content=(FIXTURES / "rbi" / name).read_bytes())
    return a.parse(res, next(iter(cfg.urls)))


def test_press_release_feed_parses_with_bom():
    items = _parse("pressreleases_rss_2026-10-03.xml", "rbi_pr")
    assert len(items) == 10
    top = items[0]
    assert top.ext_id == "prid:63719"
    assert top.title == "RBI appoints Shri Sudhakar Malli as new Executive Director"
    assert top.source_published_at == datetime(2026, 10, 2, 11, 35, tzinfo=timezone.utc)  # 17:05 IST
    assert top.published_raw.strip() == "Fri, 02 Oct 2026 17:05:00"
    assert top.summary and "Executive Director" in top.summary


def test_notification_feed_ids():
    items = _parse("notifications_rss_2026-10-03.xml", "rbi_notif")
    assert len(items) == 10
    assert all(i.ext_id.startswith("id:") for i in items)
    assert len({i.ext_id for i in items}) == 10


def test_unicode_titles_survive():
    titles = [i.title for i in _parse("pressreleases_rss_2026-10-03.xml", "rbi_pr")]
    assert any("–" in t for t in titles)  # en dash in the WSS title
    assert not any("�" in t for t in titles)
