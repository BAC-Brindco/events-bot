"""BEA (GDP, PIO) and Census (retail sales, durables) print alerts: calendars (ET -> UTC across DST), parsing
of live-captured releases, window runs producing the dry-run alert, and the not-yet-posted / previous-period
guards.

Fixtures (tests/fixtures/bea), fetched live on 2026-11-26:
  bea_release_dates.json     https://apps.bea.gov/API/signup/release_dates.json
  bea_calendar.ics           https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics
  bea_rss.xml                https://apps.bea.gov/rss/rss.xml  (last build 25 Nov 2026 08:30 EST)
  gdp_2026q3_second.html     .../news/2026/gdp-second-estimate-and-corporate-profits-3rd-quarter-2026 (25 Nov)
  pio_2026_10.html           .../news/2026/personal-income-and-outlays-october-2026 (25 Nov)
  pio_2026_09.html           .../news/2026/personal-income-and-outlays-september-2026 (29 Oct)
  census_calendar.html       https://www.census.gov/economic-indicators/calendar-listview.html (2027 list)
  census_calendar_2026.html  https://www.census.gov/economic-indicators/calendar-listview-2026.html
  census_indicator.xml       https://www.census.gov/economic-indicators/indicator.xml
  marts_current.pdf          https://www.census.gov/retail/marts/www/marts_current.pdf (October 2026, 17 Nov)
  durgd.pdf                  https://www.census.gov/manufacturing/m3/adv/pdf/durgd.pdf (October 2026, 25 Nov)
"""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from events_bot.core.models import ScheduledEvent
from events_bot.core.timeutil import ET
from events_bot.deliver.interface import Dispatcher
from events_bot.extract.validator import validate_all
from events_bot.sources.us import bea, census, prints_base

from .conftest import FIXTURES, mock_fetcher, requires_db

FX = FIXTURES / "bea"
UTC = timezone.utc


def _b(name: str) -> bytes:
    return (FX / name).read_bytes()


def _vals(ex, texts):
    validate_all(ex, texts)
    assert all(e.ok for e in ex), [(e.field, e.validation_error) for e in ex]
    return {e.field: e.value_text for e in ex}


# ---- BEA: calendar ------------------------------------------------------------------------------------------

def test_bea_release_dates_and_ics_et_to_utc():
    j = {(k, at.date()): at for k, at, _ in bea.parse_release_dates(_b("bea_release_dates.json"))}
    # Winter (EST): 08:30 ET = 13:30 UTC; summer (EDT): 12:30 UTC.
    assert j[("gdp", date(2026, 11, 25))].astimezone(UTC) == datetime(2026, 11, 25, 13, 30, tzinfo=UTC)
    assert j[("pce", date(2026, 10, 29))].astimezone(UTC) == datetime(2026, 10, 29, 12, 30, tzinfo=UTC)
    assert j[("gdp", date(2026, 12, 23))].astimezone(ET).hour == 8
    ics = {(k, at.date()): s for k, at, s in bea.parse_ics(_b("bea_calendar.ics"))}
    assert ics[("gdp", date(2026, 11, 25))] == "GDP (Second Estimate) and Corporate Profits, 3rd Quarter 2026"
    assert ics[("pce", date(2026, 12, 23))] == "Personal Income and Outlays, November 2026"
    # folded SUMMARY lines are unfolded and the escapes removed
    assert ics[("gdp", date(2026, 12, 23))].startswith("GDP (Third Estimate), Industries, Corporate Profits, State GDP")
    assert bea.period_key("gdp", ics[("gdp", date(2026, 12, 23))]) == ("q", 3, 2026)


def _bea_handler(rss=b"", pages=None, json_ok=True):
    pages = pages or {}

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if u.endswith("release_dates.json"):
            return httpx.Response(200 if json_ok else 503, content=_b("bea_release_dates.json") if json_ok else b"")
        if u.endswith(".ics"):
            return httpx.Response(200, content=_b("bea_calendar.ics"))
        if u.endswith("rss.xml"):
            return httpx.Response(200, content=rss or _b("bea_rss.xml"))
        if u in pages:
            return httpx.Response(200, content=pages[u])
        return httpx.Response(404, content=b"<html>not found</html>")
    return handler


