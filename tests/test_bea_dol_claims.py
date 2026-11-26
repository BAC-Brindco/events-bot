"""DOL weekly jobless claims stream: PDF parse, RSS backstop (claims kept, other DOL releases dropped), the
Wed-Fri poll gate, routing to the US Morning Wrap, and the digest body quoting the headline paragraphs.

Fixtures (tests/fixtures/bea), fetched live on 2026-11-26 with prints_base.BROWSER_HEADERS:
  dol_data.pdf      https://www.dol.gov/ui/data.pdf (release embargoed until 8:30 A.M. Wed 25 Nov 2026,
                    Thanksgiving week; ETag "9605f-66119d2dc6392")
  dol_releases.xml  https://www.dol.gov/rss/releases.xml
"""
from datetime import datetime, timedelta, timezone

import httpx

from events_bot.core.pipeline import PollStats
from events_bot.digest import scope
from events_bot.sources.us import dol

from .conftest import FIXTURES, mock_fetcher, requires_db

FX = FIXTURES / "bea"
UTC = timezone.utc
WED = datetime(2026, 11, 25, 14, 0, tzinfo=UTC)
ETA = "https://www.dol.gov/newsroom/releases/eta/eta20261125"


def _b(name):
    return (FX / name).read_bytes()


def test_claims_pdf_parse():
    p = dol.parse_pdf(_b("dol_data.pdf"))
    assert p["release_at"].astimezone(UTC) == datetime(2026, 11, 25, 13, 30, tzinfo=UTC)
    assert p["week_ending"] == "November 21"
    a, b = p["paragraphs"]
    assert a.startswith("In the week ending November 21, the advance figure for seasonally adjusted initial claims "
                        "was 189,000, a decrease of 13,000")
    assert "The 4-week moving average was 196,000, a decrease of 1,500" in a
    assert b.startswith("The advance seasonally adjusted insured unemployment rate was 1.1 percent for the week "
                        "ending November 14")
    assert "insured unemployment during the week ending November 14 was 1,697,000" in b
    assert all(x in p["text"].split("\n") for x in (a, b))


def _handler(calls=None):
    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if calls is not None:
            calls.append((u, req.headers.get("user-agent", "")))
        if u.endswith("/ui/data.pdf") or u == ETA:
            return httpx.Response(200, content=_b("dol_data.pdf"), headers={"etag": '"9605f-66119d2dc6392"'})
        if u.endswith("releases.xml"):
            return httpx.Response(200, content=_b("dol_releases.xml"))
        return httpx.Response(404, content=b"")
    return handler


@requires_db
def test_poll_routes_claims_to_morning_wrap_and_drops_other_dol_releases(make_app, monkeypatch):
    monkeypatch.setattr(dol, "utcnow", lambda: WED)
    app = make_app("dry_run")
    calls = []
    mock_fetcher(app, _handler(calls))
    a = app.adapter("dol_claims")
    items = a.poll()
    assert all("Chrome" in ua for _, ua in calls)                         # Akamai: browser header set
    by = {i.ext_id: i for i in items}
    claims = by["claims:2026-11-25"]
    assert claims.url == ETA and claims.title == "Unemployment Insurance Weekly Claims Report"
    assert claims.source_published_at.astimezone(UTC) == datetime(2026, 11, 25, 13, 30, tzinfo=UTC)
    assert claims.meta.get("from") != "rss"                               # the PDF item wins the duplicate
    assert by["claims:2026-11-19"].meta["from"] == "rss"
    other = by["rss:ebsa20261123"]
    cfg = app.sources["dol_claims"]
    assert scope.decide(cfg, claims).route == "us_morning_wrap"
    assert scope.decide(cfg, other).route is None
    assert len([i for i in items if i.ext_id.startswith("rss:")]) == 7   # 8 non-claims rows, one listed twice
    # Through the pipeline: the claims item is queued for the wrap, the EBSA release is filtered.
    st = PollStats()
    for it in (claims, other):
        iid = app.db.insert_item(it, url_norm=it.url, title_norm=it.title.lower(), tier=1, first_seen_at=WED)
        app.pipeline.deliver(cfg, iid, it, WED, st)
    assert st.queued == 1 and st.filtered == 1 and st.realtime == 0
    routes = {r["ext_id"]: (r["status"], r["route"]) for r in app.db.q("select ext_id, status, route from items")}
    assert routes["claims:2026-11-25"] == ("queued", "us_morning_wrap")
    assert routes["rss:ebsa20261123"][0] == "filtered"


@requires_db
def test_poll_only_wed_to_fri(make_app, monkeypatch):
    app = make_app("dry_run")
    calls = []
    mock_fetcher(app, _handler(calls))
    monkeypatch.setattr(dol, "utcnow", lambda: WED + timedelta(days=5))   # Monday 30 Nov
    assert app.adapter("dol_claims").poll() == [] and calls == []
    monkeypatch.setattr(dol, "utcnow", lambda: WED + timedelta(days=1))   # Thursday
    assert app.adapter("dol_claims").poll()


@requires_db
def test_digest_body_quotes_headline_paragraphs(make_app, monkeypatch):
    monkeypatch.setattr(dol, "utcnow", lambda: WED)
    app = make_app("dry_run")
    mock_fetcher(app, _handler())
    a = app.adapter("dol_claims")
    claims = next(i for i in a.poll() if i.ext_id == "claims:2026-11-25")
    body = a.digest_body(claims)
    assert len(body.paragraphs) == 2 and all(p in body.text.split("\n") for p in body.paragraphs)
    assert "initial claims was 189,000" in body.paragraphs[0]
    assert body.paragraphs[1].startswith("The advance seasonally adjusted insured unemployment rate was 1.1 percent")
    assert body.page_url == ETA and body.posted.astimezone(UTC) == datetime(2026, 11, 25, 13, 30, tzinfo=UTC)
