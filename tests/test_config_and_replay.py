import pytest
from pydantic import ValidationError

from events_bot.core import registry
from events_bot.core.config import load_delivery, load_sources
from events_bot.core.models import SourceConfig
from events_bot.core.settings import ROOT
from events_bot.ops.replay import replay_file, replay_ref

from .conftest import FIXTURES


def test_registry_loads_and_every_adapter_resolves():
    srcs = load_sources(ROOT / "config")
    assert srcs
    for cfg in srcs.values():
        assert issubclass(registry.resolve(cfg.adapter), registry.SourceAdapter)
        assert all(u.startswith("https://") for u in cfg.urls.values())


def test_every_country_tier_has_a_route():
    d = load_delivery(ROOT / "config")
    for c in ("IN", "US"):
        for t in (1, 2, 3):
            assert f"{c}:{t}" in d.routes


def test_bad_source_entry_rejected():
    with pytest.raises(ValidationError):
        SourceConfig.model_validate({"id": "x", "country": "UK", "tier": 1, "kind": "stream", "feed_type": "rss",
                                     "urls": {}, "poll_interval": 60, "adapter": "a:B"})


def test_replay_file_renders_every_item(make_app):
    app = make_app()
    paths = replay_file(app, "rbi_pr", FIXTURES / "rbi" / "pressreleases_rss_2026-10-03.xml")
    assert len(paths) == 10
    assert all(p.parent.name == "replay" for p in paths)
    assert app.db.one("select count(*) as n from sends")["n"] == 0


def test_replay_ref_uses_archived_bytes(make_app):
    import httpx
    from .conftest import mock_fetcher
    app = make_app()
    rss = (FIXTURES / "rbi" / "pressreleases_rss_2026-10-03.xml").read_bytes()
    mock_fetcher(app, lambda req: httpx.Response(200, content=rss))
    app.poll_source("rbi_pr")
    mock_fetcher(app, lambda req: (_ for _ in ()).throw(AssertionError("replay must not use the network")))
    [p] = replay_ref(app, "item:rbi_pr:prid:63719")
    txt = p.with_suffix(".txt").read_text(encoding="utf-8")
    assert "Source time: 02 Oct 2026 17:05 IST" in txt