@requires_db
@pytest.mark.parametrize("json_ok", [True, False])
def test_bea_calendar_events(make_app, monkeypatch, json_ok):
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(prints_base, "utcnow", lambda: now)
    monkeypatch.setattr(bea, "utcnow", lambda: now)
    app = make_app()
    mock_fetcher(app, _bea_handler(json_ok=json_ok))
    evs = {e.ref: e for e in app.adapter("bea").calendar()}
    assert set(evs) == {"usprint:gdp:2026-11-25", "usprint:pce:2026-11-25", "usprint:gdp:2026-12-23",
                        "usprint:pce:2026-12-23"}
    gdp = evs["usprint:gdp:2026-11-25"]
    assert gdp.scheduled_at == datetime(2026, 11, 25, 13, 30, tzinfo=UTC)          # 08:30 EST
    assert gdp.window_start == datetime(2026, 11, 25, 13, 20, tzinfo=UTC)
    assert gdp.meta["calendar_title"] == "GDP (Second Estimate) and Corporate Profits, 3rd Quarter 2026"
    if json_ok:
        assert gdp.meta["ics_confirmed"] is True
    else:
        assert gdp.meta["calendar_source"] == "ics"


# ---- BEA: parsing --------------------------------------------------------------------------------------------

def test_gdp_second_estimate_figures():
    text = bea.page_text(_b("gdp_2026q3_second.html"))
    period, ex, bullets = bea.parse_gdp(text)
    v = _vals(ex, {"release": text})
    assert period == "3rd Quarter 2026 (Second Estimate)"
    assert v == {"bea.gdp.vintage": "Second", "bea.gdp.real": "4.3", "bea.gdp.real_prev_est": "3.4",
                 "bea.gdp.real_prior_q": "2.2", "bea.gdp.pce_price": "2.1", "bea.gdp.core_pce_price": "2.5"}
    assert bullets[0].startswith("Real gross domestic product (GDP ) increased at an annual rate of 4.3 percent")
    assert all(b in text for b in bullets)


def test_pio_october_figures():
    text = bea.page_text(_b("pio_2026_10.html"))
    period, ex, bullets = bea.parse_pio(text)
    v = _vals(ex, {"release": text})
    assert period == "October 2026"
    assert (v["bea.pce.price_mm"], v["bea.pce.price_yy"], v["bea.pce.core_mm"], v["bea.pce.core_yy"]) == \
        ("0.3", "3.8", "0.3", "3.3")
    assert (v["bea.pce.price_mm_prior"], v["bea.pce.core_mm_prior"]) == ("0.5", "0.3")
    assert (v["bea.pce.income_mm"], v["bea.pce.income_mm_prior"]) == ("0.7", "0.3")
    assert (v["bea.pce.spending_mm"], v["bea.pce.spending_mm_prior"]) == ("0.3", "0.6")
    assert {e.period for e in ex if e.field == "bea.pce.price_mm_prior"} == {"September"}
    assert bullets[0].startswith("Personal income increased $201.8 billion (0.7 percent at a monthly rate)")


def test_signed_sentence_keeps_the_verb_for_a_decrease():
    text = ("Real gross domestic product (GDP) decreased at an annual rate of 0.5 percent in the first quarter of "
            "2025.\nIn the fourth quarter, real GDP increased 2.4 percent.")
    import re
    from events_bot.extract.us_release_text import signed
    m = re.search(r"(increased|decreased) at an annual rate of (\d+\.\d+)", text)
    e = signed(text, m, 1, 2, "x")
    assert e.value_text == "decreased at an annual rate of 0.5" == text[e.char_start:e.char_end]
    assert str(e.value_norm) == "-0.5"


