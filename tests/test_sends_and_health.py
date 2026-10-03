import httpx

from events_bot.core.models import Message
from events_bot.deliver.interface import Dispatcher
from events_bot.ops import health

from .conftest import mock_fetcher


class FakeChannel:
    name = "email"

    def __init__(self, fail=False):
        self.sent: list[str] = []
        self.fail = fail

    def send(self, msg, recipients):
        if self.fail:
            raise ConnectionError("smtp down")
        self.sent.append(msg.ref)


def msg(ref="item:rbi_pr:prid:1"):
    return Message(ref=ref, stage="stage1", kind="realtime", subject="s", body_html="<p>b</p>", body_text="b")


def test_restart_cannot_double_send(db, tmp_path):
    ch = FakeChannel()
    d1 = Dispatcher(db, [ch], "live", tmp_path)
    assert d1.dispatch(msg(), ["a@x"]).status == "sent"
    d2 = Dispatcher(db, [ch], "live", tmp_path)              # "restarted" process
    assert d2.dispatch(msg(), ["a@x"]).status == "already_sent"
    assert ch.sent == ["item:rbi_pr:prid:1"]
    row = db.one("select status, attempts, body_text from sends")
    assert (row["status"], row["attempts"], row["body_text"]) == ("sent", 1, "b")


def test_failed_send_is_recorded_and_only_explicitly_retried(db, tmp_path):
    d = Dispatcher(db, [FakeChannel(fail=True)], "live", tmp_path)
    assert d.dispatch(msg(), ["a@x"]).status == "failed"
    row = db.one("select id, status, error from sends")
    assert row["status"] == "failed" and "smtp down" in row["error"]
    # A second automatic dispatch does not resend: the claim already exists.
    assert Dispatcher(db, [FakeChannel()], "live", tmp_path).dispatch(msg(), ["a@x"]).status == "already_sent"
    assert db.reclaim_failed_send(row["id"])["status"] == "claimed"
    assert db.reclaim_failed_send(row["id"]) is None          # not failed any more


def test_same_ref_different_stage_is_a_different_send(db, tmp_path):
    ch = FakeChannel()
    d = Dispatcher(db, [ch], "live", tmp_path)
    d.dispatch(msg(), ["a@x"])
    m2 = msg().model_copy(update={"stage": "stage2"})
    assert d.dispatch(m2, ["a@x"]).status == "sent"


def test_no_recipients_never_claims(db, tmp_path):
    d = Dispatcher(db, [FakeChannel()], "live", tmp_path)
    assert d.dispatch(msg(), []).status == "no_recipients"
    assert db.one("select count(*) as n from sends")["n"] == 0


def test_consecutive_errors_alert_once_then_clear(make_app):
    app = make_app()
    state = {"fail": True}
    rss = b'<?xml version="1.0"?><rss><channel></channel></rss>'
    mock_fetcher(app, lambda req: httpx.Response(503) if state["fail"] else httpx.Response(200, content=rss))
    for _ in range(3):
        assert app.poll_source("rbi_pr") is None
    assert app.check_health() == 1
    assert app.check_health() == 0                            # still open: not re-sent
    alerts = list((app.settings.out_dir / "dry_run").glob("health_*.txt"))
    assert len(alerts) == 1 and "3 consecutive errors" in alerts[0].read_text(encoding="utf-8")
    state["fail"] = False
    app.poll_source("rbi_pr")
    app.check_health()
    assert app.db.open_alerts() == []


def test_min_items_shape_check(make_app):
    app = make_app()
    one_item = (b'<?xml version="1.0"?><rss><channel><item><title>x</title>'
                b'<link>https://rbi.org.in/a.aspx?prid=1</link></item></channel></rss>')
    mock_fetcher(app, lambda req: httpx.Response(200, content=one_item))
    assert app.poll_source("rbi_pr") is None
    assert "min_items" in app.db.health_rows()[0]["last_error"]


def test_stuck_claim_is_reported(db, tmp_path):
    db.claim_send(ref="r", stage="stage1", kind="realtime", channel="email", subject="s", body_html="b",
                  recipients=["a"])
    db.q("update sends set claimed_at = now() - interval '1 hour'")
    rules = [b.rule for b in health.evaluate(db, {})]
    assert any(r.startswith("stuck_send:") for r in rules)
