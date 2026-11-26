"""Tier 3 India sources (weekly digest): NITI Aayog RSS, PRS bill tracker, ECI press notes.

Fixtures are live captures of 2026-11-26 (tests/fixtures/tier3). eci_pn029_18481_head.txt is the first
1,500 characters of content.pdf_body() of the real 13.9 MB PN-029 schedule PDF (too large to commit).
"""
from datetime import date, datetime, timezone

import httpx

from events_bot.core.models import FetchResult, RawItem
from events_bot.sources.india import eci, niti, prs

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "tier3"
NITI_RSS = FX / "niti_rss_2026-11-26.xml"
PRS_LIST = FX / "prs_billtrack_2026-11-26.html"
ECI_P1 = FX / "eci_pressnotes_p1_2026-11-26.json"
ECI_P5 = FX / "eci_pressnotes_p5_2026-11-26.json"


def _res(path) -> FetchResult:
    return FetchResult(url="u", final_url="u", status=200, fetched_at=datetime.now(timezone.utc),
                       content=path.read_bytes())


def _rebaseline(app, sid: str, new_ext_ids: list[str]) -> None:
    """First poll = silent baseline; then forget a few items so the next poll sees them as new."""
    app.poll_source(sid)
    assert app.db.one("select count(*) as n from items where source_id = %s and status = 'baseline'", (sid,))["n"] > 0
    app.db.q("delete from items where source_id = %s and ext_id = any(%s)", (sid, new_ext_ids))


def _rows(app, sid: str) -> dict[str, dict]:
    return {r["ext_id"]: r for r in app.db.q(
        "select ext_id, status, route, meta, source_published_at from items where source_id = %s "
        "and status <> 'baseline'", (sid,))}


# ---- NITI ---------------------------------------------------------------------------------------------
def test_niti_feed_classification():
    rows = {r["nid"]: r for r in niti.parse_feed(NITI_RSS.read_bytes())}
    assert len(rows) == 10
    keep = {n for n, r in rows.items() if r["route_hint"] == "weekly"}
    assert keep == {"2465", "2456", "2455"}          # discussion paper, report-launch release, the report
    assert rows["2467"]["kind"] == "Article"           # Frontier Tech op-ed link: dropped
    assert rows["2460"]["kind"] == "Library alert"     # TOC Nov 2026
    assert rows["2464"]["route_hint"] == "drop"        # Karmayogi workshop press release
    assert rows["2455"]["pdf_url"] == "https://www.niti.gov.in/sites/default/files/2026-11/Impact-Assessment-of-PLI-Schemes.pdf"
    assert rows["2456"]["release_url"] == "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2335594&reg=3&lang=1"
    assert rows["2456"]["published"] == datetime(2026, 11, 15, 8, 41, 3, tzinfo=timezone.utc)
    assert rows["2456"]["title"] == "NITI Aayog Launches Report on “Impact Assessment of PLI Schemes”"


def test_niti_poll_routes_weekly_and_drops_noise(make_app):
    app = make_app("dry_run")
    mock_fetcher(app, lambda req: httpx.Response(200, content=NITI_RSS.read_bytes(),
                                                 headers={"content-type": "application/rss+xml"}))
    _rebaseline(app, "niti", ["node:2456", "node:2455", "node:2464", "node:2467"])
    st = app.poll_source("niti")
    assert st.new == 4 and st.realtime == 0
    rows = _rows(app, "niti")
    assert rows["node:2456"]["status"] == "queued" and rows["node:2456"]["route"] == "weekly"
    assert rows["node:2455"]["route"] == "weekly"
    assert rows["node:2464"]["status"] == "filtered" and rows["node:2467"]["status"] == "filtered"
    reasons = {r["title"]: r["reason"] for r in app.db.q("select title, reason from filtered_items")}
    assert any("op-ed" in v for v in reasons.values())