def test_rss_matching_by_date_and_period():
    items = bea.parse_rss(_b("bea_rss.xml"))
    at = datetime(2026, 11, 25, 8, 30, tzinfo=ET)
    gdp = bea.match_item(items, "gdp", at, ("q", 3, 2026))
    assert gdp["link"] == "https://www.bea.gov/news/2026/gdp-second-estimate-and-corporate-profits-3rd-quarter-2026"
    assert bea.match_item(items, "pce", at, ("m", 10, 2026))["title"] == "Personal Income and Outlays, October 2026"
    # Not yet in the feed: the 23 Dec releases.
    assert bea.match_item(items, "gdp", datetime(2026, 12, 23, 8, 30, tzinfo=ET), None) is None
    # A scheme-less link in the feed is normalised.
    assert any(i["link"] == "https://www.bea.gov/news/2026/gdp-advance-estimate-4th-quarter-and-year-2025"
               for i in items)


# ---- BEA: window run ---------------------------------------------------------------------------------------

def _spy(monkeypatch):
    sent = []
    orig = Dispatcher.dispatch

    def spy(self, msg, recipients):
        sent.append((self.mode, msg))
        return orig(self, msg, recipients)

    monkeypatch.setattr(Dispatcher, "dispatch", spy)
    return sent


def _event(app, source_id, kind, at, title, **meta):
    ev = ScheduledEvent(ref=f"usprint:{kind}:{at.astimezone(ET).date().isoformat()}", source_id=source_id,
                        event_type=kind, title=title, scheduled_at=at, window_start=at - timedelta(minutes=10),
                        window_end=at + timedelta(hours=1), calendar_url="https://example.invalid/cal",
                        meta={"kind": kind, "calendar_title": title, **meta})
    app.db.upsert_event(ev, at)
    return app.db.get_event(ev.ref)


GDP_URL = "https://www.bea.gov/news/2026/gdp-second-estimate-and-corporate-profits-3rd-quarter-2026"
PIO_URL = "https://www.bea.gov/news/2026/personal-income-and-outlays-october-2026"


@requires_db
@pytest.mark.parametrize("kind,title,subject", [
    ("gdp", "GDP (Second Estimate) and Corporate Profits, 3rd Quarter 2026",
     "[BEA] GDP | 3rd Quarter 2026 (Second Estimate) | Real GDP q/q saar 4.3; prior quarter 2.2"),
    ("pce", "Personal Income and Outlays, October 2026",
     "[BEA] PCE inflation and personal income | October 2026 | PCE prices m/m 0.3; y/y 3.8; core m/m 0.3; "
     "core y/y 3.3"),
])
def test_bea_window_run_sends_dry_run_alert(make_app, monkeypatch, kind, title, subject):
    at = datetime(2026, 11, 25, 13, 30, tzinfo=UTC)
    monkeypatch.setattr(prints_base, "utcnow", lambda: at + timedelta(minutes=2))
    app = make_app("dry_run")
    mock_fetcher(app, _bea_handler(pages={GDP_URL: _b("gdp_2026q3_second.html"), PIO_URL: _b("pio_2026_10.html")}))
    sent = _spy(monkeypatch)
    ev = _event(app, "bea", kind, at, title)
    app.adapter("bea").poll_event(ev)
    assert [m.subject for _, m in sent] == [subject]
    assert "EXTRACTION FAILED" not in sent[0][1].body_text
    got = app.db.get_event(ev["ref"])
    assert got["status"] == "captured" and got["meta"]["stage1_at"]


