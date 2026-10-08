"""CBDT (Liferay search via curl_cffi) and CBIC (latest-updates JSON with a shipped TLS intermediate)."""
import ssl
from datetime import date, datetime, timezone

import httpx

from events_bot.core import fetch
from events_bot.core.models import FetchResult
from events_bot.core.timeutil import IST
from events_bot.sources.india import cbdt, cbic

from .conftest import FIXTURES, mock_fetcher, requires_db


def _res(path) -> FetchResult:
    return FetchResult(url="u", final_url="u", status=200, fetched_at=datetime.now(timezone.utc),
                       content=path.read_bytes())


def test_cbdt_search_parses_ids_times_and_pdfs():
    rows = cbdt.parse_search((FIXTURES / "cbdt" / "notifications_2026-10-08.json").read_bytes())
    assert len(rows) == 20
    top = rows[0]
    assert top["id"] == 26107110 and top["title"].startswith("Notification No. 133/2026")
    assert top["at"] == datetime(2026, 10, 1, 11, 30, tzinfo=IST)
    assert top["url"] == "https://www.incometaxindia.gov.in/documents/d/guest/notification-133-2026-pdf"
    press = cbdt.parse_search((FIXTURES / "cbdt" / "press_2026-10-08.json").read_bytes())
    assert press[0]["title"].startswith("CBDT extends due date for furnishing Return of Income")


def test_cbdt_requests_carry_the_blueprints():
    reqs = cbdt.requests()
    assert set(reqs) == {"notifications", "circulars", "press"}
    attrs = reqs["notifications"][1]["attributes"]
    assert attrs["search.experiences.blueprint.external.reference.code"] == "CIRCULAR_NOTIFICATION_BP_ERC"
    assert attrs["search.experiences.structure_key"] == "NOTIFICATION_KEY"
    assert reqs["press"][1]["attributes"]["search.experiences.structure_key"] == "PRESS_RELEASE"


@requires_db
def test_cbdt_filter_drops_institution_approvals_keeps_rules(make_app):
    app = make_app()
    a, cfg, kf = app.adapter("cbdt"), app.sources["cbdt"], app.pipeline.kf
    items = a.parse(_res(FIXTURES / "cbdt" / "notifications_2026-10-08.json"), "notifications")
    kept = [i.title for i in items if kf.decide(cfg, i).keep]
    dropped = [i.title for i in items if not kf.decide(cfg, i).keep]
    assert any("Income-tax (Fifth Amendment) Rules, 2026" in t or "Income Tax (Fifth Amendment)" in t for t in kept)
    assert any("Statement of Financial Transactions" in t for t in kept)
    assert dropped and all(("45(3)" in t) or ("in the case of" in t) or ("536" in t) or ("Schedule III" in t)
                           for t in dropped)
    assert all(i.meta["priority_tags"] == ["cbdt:notifications"] for i in items)


@requires_db
def test_cbdt_poll_posts_three_searches(make_app):
    app = make_app()
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST" and req.headers["accept"] == "application/json"
        import json
        key = json.loads(req.content)["attributes"].get("search.experiences.structure_key")
        name = {"NOTIFICATION_KEY": "notifications", "CIRCULAR_KEY": "circulars", "PRESS_RELEASE": "press"}[key]
        seen.append(name)
        return httpx.Response(200, content=(FIXTURES / "cbdt" / f"{name}_2026-10-08.json").read_bytes())

    mock_fetcher(app, handler)
    items = app.adapter("cbdt").poll()
    assert sorted(seen) == ["circulars", "notifications", "press"] and len(items) == 60


def test_cbic_updates_parse_and_rate_tags():
    rows = cbic.parse_updates((FIXTURES / "cbic" / "customs_2026-10-08.json").read_bytes())
    assert [r["date"] for r in rows][0] == date(2026, 9, 30)
    assert rows[0]["title"].startswith("80/2026-Customs (N.T): Fixation of Tariff Value")


@requires_db
def test_cbic_items_tags_and_filter(make_app):
    app = make_app()
    a, cfg, kf = app.adapter("cbic"), app.sources["cbic"], app.pipeline.kf
    customs = a.parse(_res(FIXTURES / "cbic" / "customs_2026-10-08.json"), "customs")
    by_no = {i.title.split(":")[0]: i for i in customs}
    assert "cbic:rate" in by_no["24/2026-Customs (ADD)"].meta["priority_tags"]
    assert "cbic:rate" not in by_no["80/2026-Customs (N.T)"].meta["priority_tags"]
    assert not kf.decide(cfg, by_no["80/2026-Customs (N.T)"]).keep          # fortnightly tariff values
    assert not kf.decide(cfg, by_no["79/2026-Customs (N.T)"]).keep          # adjudication appointment
    assert kf.decide(cfg, by_no["24/2026-Customs (ADD)"]).keep
    assert by_no["24/2026-Customs (ADD)"].url == "https://taxinformation.cbic.gov.in/view-pdf/1010764/ENG/Notifications"
    excise = a.parse(_res(FIXTURES / "cbic" / "excise_2026-10-08.json"), "excise")
    assert all("cbic:rate" in i.meta["priority_tags"] for i in excise)   # excise tariff = duty changes
    gst = a.parse(_res(FIXTURES / "cbic" / "gst_2026-10-08.json"), "gst")
    assert {i.ext_id.split(":")[0] for i in gst} == {"circulars", "notifications"}


def test_intermediate_is_added_only_for_listed_hosts(tmp_path):
    from events_bot.core.archive import Archive
    f = fetch.Fetcher(None, Archive(tmp_path), "ua")
    try:
        assert f._client_for("rbi.org.in") is f.client
        c = f._client_for("taxinformation.cbic.gov.in")
        assert c is not f.client and c is f._client_for("www.cbic.gov.in")
        assert (fetch.CERTS / "sectigo_public_server_ca_ov_r36.pem").read_text().startswith("-----BEGIN CERTIFICATE")
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cafile=str(fetch.CERTS / "sectigo_public_server_ca_ov_r36.pem"))
        assert ctx.verify_mode == ssl.CERT_REQUIRED
    finally:
        f.close()


@requires_db
def test_cbic_retries_a_connection_reset(make_app, monkeypatch):
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    app = make_app()
    n = {"calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        if n["calls"] == 1:
            raise httpx.ConnectError("[Errno 104] Connection reset by peer")
        name = {v: k for k, v in cbic.URLS.items()}[str(req.url)]
        return httpx.Response(200, content=(FIXTURES / "cbic" / f"{name}_2026-10-08.json").read_bytes())

    mock_fetcher(app, handler)
    assert len(app.adapter("cbic").poll()) == 12 and n["calls"] == 4
