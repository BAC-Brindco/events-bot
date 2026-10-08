from __future__ import annotations

import os
from pathlib import Path

import httpx
import psycopg
import pytest

from events_bot.core.app import App
from events_bot.core.db import Database
from events_bot.core.fetch import Fetcher
from events_bot.core.settings import ROOT, Settings

TEST_DSN = os.environ.get("EVENTS_BOT_TEST_DSN", "host=localhost port=54433 user=postgres dbname=events_bot_test")
FIXTURES = Path(__file__).parent / "fixtures"


def _db_up() -> bool:
    try:
        psycopg.connect(TEST_DSN, connect_timeout=3).close()
        return True
    except psycopg.Error:
        return False


requires_db = pytest.mark.skipif(not _db_up(), reason="test Postgres not reachable (EVENTS_BOT_TEST_DSN)")


@pytest.fixture(scope="session")
def schema_ready():
    if not _db_up():
        pytest.skip("test Postgres not reachable")
    with psycopg.connect(TEST_DSN, autocommit=True) as c:
        c.execute("drop schema public cascade; create schema public;")
    db = Database(TEST_DSN)
    db.migrate(ROOT / "migrations")
    db.close()


@pytest.fixture
def db(schema_ready):
    d = Database(TEST_DSN)
    d.q("truncate sources, http_cache, documents, events, items, extractions, filtered_items, sends, "
        "digests, source_health, health_alerts, raw_blobs, llm_cache restart identity cascade")
    yield d
    d.close()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(dsn=TEST_DSN, archive_dir=tmp_path / "archive", out_dir=tmp_path / "out",
                    recipients=["desk@example.com"], operator_emails=["ops@example.com"])


def mock_fetcher(app: App, handler) -> Fetcher:
    f = Fetcher(app.db, app.archive, "test-agent", min_gap=0, transport=httpx.MockTransport(handler))
    app.fetcher.close()
    app.fetcher = f
    app.ctx.fetcher = f
    return f


@pytest.fixture
def make_app(db, settings):
    apps: list[App] = []

    def _make(mode: str = "dry_run") -> App:
        a = App(settings, mode=mode)
        a.db.sync_sources(a.sources)
        apps.append(a)
        return a

    yield _make
    for a in apps:
        a.close()