@requires_db
def test_bea_not_yet_posted_and_stale_page_do_not_send(make_app, monkeypatch):
    app = make_app("dry_run")
    calls = []
    inner = _bea_handler(pages={PIO_URL: _b("pio_2026_09.html")})       # the October URL still serving September

    def handler(req):
        calls.append(str(req.url))
        return inner(req)

    mock_fetcher(app, handler)
    sent = _spy(monkeypatch)
    # (1) 23 Dec window open: the feed has no item stamped that day -> no page fetch, nothing sent.
    dec = datetime(2026, 12, 23, 13, 30, tzinfo=UTC)
    monkeypatch.setattr(prints_base, "utcnow", lambda: dec + timedelta(minutes=1))
    app.adapter("bea").poll_event(_event(app, "bea", "pce", dec, "Personal Income and Outlays, November 2026"))
    assert sent == [] and not any("news/2026" in c for c in calls)
    # (2) 25 Nov: the RSS item is there but the page is still the previous period -> nothing sent.
    nov = datetime(2026, 11, 25, 13, 30, tzinfo=UTC)
    monkeypatch.setattr(prints_base, "utcnow", lambda: nov + timedelta(minutes=1))
    ev = _event(app, "bea", "pce", nov, "Personal Income and Outlays, October 2026")
    app.adapter("bea").poll_event(ev)
    assert sent == [] and PIO_URL in calls
    assert not (app.db.get_event(ev["ref"])["meta"] or {}).get("stage1_at")
    # (3) before 08:30 ET nothing is fetched at all (embargo).
    calls.clear()
    monkeypatch.setattr(prints_base, "utcnow", lambda: nov - timedelta(minutes=5))
    app.adapter("bea").poll_event(ev)
    assert calls == []


# ---- Census -------------------------------------------------------------------------------------------------

def test_census_calendar_rows_and_anchor_trap():
    rows = census.parse_calendar(_b("census_calendar_2026.html"))
    by = {(r["kind"], r["release_at"].date()): r for r in rows if r["kind"]}
    retail = by[("retail", date(2026, 12, 16))]
    assert retail["period"] == "November 2026"
    assert retail["release_at"].astimezone(UTC) == datetime(2026, 12, 16, 13, 30, tzinfo=UTC)
    # EDT: 08:30 ET = 12:30 UTC
    assert by[("durables", date(2026, 9, 25))]["release_at"].astimezone(UTC) == datetime(2026, 9, 25, 12, 30, tzinfo=UTC)
    # The page's hidden anchor disagrees with the visible date on one row; the visible date wins.
    feb = by[("durables", date(2026, 4, 7))]
    assert feb["anchor"] == "202604270830" and feb["anchor_mismatch"]
    assert len(census.parse_calendar(_b("census_calendar.html"))) >= census.MIN_EVENTS


def _census_handler(pdfs=None, calls=None):
    pdfs = {"marts_current.pdf": _b("marts_current.pdf"), "durgd.pdf": _b("durgd.pdf")} if pdfs is None else pdfs

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if calls is not None:
            calls.append(u)
        name = u.rsplit("/", 1)[-1]
        if name == "calendar-listview.html":
            return httpx.Response(200, content=_b("census_calendar.html"))
        if name == "calendar-listview-2026.html":
            return httpx.Response(200, content=_b("census_calendar_2026.html"))
        if name == "indicator.xml":
            return httpx.Response(200, content=_b("census_indicator.xml"))
        if name in pdfs:
            return httpx.Response(200, content=pdfs[name])
        return httpx.Response(404, content=b"<html>not found</html>")
    return handler


@requires_db
def test_census_calendar_events_follow_the_current_year_page(make_app, monkeypatch):
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(prints_base, "utcnow", lambda: now)
    monkeypatch.setattr(census, "utcnow", lambda: now)
    app = make_app()
    mock_fetcher(app, _census_handler())
    evs = {e.ref: e for e in app.adapter("census").calendar()}
    assert {"usprint:durables:2026-11-25", "usprint:retail:2026-12-16", "usprint:durables:2026-12-23"} <= set(evs)
    d = evs["usprint:durables:2026-11-25"]
    assert d.scheduled_at == datetime(2026, 11, 25, 13, 30, tzinfo=UTC) and d.meta["period"] == "October 2026"