def test_niti_digest_body_reads_the_pib_release(make_app):
    app = make_app("dry_run")
    mock_fetcher(app, lambda req: httpx.Response(200, content=(FX / "pib_release_2335594.html").read_bytes()))
    it = next(i for i in app.adapter("niti").parse(_res(NITI_RSS), "rss") if i.ext_id == "node:2456")
    body = app.adapter("niti").digest_body(it)
    assert body.posted == datetime(2026, 11, 12, 14, 15, tzinfo=timezone.utc)      # 19:45 IST
    assert body.paragraphs and all(p in body.text for p in body.paragraphs)
    assert body.paragraphs[0].startswith("NITI Aayog today released the report titled “Impact Assessment of PLI")


# ---- PRS ----------------------------------------------------------------------------------------------
def test_prs_listing_and_scope():
    rows = prs.parse_listing(PRS_LIST.read_bytes())
    assert len(rows) == 977
    by = {(r["slug"], r["status"]): r for r in rows}
    assert ("the-taxation-and-other-laws-amendment-bill-2026", "Passed") in by
    assert ("the-securities-markets-code-2025", "Pending") in by
    assert prs.scope("The Taxation and Other Laws (Amendment) Bill, 2026", "Passed")[0] == "weekly"
    assert prs.scope("The Securities Markets Code, 2025", "Pending")[0] == "weekly"
    assert prs.scope("Code on Wages (Central) Rules, 2026", "Rules")[0] == "weekly"
    assert prs.scope("The Kerala (Alteration of Name) Bill, 2026", "Passed")[0] == "drop"
    assert prs.scope("The Insolvency and Bankruptcy Code, 2016", "Lapsed")[0] == "drop"       # not a live stage
    # A title without an economic keyword is kept on the ministry read from the bill page.
    assert prs.scope("The Indian Statistical Institute Bill, 2026", "In Committee", "Finance")[0] == "weekly"


def test_prs_bill_page_timeline():
    b = prs.parse_bill((FX / "prs_bill_securities-markets-code-2025.html").read_bytes())
    assert b["ministry"] == "Finance"
    assert [(s["status"], s["where"], s["date"]) for s in b["stages"]] == [
        ("Introduced", "Lok Sabha", date(2025, 12, 18)), ("In Committee", "Standing Committee", date(2025, 12, 18)),
        ("Report", "Standing Committee", date(2026, 7, 23))]


def test_prs_poll_status_change_routes_weekly(make_app):
    app = make_app("dry_run")
    pages = {"/billtrack": PRS_LIST,
             "/billtrack/the-taxation-and-other-laws-amendment-bill-2026":
                 FX / "prs_bill_taxation-and-other-laws-amendment-bill-2026.html",
             "/billtrack/the-securities-markets-code-2025": FX / "prs_bill_securities-markets-code-2025.html"}

    def handler(req):
        p = pages.get(req.url.path)
        return httpx.Response(200, content=p.read_bytes()) if p else httpx.Response(404, content=b"not found")

    mock_fetcher(app, handler)
    new = ["the-taxation-and-other-laws-amendment-bill-2026|Passed", "the-securities-markets-code-2025|Pending",
           "the-kerala-alteration-of-name-bill-2026|Passed"]
    _rebaseline(app, "prs", new)
    st = app.poll_source("prs")
    assert st.new == 3 and st.realtime == 0
    rows = _rows(app, "prs")
    tax = rows[new[0]]
    assert tax["status"] == "queued" and tax["route"] == "weekly"
    assert tax["meta"]["ministry"] == "Finance" and tax["meta"]["stage"] == "Passed, Rajya Sabha"
    assert tax["meta"]["kind"] == "Bill passed"
    assert rows[new[1]]["route"] == "weekly"
    assert rows[new[2]]["status"] == "filtered"            # bill page 404 -> enrich failed, title rule drops it


def test_prs_items_keep_distinct_urls_per_status(make_app):
    app = make_app()
    items = app.adapter("prs").parse(_res(PRS_LIST), "billtrack")
    tax = next(i for i in items if i.ext_id == "the-taxation-and-other-laws-amendment-bill-2026|Passed")
    assert tax.url == "https://prsindia.org/billtrack/the-taxation-and-other-laws-amendment-bill-2026?status=passed"
    assert len({i.url for i in items}) == len(items)


