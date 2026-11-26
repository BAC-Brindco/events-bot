"""US Tier-1 policy streams: White House, USTR, Congress laws, OPM status, FDA press / recalls / warning letters.

Fixtures in tests/fixtures/uspol are live captures of 26 Nov 2026. The one exception is the OPM shutdown status,
which is the captured JSON with its status fields rewritten (no shutdown was in progress to capture); it is
built in `_opm_shutdown()` below so the derivation is visible.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from events_bot.core.models import FetchResult, RawItem
from events_bot.core.pipeline import PollStats
from events_bot.digest import scope
from events_bot.sources.us import congress, fda, ustr, whitehouse

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "uspol"


def _res(name: str) -> FetchResult:
    return FetchResult(url="u", final_url="u", status=200, fetched_at=datetime.now(timezone.utc),
                       content=(FX / name).read_bytes())


def _seed(app, source_id: str) -> None:
    """One prior item so the next poll is not the silent baseline."""
    it = RawItem(source_id=source_id, ext_id="seed", url=f"https://example.invalid/{source_id}", title="seed",
                 meta={"route_hint": "drop"})
    app.db.insert_item(it, url_norm=it.url, title_norm="seed", tier=1,
                       first_seen_at=datetime.now(timezone.utc) - timedelta(days=30))


def _rows(app, source_id: str) -> dict[str, dict]:
    return {r["ext_id"]: r for r in app.db.q("select ext_id, status, route, meta from items where source_id = %s",
                                              (source_id,))}


def _deliver(app, source_id: str, it: RawItem, now: datetime) -> str:
    iid = app.db.insert_item(it, url_norm=it.url, title_norm=it.title.lower(), tier=1, first_seen_at=now)
    st = PollStats()
    app.pipeline.deliver(app.sources[source_id], iid, it, now, st)
    row = app.db.one("select status, route from items where id = %s", (iid,))
    return "dropped" if row["status"] == "filtered" else row["route"]


# ---- White House ------------------------------------------------------------------------------------------
def _wh_items(app):
    return {i.title: i for i in app.adapter("whitehouse").parse(_res("whitehouse_feed_2026-11-26.xml"), "actions")}


def test_whitehouse_scope_rules():
    parsed = whitehouse.parse_feed((FX / "whitehouse_feed_2026-11-26.xml").read_bytes())
    assert len(parsed) == 30 and len({r["ext_id"] for r in parsed}) == 30
    rows = {r["title"]: r for r in parsed}
    robotics = rows["ADJUSTING IMPORTS OF ROBOTICS, INDUSTRIAL MACHINERY, AND THEIR PARTS AND COMPONENTS INTO THE UNITED STATES"]
    assert whitehouse.classify(robotics)[:1] == ("realtime_if_keyword",)
    assert robotics["ext_id"] == "p:53657" and robotics["published"] == datetime(2026, 10, 28, 20, 3, 48, tzinfo=timezone.utc)
    assert whitehouse.classify(rows["Thanksgiving Day, 2026"])[:2] == ("drop", "ceremonial proclamation")
    # an observance that praises tariffs in its body is still ceremonial
    assert whitehouse.classify(rows["National Manufacturing Day, 2026"])[0] == "drop"
    assert whitehouse.classify(rows["Nominations Sent to the Senate"])[0] == "drop"
    eo = rows["Emergency Tax Relief on Diesel Fuel"]
    assert whitehouse.classify(eo) == ("realtime_if_keyword", "White House executive order (F-08)", "Executive order")


def test_whitehouse_proclamation_realtime_and_ceremonial_dropped(make_app):
    app = make_app("dry_run")
    items = _wh_items(app)
    robotics = items["ADJUSTING IMPORTS OF ROBOTICS, INDUSTRIAL MACHINERY, AND THEIR PARTS AND COMPONENTS INTO THE UNITED STATES"]
    assert "section 232" in robotics.summary.lower()          # body sentence, not in the title
    sc = scope.decide(app.sources["whitehouse"], robotics, app.delivery)
    assert sc.route == "realtime" and "Section 232" in sc.reason
    # alert in real time when seen the evening it was signed
    assert _deliver(app, "whitehouse", robotics, robotics.source_published_at + timedelta(minutes=10)) == "realtime"
    assert _deliver(app, "whitehouse", items["Thanksgiving Day, 2026"],
                    items["Thanksgiving Day, 2026"].source_published_at + timedelta(minutes=10)) == "dropped"
    # an EO with no India / tariff keyword goes to the US Morning Wrap
    housing = items["DECREASING HOUSING COSTS FOR THE AMERICAN PEOPLE"]
    assert _deliver(app, "whitehouse", housing, housing.source_published_at + timedelta(minutes=10)) == "us_morning_wrap"


def test_whitehouse_poll_routes(make_app):
    app = make_app("dry_run")
    feed = (FX / "whitehouse_feed_2026-11-26.xml").read_bytes()
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, content=feed, headers={"etag": '"abc"', "content-type": "application/rss+xml"})

    mock_fetcher(app, handler)
    _seed(app, "whitehouse")
    st = app.poll_source("whitehouse")
    assert st is not None and st.new == 30
    rows = _rows(app, "whitehouse")
    assert rows["p:55261"]["status"] == "filtered"                       # Thanksgiving Day, 2026
    assert rows["p:54676"]["status"] == "filtered"                       # nominations
    # the 28 Oct section 232 proclamation is weeks old here: digest, not a stale real-time alert
    assert rows["p:53657"]["route"] == "us_morning_wrap"
    app.poll_source("whitehouse")
    assert seen[-1].headers.get("if-none-match") == '"abc"'             # always conditional


def test_whitehouse_digest_body_quotes_the_page(make_app):
    app = make_app("dry_run")
    page = (FX / "whitehouse_robotics_232_2026-10-28.html").read_bytes()
    mock_fetcher(app, lambda req: httpx.Response(200, content=page))
    it = _wh_items(app)["ADJUSTING IMPORTS OF ROBOTICS, INDUSTRIAL MACHINERY, AND THEIR PARTS AND COMPONENTS INTO THE UNITED STATES"]
    b = app.adapter("whitehouse").digest_body(it)
    assert b.text.startswith("ADJUSTING IMPORTS OF ROBOTICS")
    assert any("section 232 of the Trade Expansion Act of 1962" in p for p in b.paragraphs)
    assert all(p in b.text.split("\n") for p in b.paragraphs)


# ---- USTR -------------------------------------------------------------------------------------------------
S301 = "2026/november/ustr-makes-determinations-and-proposes-actions-14-section-301-investigations-relating-structural"


def test_ustr_listing_and_scope():
    rows = ustr.parse_listing((FX / "ustr_listing_2026-11-26.html").read_bytes())
    assert len(rows) == 30 and rows[0]["date"].isoformat() == "2026-11-25"
    assert all(r["url"].startswith("https://ustr.gov/about/") for r in rows)
    by = {r["key"]: r for r in rows}
    assert ustr.classify(by[S301]["title"])[0] == "realtime_if_keyword"
    readout = next(r for r in rows if r["title"].startswith("Readout of First Meeting"))
    assert ustr.classify(readout["title"])[0] == "drop"


def test_ustr_poll_section301_naming_india_alerts(make_app):
    app = make_app("dry_run")
    listing = (FX / "ustr_listing_2026-11-26.html").read_bytes()
    page = (FX / "ustr_section301_excess_capacity_2026-11-25.html").read_bytes()

    def handler(req):
        return httpx.Response(200, content=page if "/2026/" in req.url.path else listing)

    mock_fetcher(app, handler)
    a = app.adapter("ustr")
    items = {i.ext_id: i for i in a.poll()}
    it = a.enrich(items[S301])
    assert "ustr:india" in it.meta["priority_tags"] and "India" in it.summary
    # posted the same day (date-only stamp): real-time alert
    assert _deliver(app, "ustr", it, it.source_published_at + timedelta(hours=10)) == "realtime"
    readout = next(i for i in items.values() if i.title.startswith("Readout of Ambassador Greer"))
    assert _deliver(app, "ustr", readout, readout.source_published_at + timedelta(hours=10)) == "dropped"
    b = a.digest_body(it)
    assert any("Bangladesh, Japan, and India" in p for p in b.paragraphs)


# ---- Congress + OPM ---------------------------------------------------------------------------------------
def test_congress_laws_kept_and_dropped():
    rows = {r["law"] + r["law_type"]: r for r in congress.parse_laws((FX / "congress_law119_2026-11-26.json").read_bytes())}
    cr = rows["119-103Public Law"]
    assert cr["title"] == "Continuing Appropriations and Extensions Act, 2027"
    assert congress.classify_law(cr)[0] == "us_morning_wrap"
    assert congress.classify_law(rows["119-21Public Law"])[0] == "us_morning_wrap"          # reconciliation
    land = rows["119-146Public Law"]                                                          # Fort Bragg parcel
    assert congress.classify_law(land)[0] == "drop"
    vets = rows["119-149Public Law"]
    assert vets["title"] == "Veterans’ Compensation Cost-of-Living Adjustment Act of 2026"   # mojibake repaired
    assert congress.classify_law(vets)[0] == "drop"
    assert congress.web_url(119, "HR", "6500") == "https://www.congress.gov/bill/119th-congress/house-bill/6500"


def test_congress_poll_sends_key_in_header(make_app):
    app = make_app("dry_run")
    body = (FX / "congress_law119_2026-11-26.json").read_bytes()
    reqs = []

    def handler(req):
        reqs.append(req)
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    mock_fetcher(app, handler)
    _seed(app, "congress")
    st = app.poll_source("congress")
    assert st is not None
    assert reqs[0].headers["x-api-key"] == "DEMO_KEY" and "api_key" not in str(reqs[0].url)
    rows = _rows(app, "congress")
    assert rows["public:119-103"]["route"] == "us_morning_wrap"
    assert rows["public:119-146"]["status"] == "filtered"


def test_congress_digest_summary():
    text = congress.CongressLaws.summary_text((FX / "congress_hr6500_summaries.json").read_bytes())
    assert text.startswith("Continuing Appropriations and Extensions Act, 2027")
    assert "through December 11, 2026" in text


def _opm_shutdown() -> bytes:
    d = json.loads((FX / "opm_status_2026-11-26.json").read_bytes())
    d.update(StatusSummary="Closed", StatusType="Closed", Icon="Closed",
             ShortStatusMessage="Federal agencies are closed due to a lapse in appropriations.",
             LongStatusMessage="Federal agencies are closed due to a lapse in appropriations.\r\n"
                               "Non-excepted employees are in a shutdown furlough status.",
             DateStatusPosted="/Date(1795780000000)/")
    return json.dumps(d).encode()


def test_opm_status_change_only_and_shutdown_realtime(make_app):
    app = make_app("dry_run")
    bodies = [(FX / "opm_status_2026-11-26.json").read_bytes()]
    mock_fetcher(app, lambda req: httpx.Response(200, content=bodies[-1]))
    st = app.poll_source("opm_status")                       # first poll: baseline "Open"
    assert st.new == 1 and st.baseline == 1
    assert app.poll_source("opm_status").new == 0            # same status re-posted: nothing
    bodies.append(_opm_shutdown())
    a = app.adapter("opm_status")
    items = a.poll()
    assert len(items) == 1 and items[0].meta["route_hint"] == "realtime" and items[0].meta["lapse"]
    assert _deliver(app, "opm_status", items[0], items[0].source_published_at + timedelta(minutes=5)) == "realtime"
    assert a.poll() == []                                    # still closed: no new item
    bodies.append((FX / "opm_status_2026-11-26.json").read_bytes())
    reopen = a.poll()
    assert reopen[0].meta["route_hint"] == "us_morning_wrap" and reopen[0].meta["previous_status"] == "Closed"


def test_opm_weather_change_dropped():
    prev = {"status": "Open", "lapse": False}
    s = congress.parse_status((FX / "opm_status_2026-11-26.json").read_bytes())
    s.update(summary="Delayed Arrival", lapse=False)
    assert congress.classify_status(s, prev)[0] == "drop"


# ---- FDA --------------------------------------------------------------------------------------------------
def test_fda_press_scope():
    rows = {r["title"]: r for r in fda.parse_press((FX / "fda_press_rss_2026-11-26.xml").read_bytes())}
    assert len(rows) == 20 and all(r["url"].startswith("https://www.fda.gov/") for r in rows.values())
    india = next(r for t, r in rows.items() if "Indian Nationals" in t)
    assert fda.classify_press(india)[2] == ["fda:india"]
    approval = rows["FDA Approves First Treatment for Limb-Girdle Muscular Dystrophy Type 2I/R9"]
    assert fda.classify_press(approval)[0] == "us_morning_wrap"
    assert fda.classify_press(rows["FDA Launches New Program to Advance Neuroscience Medical Product Development"])[0] == "drop"


def test_fda_india_recall_poll(make_app):
    app = make_app("dry_run")
    body = (FX / "openfda_enforcement_india_2026-11-26.json").read_bytes()
    mock_fetcher(app, lambda req: httpx.Response(200, content=body))
    _seed(app, "fda_recalls")
    st = app.poll_source("fda_recalls")
    assert st.new == 7                                       # 25 records -> 7 recall events
    rows = _rows(app, "fda_recalls")
    msn = rows["event:100172"]
    assert msn["route"] == "us_morning_wrap" and "fda:india" in msn["meta"]["priority_tags"]
    assert len(rows["event:99849"]["meta"]["recall_numbers"]) == 17   # one item per event, not per product
    items = {i.ext_id: i for i in app.adapter("fda_recalls").parse(_res("openfda_enforcement_india_2026-11-26.json"), "india")}
    assert items["event:100172"].title.startswith(
        "MSN Laboratories Private Limited (Formulations Division, Unit-II) (Rangareddy District, India): Class II drug recall")
    text = fda.FdaRecalls.event_text((FX / "openfda_event_100172.json").read_bytes())
    assert "Reason for recall: Presence of Foreign Tablets/Capsules" in text


def test_fda_warning_letters_india_only(make_app):
    app = make_app("dry_run")
    pages = {"alkem-laboratories-limited-736113-11182026": FX / "fda_wl_alkem.html",
             "umicore-argentina-sa-736869-11062026": FX / "fda_wl_umicore.html"}

    def handler(req):
        slug = req.url.path.rsplit("/", 1)[-1]
        if slug in pages:
            return httpx.Response(200, content=pages[slug].read_bytes())
        name = "fda_wl_listing_p2_2026-11-26.html" if req.url.params.get("page") == "1" else "fda_wl_listing_2026-11-26.html"
        return httpx.Response(200, content=(FX / name).read_bytes())

    mock_fetcher(app, handler)
    a = app.adapter("fda_warning_letters")
    items = {i.ext_id: i for i in a.poll()}
    assert len(items) == 13                                  # 10 + 10 rows, overlapping
    alkem = a.enrich(items["alkem-laboratories-limited-736113-11182026"])
    assert alkem.meta["recipient_country"] == "India" and "fda:india" in alkem.meta["priority_tags"]
    now = datetime(2026, 11, 25, 2, 0, tzinfo=timezone.utc)
    assert _deliver(app, "fda_warning_letters", alkem, now) == "us_morning_wrap"
    umicore = a.enrich(items["umicore-argentina-sa-736869-11062026"])
    assert umicore.meta["recipient_country"] == "Belgium"
    assert _deliver(app, "fda_warning_letters", umicore, now) == "dropped"
    tobacco = items["woodstockvapeandglasscom-730482-05222026"]
    assert tobacco.meta["route_hint"] == "drop" and a.enrich(tobacco) is tobacco     # no letter fetch
    b = a.digest_body(alkem)
    assert any("Alkem Laboratories Limited" in p and "Daman" in p for p in b.paragraphs)


def test_fda_warning_letter_columns_health():
    html = (FX / "fda_wl_listing_2026-11-26.html").read_bytes().replace(b"Issuing Office", b"Office")
    with pytest.raises(ValueError, match="columns changed"):
        fda.parse_wl_listing(html)