def test_retail_and_durables_figures():
    cap = census.CensusPrints.build_capture("retail", _b("marts_current.pdf"), "u", date(2026, 11, 17), "October 2026")
    v = _vals(cap.extractions, cap.texts)
    assert v == {"census.retail.total_mm": "0.2", "census.retail.total_mm_prior": "0.8",
                 "census.retail.total_yy": "6.8", "census.retail.exauto_mm": "0.6",
                 "census.retail.exauto_mm_prior": "1.1", "census.retail.exautogas_mm": "0.5",
                 "census.retail.exautogas_mm_prior": "0.5"}
    assert cap.bullets[0].startswith("Advance estimates of U.S. retail and food services sales for October 2026")
    cap = census.CensusPrints.build_capture("durables", _b("durgd.pdf"), "u", date(2026, 11, 25), "October 2026")
    v = _vals(cap.extractions, cap.texts)
    assert (v["census.durables.total_mm"], v["census.durables.total_mm_prior"]) == ("0.2", "2.0")
    assert (v["census.durables.extrans_mm"], v["census.durables.extrans_mm_prior"]) == ("0.5", "0.5")
    assert (v["census.durables.exdef_mm"], v["census.durables.core_capex_mm"]) == ("0.4", "1.2")
    # The PDF is still the previous release on the next release day: no capture.
    assert census.CensusPrints.build_capture("durables", _b("durgd.pdf"), "u", date(2026, 12, 23),
                                             "November 2026") is None


@requires_db
@pytest.mark.parametrize("kind,day,subject", [
    ("retail", date(2026, 11, 17), "[CENSUS] Retail sales | October 2026 | Retail sales m/m 0.2; ex autos 0.6"),
    ("durables", date(2026, 11, 25),
     "[CENSUS] Durable goods orders | October 2026 | Durable goods orders m/m 0.2; ex transportation 0.5"),
])
def test_census_window_run_sends_dry_run_alert(make_app, monkeypatch, kind, day, subject):
    at = datetime(day.year, day.month, day.day, 8, 30, tzinfo=ET).astimezone(UTC)
    monkeypatch.setattr(prints_base, "utcnow", lambda: at + timedelta(seconds=30))
    monkeypatch.setattr(census, "utcnow", lambda: at + timedelta(seconds=30))
    app = make_app("dry_run")
    mock_fetcher(app, _census_handler())
    sent = _spy(monkeypatch)
    ev = _event(app, "census", kind, at, "x", period="October 2026")
    app.adapter("census").poll_event(ev)
    assert [m.subject for _, m in sent] == [subject]
    assert "EXTRACTION FAILED" not in sent[0][1].body_text
    assert app.db.get_event(ev["ref"])["status"] == "captured"


@requires_db
def test_census_previous_period_waits_then_indicator_fallback(make_app, monkeypatch):
    app = make_app("dry_run")
    calls = []
    sent = _spy(monkeypatch)
    # Next retail release (16 Dec, November 2026): the PDF and indicator.xml are both still October.
    mock_fetcher(app, _census_handler(calls=calls))
    dec = datetime(2026, 12, 16, 13, 30, tzinfo=UTC)
    ev = _event(app, "census", "retail", dec, "x", period="November 2026")
    for after in (timedelta(minutes=1), timedelta(minutes=20)):
        monkeypatch.setattr(prints_base, "utcnow", lambda a=after: dec + a)
        monkeypatch.setattr(census, "utcnow", lambda a=after: dec + a)
        app.adapter("census").poll_event(ev)
    assert sent == []
    assert sum(c.endswith("indicator.xml") for c in calls) == 1        # only after FALLBACK_AFTER
    # Durables 25 Nov with the PDF missing: after 10 minutes indicator.xml (period October 2026) is used.
    mock_fetcher(app, _census_handler(pdfs={}))
    nov = datetime(2026, 11, 25, 13, 30, tzinfo=UTC)
    ev = _event(app, "census", "durables", nov, "x", period="October 2026")
    monkeypatch.setattr(prints_base, "utcnow", lambda: nov + timedelta(minutes=2))
    monkeypatch.setattr(census, "utcnow", lambda: nov + timedelta(minutes=2))
    app.adapter("census").poll_event(ev)
    assert sent == []                                                   # too early for the fallback
    monkeypatch.setattr(prints_base, "utcnow", lambda: nov + timedelta(minutes=12))
    monkeypatch.setattr(census, "utcnow", lambda: nov + timedelta(minutes=12))
    app.adapter("census").poll_event(ev)
    assert [m.subject for _, m in sent] == ["[CENSUS] Durable goods orders | October 2026 | Durable goods orders m/m +0.2"]
