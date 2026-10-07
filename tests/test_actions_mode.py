"""GitHub Actions mode: DB-backed raw archive and the one-pass tick."""
import httpx

from events_bot.core.app import App
from events_bot.core.archive import DbArchive

from .conftest import FIXTURES, mock_fetcher


def test_db_archive_roundtrip_and_idempotent(db):
    a = DbArchive(db)
    body = b"<rss>" + b"x" * 5000 + b"</rss>"
    h, key = a.put(body)
    assert key == f"{h[:2]}/{h}"
    assert a.put(body) == (h, key)
    assert a.get(key) == body
    row = db.one("select count(*) n, max(bytes) b, max(length(gz)) gz from raw_blobs")
    assert row["n"] == 1 and row["b"] == len(body) and row["gz"] < len(body)


def test_tick_polls_every_stream_source_once_with_db_archive(db, settings):
    settings.archive_backend = "db"
    app = App(settings, mode="dry_run")
    app.db.sync_sources(app.sources)
    feeds = {"pressreleases_rss.xml": "pressreleases_rss_2026-10-03.xml",
             "notifications_rss.xml": "notifications_rss_2026-10-03.xml"}
    hits: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        name = req.url.path.rsplit("/", 1)[-1]
        hits.append(name)
        if name not in feeds:                       # other stream sources (PIB, ...) are down here
            return httpx.Response(404, content=b"nf")
        return httpx.Response(200, content=(FIXTURES / "rbi" / feeds[name]).read_bytes())

    mock_fetcher(app, handler)
    try:
        out = app.tick()
    finally:
        app.close()
    assert {"rbi_pr", "rbi_notif"} <= set(out)
    assert all("baseline 10" in out[s] for s in ("rbi_pr", "rbi_notif")), out
    assert all(v == "error" for s, v in out.items() if s not in ("rbi_pr", "rbi_notif")), out  # isolated
    assert sorted(h for h in hits if h in feeds) == sorted(feeds)
    assert db.one("select count(*) n from raw_blobs")["n"] == 2
    # replay reads the archived bytes back through the same backend
    doc = db.one("select storage_key from documents limit 1")
    assert DbArchive(db).get(doc["storage_key"]).lstrip(b"\xef\xbb\xbf").startswith(b"<")


def test_window_launch_claimed_once_and_survives_calendar_refresh(db, make_app):
    from datetime import timedelta
    from events_bot.core.models import ScheduledEvent
    from events_bot.core.timeutil import utcnow
    make_app()  # syncs sources so the FK holds
    now = utcnow()

    def ev(start):
        return ScheduledEvent(ref="rbi_pr:test", source_id="rbi_pr", event_type="t", title="t",
                              scheduled_at=start + timedelta(minutes=10), window_start=start,
                              window_end=start + timedelta(hours=2), calendar_url="u", meta={"k": 1})

    db.upsert_event(ev(now + timedelta(hours=3)), now)
    assert db.claim_window_launches(timedelta(minutes=25)) == []      # too far ahead
    db.upsert_event(ev(now + timedelta(minutes=20)), now)
    assert db.claim_window_launches(timedelta(minutes=25)) == ["rbi_pr:test"]
    assert db.claim_window_launches(timedelta(minutes=25)) == []      # second tick: already launched
    db.upsert_event(ev(now + timedelta(minutes=20)), now)              # daily refresh, same window
    assert db.claim_window_launches(timedelta(minutes=25)) == []
    assert db.one("select meta from events")["meta"]["k"] == 1
    db.upsert_event(ev(now + timedelta(minutes=15)), now)              # window moved: relaunch
    assert db.claim_window_launches(timedelta(minutes=25)) == ["rbi_pr:test"]


def test_arm_claims_once_and_health_flags_unarmed_and_missed(db, make_app):
    from datetime import timedelta
    from events_bot.core.models import ScheduledEvent
    from events_bot.core.timeutil import utcnow
    from events_bot.ops import health
    app = make_app()
    now = utcnow()

    def ev(ref, start, hours=2):
        db.upsert_event(ScheduledEvent(ref=ref, source_id="rbi_pr", event_type="t", title=ref, scheduled_at=start,
                                       window_start=start, window_end=start + timedelta(hours=hours),
                                       calendar_url="u"), now)

    ev("soon", now + timedelta(hours=3))
    ev("later", now + timedelta(hours=60))
    ev("gone", now - timedelta(hours=5))                     # window closed, never captured
    rules = {b.rule for b in health.evaluate(db, app.sources)}
    assert "unarmed:soon" in rules and "missed:gone" in rules and "unarmed:later" not in rules
    armed = [r["ref"] for r in db.claim_arms(timedelta(hours=36))]
    assert armed == ["soon"] and db.claim_arms(timedelta(hours=36)) == []
    rules = {b.rule for b in health.evaluate(db, app.sources)}
    assert "unarmed:soon" not in rules and "missed:gone" in rules
    db.mark_event("gone", None, stage1_at=now)
    assert "missed:gone" not in {b.rule for b in health.evaluate(db, app.sources)}