def test_prs_digest_body(make_app):
    app = make_app()
    mock_fetcher(app, lambda req: httpx.Response(
        200, content=(FX / "prs_bill_taxation-and-other-laws-amendment-bill-2026.html").read_bytes()))
    it = RawItem(source_id="prs", ext_id="x", title="The Taxation and Other Laws (Amendment) Bill, 2026",
                 url="https://prsindia.org/billtrack/the-taxation-and-other-laws-amendment-bill-2026?status=passed")
    body = app.adapter("prs").digest_body(it)
    assert body.page_url == "https://prsindia.org/billtrack/the-taxation-and-other-laws-amendment-bill-2026"
    assert body.paragraphs and all(p in body.text for p in body.paragraphs)
    assert any("foreign institutional investors" in p for p in body.paragraphs)


# ---- ECI ----------------------------------------------------------------------------------------------
def test_eci_list_parse_and_title_scope():
    rows = eci.parse_list(ECI_P1.read_bytes())
    assert len(rows) == 20 and rows[0]["id"] == 18821
    assert rows[0]["published"] == datetime(2026, 10, 22, 4, 52, tzinfo=timezone.utc)      # 10:22 AM IST
    assert rows[0]["pdf_url"].startswith("https://www.eci.gov.in/eci-backend/public/api/download?url=")
    p5 = {r["id"]: r for r in eci.parse_list(ECI_P5.read_bytes())}
    kept = [i for i, r in p5.items() if eci.classify(r["title"])[0] == "weekly"]
    assert kept == [18481]                  # "General Election to the Legislative Assemblies of ..., 2026"
    assert eci.classify(p5[18482]["title"])[0] == "drop"             # bye-election schedule
    assert eci.classify(p5[18497]["title"])[0] == "drop"             # "...2026-Allotment of Broadcast time"


def test_eci_generic_titles_classified_from_pdf_heading():
    head = (FX / "eci_pn029_18481_head.txt").read_text(encoding="utf-8")
    assert eci.classify("PRESS NOTE", head) == ("weekly", "ECI general election schedule")
    pn121 = eci.pdf_head((FX / "eci_pn121_18812.pdf").read_bytes())           # SIR dates extended
    assert eci.classify("PRESS NOTE", pn121)[0] == "drop"
    assert eci.classify("PRESS NOTE", "")[0] == "weekly"                       # PDF unread: never lost


def test_eci_rotated_params_raise_for_health():
    import pytest
    with pytest.raises(ValueError):
        eci.parse_list(b'{"code":400,"message":"Invalid request"}')


def test_eci_poll_routes_schedule_weekly_and_drops_noise(make_app):
    app = make_app("dry_run")
    state = {"list": ECI_P5}

    def handler(req):
        if req.url.path.endswith("/api/get-file"):
            return httpx.Response(200, content=state["list"].read_bytes(), headers={"content-type": "application/json"})
        return httpx.Response(200, content=(FX / "eci_pn122_18817.pdf").read_bytes())

    mock_fetcher(app, handler)
    _rebaseline(app, "eci", ["id:18481", "id:18482"])
    st = app.poll_source("eci")
    assert st.new == 2 and st.realtime == 0
    rows = _rows(app, "eci")
    assert rows["id:18481"]["status"] == "queued" and rows["id:18481"]["route"] == "weekly"
    assert rows["id:18482"]["status"] == "filtered"
    # A generic "PRESS NOTE" (PN-122, 6 Oct 2026) is read from its PDF in enrich and dropped.
    state["list"] = ECI_P1
    app.poll_source("eci")                                   # p1 rows are new to the source: but not baseline
    rows = _rows(app, "eci")
    assert rows["id:18817"]["status"] == "filtered"
    assert "PDF heading" in rows["id:18817"]["meta"]["scope_reason"]


def test_eci_digest_body_is_the_pdf(make_app):
    app = make_app()
    mock_fetcher(app, lambda req: httpx.Response(200, content=(FX / "eci_pn121_18812.pdf").read_bytes()))
    it = next(i for i in app.adapter("eci").parse(_res(ECI_P1), "press_notes") if i.ext_id == "id:18812")
    body = app.adapter("eci").digest_body(it)
    assert "No. ECI/PN/121/2026" in body.text and body.pdf_url == it.url
    assert all(p in body.text for p in body.paragraphs)
