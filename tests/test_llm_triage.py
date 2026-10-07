"""LLM triage: verdicts route like rule decisions; a down model never loses an item."""
from datetime import timedelta

from events_bot.core.models import RawItem
from events_bot.core.timeutil import utcnow
from events_bot.llm import triage
from events_bot.llm.client import LLMUnavailable

import httpx

from .conftest import mock_fetcher


class StubLLM:
    def __init__(self, answers: dict[str, bool] | None = None, up: bool = True, fail: bool = False):
        self.answers, self.up, self.fail = answers or {}, up, fail
        self.calls = self.cache_hits = 0
        self.seen: list[str] = []

    def healthy(self) -> bool:
        return self.up

    def ask(self, task, system, user, schema, max_tokens=16):
        if self.fail:
            raise LLMUnavailable("boom")
        self.calls += 1
        self.seen.append(user)
        return {"relevant": next((v for k, v in self.answers.items() if k in user), False)}


def _offline(app):
    mock_fetcher(app, lambda req: httpx.Response(404, content=b"nf"))   # enrich must not hit the network


def _pending(app, title: str, ministry: str, age_min: int = 0) -> int:
    it = RawItem(source_id="pib", ext_id=f"prid:{abs(hash(title)) % 10**7}", url=f"https://x/{title[:8]}",
                 title=title, meta={"ministry": ministry})
    iid = app.db.insert_item(it, url_norm=it.url, title_norm=title.lower(), tier=1,
                             first_seen_at=utcnow() - timedelta(minutes=age_min))
    app.db.update_item(iid, status="pending_llm")
    return iid


def _status(app, iid):
    return app.db.one("select status, route, tags, meta from items where id = %s", (iid,))


def test_verdicts_route_and_filter(make_app):
    app = make_app("dry_run")
    _offline(app)
    a = _pending(app, "Draft Petroleum (Amendment) Bill, 2026 released", "Ministry of Petroleum & Natural Gas")
    b = _pending(app, "Minister addresses conference on tourism", "Ministry of Tourism")
    llm = StubLLM({"Petroleum": True})
    st = triage.run(app, llm)
    assert st.seen == 2 and st.filtered == 1 and st.realtime + st.queued == 1
    assert _status(app, a)["status"] in ("routed", "queued") and _status(app, a)["meta"]["triage"] == "llm:relevant"
    assert _status(app, b)["status"] == "filtered"
    assert app.db.one("select rule from filtered_items where item_id = %s", (b,))["rule"] == "llm"
    assert llm.seen[0].startswith("[Ministry of Petroleum & Natural Gas] Draft Petroleum")


def test_model_down_keeps_recent_items_and_falls_back_old_ones_to_digest(make_app):
    app = make_app("dry_run")
    _offline(app)
    recent = _pending(app, "Recent uncertain release", "Ministry of Steel", age_min=5)
    old = _pending(app, "Old uncertain release", "Ministry of Steel", age_min=90)
    st = triage.run(app, StubLLM(up=False), max_wait=timedelta(minutes=60))
    assert _status(app, recent)["status"] == "pending_llm"
    o = _status(app, old)
    assert o["status"] == "queued" and o["route"] == "india_eod" and "unclassified" in o["tags"]
    assert st.queued == 1


def test_model_error_mid_run_degrades_to_fallback_not_crash(make_app):
    app = make_app("dry_run")
    _offline(app)
    old = _pending(app, "Old item", "Ministry of Mines", age_min=120)
    triage.run(app, StubLLM(fail=True))
    assert _status(app, old)["meta"]["triage"] == "fallback:unclassified"


def test_no_llm_configured(make_app):
    app = make_app("dry_run")
    _offline(app)
    iid = _pending(app, "Some release", "Ministry of Coal", age_min=1)
    triage.run(app, None)
    assert _status(app, iid)["status"] == "pending_llm"
