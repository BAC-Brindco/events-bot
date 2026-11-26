"""BLS print alerts: ICS calendar (ET -> UTC across DST), nr0 parsing on live-captured releases, window run
producing the dry-run alert, and the 'previous release still up' guard.

Fixtures were fetched live on 2026-11-26 with prints_base.BROWSER_HEADERS:
  bls_2026-11-26.ics            https://www.bls.gov/schedule/news_release/bls.ics
  cpi_nr0_2026-11-10.htm        https://www.bls.gov/news.release/cpi.nr0.htm     (October 2026 CPI, 10 Nov 08:30 ET)
  empsit_nr0_2026-11-06.htm     https://www.bls.gov/news.release/empsit.nr0.htm  (October 2026, 6 Nov 08:30 ET)
  ppi_nr0_2026-11-13.htm        https://www.bls.gov/news.release/ppi.nr0.htm     (October 2026, 13 Nov 08:30 ET)
  jolts_nr0_2026-11-03.htm      https://www.bls.gov/news.release/jolts.nr0.htm   (September 2026, 3 Nov 10:00 ET)
"""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from events_bot.core.timeutil import ET
from events_bot.deliver.interface import Dispatcher
from events_bot.extract import bls as bx
from events_bot.extract.validator import validate_all
from events_bot.sources.us import bls, prints_base

from .conftest import FIXTURES, mock_fetcher, requires_db

FX = FIXTURES / "bls"
ICS = FX / "bls_2026-11-26.ics"
PAGES = {"cpi": FX / "cpi_nr0_2026-11-10.htm", "nfp": FX / "empsit_nr0_2026-11-06.htm",
         "ppi": FX / "ppi_nr0_2026-11-13.htm", "jolts": FX / "jolts_nr0_2026-11-03.htm"}
RELEASED = {"cpi": datetime(2026, 11, 10, 8, 30, tzinfo=ET), "nfp": datetime(2026, 11, 6, 8, 30, tzinfo=ET),
            "ppi": datetime(2026, 11, 13, 8, 30, tzinfo=ET), "jolts": datetime(2026, 11, 3, 10, 0, tzinfo=ET)}
UTC = timezone.utc


def _vals(kind):
    text, ex = bx.PARSERS[kind](PAGES[kind].read_bytes())
    validate_all(ex, {"release": text})
    assert all(e.ok for e in ex), [(e.field, e.validation_error) for e in ex]
    return text, {e.field: e.value_text for e in ex}, {e.field: e.period for e in ex}


# ---- calendar --------------------------------------------------------------------------------------------

def test_ics_kinds_and_et_to_utc_across_dst():
    rels = bls.parse_ics(ICS.read_bytes())
    by = {(r.kind, r.release_at.date()): r for r in rels}
    assert {r.kind for r in rels} == {"cpi", "nfp", "ppi", "jolts"}
    # Not tracked: State JOLTS, Employment Situation of Veterans, Real Earnings (same day as CPI).
    assert not any(r.title.startswith("State ") or "Veterans" in r.title for r in rels)
    # Winter (EST, UTC-5): 08:30 ET = 13:30 UTC = 19:00 IST.
    cpi_nov = by[("cpi", date(2026, 11, 10))].release_at
    assert cpi_nov.astimezone(UTC) == datetime(2026, 11, 10, 13, 30, tzinfo=UTC)
    # Summer (EDT, UTC-4): 08:30 ET = 12:30 UTC.
    cpi_jul = by[("cpi", date(2026, 7, 14))].release_at
    assert cpi_jul.astimezone(UTC) == datetime(2026, 7, 14, 12, 30, tzinfo=UTC)
    # JOLTS at 10:00 ET; Dec Empsit/CPI/PPI dates as BLS lists them.
    assert by[("jolts", date(2026, 12, 1))].release_at.astimezone(UTC) == datetime(2026, 12, 1, 15, 0, tzinfo=UTC)
    assert {("nfp", date(2026, 12, 4)), ("cpi", date(2026, 12, 10)), ("ppi", date(2026, 12, 15))} <= set(by)
    # BLS lists no release of these four on 2026-11-25.
    assert not any(d == date(2026, 11, 25) for _, d in by)


def test_ics_line_unfolding():
    raw = (b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;TZID=US-Eastern:20260612T083000\r\n"
           b"SUMMARY:Consumer Price \r\n Index\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    [r] = bls.parse_ics(raw)
    assert r.kind == "cpi" and r.release_at.astimezone(UTC) == datetime(2026, 6, 12, 12, 30, tzinfo=UTC)


# ---- parsing (pure) --------------------------------------------------------------------------------------

def test_cpi_october_2026_figures():
    text, v, p = _vals("cpi")
    assert bx.embargo(text) == (date(2026, 11, 10), 8, 30) and bx.period(text) == "October 2026"
    assert v == {"cpi.headline.mom": "0.4", "cpi.headline.mom.prior": "0.6",
                 "cpi.core.mom": "0.3", "cpi.core.mom.prior": "0.3"}
    assert p["cpi.headline.mom.prior"] == "September 2026"
    # 12-month changes are not published for October 2026 (no Oct 2025 data, 2025 lapse): Table A shows "-".
    assert "cpi.headline.yoy" not in v and "cpi.core.yoy" not in v


def test_empsit_october_2026_figures():
    text, v, p = _vals("nfp")
    assert bx.embargo(text) == (date(2026, 11, 6), 8, 30) and bx.period(text) == "October 2026"
    assert v == {"nfp.change": "165,000", "ur": "4.1", "nfp.change.prior": "+60,000", "ahe.mom": "0.4",
                 "ahe.level": "$38.00", "ahe.yoy": "3.1"}
    assert p["nfp.change.prior"] == "September 2026"


def test_ppi_october_2026_figures():
    text, v, _ = _vals("ppi")
    assert bx.embargo(text) == (date(2026, 11, 13), 8, 30) and bx.period(text) == "October 2026"
    assert v == {"ppi.fd.mom": "0.3", "ppi.fd.yoy": "6.5", "ppi.core.mom": "0.5", "ppi.core.yoy": "4.9",
                 "ppi.fd.mom.prior": "1.4", "ppi.fd.yoy.prior": "6.3", "ppi.core.mom.prior": "0.5",
                 "ppi.core.yoy.prior": "5.0"}
    # Cross-check against the release's own lead prose.
    assert "rose 0.3 percent in October" in text and "increased 6.5 percent for the 12 months" in text


def test_jolts_september_2026_figures():
    text, v, _ = _vals("jolts")
    assert bx.embargo(text) == (date(2026, 11, 3), 10, 0) and bx.period(text) == "September 2026"
    assert v == {"jolts.openings": "7.5", "jolts.rate": "4.5"}


def test_lead_sentences_are_verbatim_lines():
    for kind in PAGES:
        text, _ = bx.PARSERS[kind](PAGES[kind].read_bytes())
        bullets = bx.lead_sentences(text)
        assert bullets and all(any(b in ln for ln in text.split("\n")) for b in bullets)
        assert "U.S. Bureau of Labor Statistics reported today" in bullets[0]


def test_negative_payrolls_keep_direction():
    page = PAGES["nfp"].read_bytes().replace(b"Total nonfarm payroll employment rose by 165,000 in October",
                                             b"Total nonfarm payroll employment fell by 32,000 in October")
    text, ex = bx.parse_empsit(page)
    f = {e.field: e.value_text for e in ex}
    assert f.get("nfp.change_down") == "32,000" and "nfp.change" not in f


def test_previous_release_still_up_is_not_captured():
    # The live CPI page (released 2026-11-10) polled in the 2026-12-10 window, or the 2026-11-25 date the
    # desk expected: embargo date differs from the scheduled date -> no capture, no send.
    content = PAGES["cpi"].read_bytes()
    assert bls.build_capture("cpi", content, date(2026, 12, 10)) is None
    assert bls.build_capture("cpi", content, date(2026, 11, 25)) is None
    cap = bls.build_capture("cpi", content, date(2026, 11, 10))
    assert cap is not None and cap.period == "October 2026"
    assert cap.release_at == datetime(2026, 11, 10, 8, 30, tzinfo=ET)


# ---- adapter with DB -------------------------------------------------------------------------------------

def _handler(pages: dict[str, bytes], seen: list):
    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        url = str(req.url)
        if req.headers.get("sec-fetch-mode") != "navigate":
            return httpx.Response(403, content=b"Access Denied")          # what Akamai does
        if url == bls.ICS_URL:
            return httpx.Response(200, content=ICS.read_bytes(), headers={"content-type": "text/calendar"})
        for kind, body in pages.items():
            if url == bls.nr0_url(kind):
                return httpx.Response(200, content=body, headers={"content-type": "text/html"})
        return httpx.Response(404)
    return h


@requires_db
def test_calendar_events_from_adapter(make_app, monkeypatch):
    monkeypatch.setattr(prints_base, "utcnow", lambda: datetime(2026, 11, 26, 6, 0, tzinfo=UTC))
    app = make_app()
    seen: list = []
    mock_fetcher(app, _handler({}, seen))
    evs = {e.ref: e for e in app.adapter("bls").calendar()}
    assert set(evs) == {"usprint:jolts:2026-12-01", "usprint:nfp:2026-12-04", "usprint:cpi:2026-12-10",
                        "usprint:ppi:2026-12-15"}
    cpi = evs["usprint:cpi:2026-12-10"]
    assert cpi.scheduled_at.astimezone(UTC) == datetime(2026, 12, 10, 13, 30, tzinfo=UTC)
    assert cpi.window_start.astimezone(UTC) == datetime(2026, 12, 10, 13, 20, tzinfo=UTC)
    assert cpi.event_type == "cpi" and cpi.meta["calendar_title"] == "Consumer Price Index"
    assert seen and seen[0].headers["sec-ch-ua-platform"] == '"Windows"'


def _arm(app, kind: str, at: datetime):
    from events_bot.core.models import ScheduledEvent
    ref = f"usprint:{kind}:{at.astimezone(ET).date().isoformat()}"
    ev = ScheduledEvent(ref=ref, source_id="bls", event_type=kind, title=kind, scheduled_at=at,
                        window_start=at - timedelta(minutes=10), window_end=at + timedelta(minutes=60),
                        calendar_url=bls.ICS_URL, meta={"kind": kind, "calendar_title": kind})
    app.db.upsert_event(ev, at)
    return ref


def _spy(monkeypatch):
    sent = []
    orig = Dispatcher.dispatch

    def spy(self, msg, recipients):
        sent.append((self.mode, msg))
        return orig(self, msg, recipients) if self.mode != "live" else None
    monkeypatch.setattr(Dispatcher, "dispatch", spy)
    return sent


EXPECT_SUBJECT = {
    "cpi": "[BLS] US CPI | October 2026 | m/m 0.4; core m/m 0.3",
    "nfp": "[BLS] US Employment Situation | October 2026 | payrolls 165,000; UR 4.1; AHE m/m 0.4",
    "ppi": "[BLS] US PPI | October 2026 | final demand m/m 0.3; y/y 6.5; core m/m 0.5",
    "jolts": "[BLS] US JOLTS | September 2026 | job openings 7.5; rate 4.5",
}


@requires_db
@pytest.mark.parametrize("kind", ["cpi", "nfp", "ppi", "jolts"])
def test_window_run_sends_dry_run_alert_with_release_figures(make_app, monkeypatch, kind):
    at = RELEASED[kind]
    monkeypatch.setattr(prints_base, "utcnow", lambda: at + timedelta(seconds=40))
    app = make_app()
    seen: list = []
    mock_fetcher(app, _handler({kind: PAGES[kind].read_bytes()}, seen))
    sent = _spy(monkeypatch)
    ref = _arm(app, kind, at)

    app.adapter("bls").poll_event(app.db.get_event(ref))

    assert len(sent) == 1
    mode, msg = sent[0]
    assert mode == "dry_run"
    assert msg.subject == EXPECT_SUBJECT[kind]
    assert "EXTRACTION FAILED" not in msg.body_text
    assert "reported today" in msg.body_text                      # verbatim lead sentence as a key point
    got = app.db.get_event(ref)
    assert got["status"] == "captured" and got["meta"]["stage1_at"]
    # A second poll in the same window does not send again.
    app.adapter("bls").poll_event(app.db.get_event(ref))
    assert len(sent) == 1


@requires_db
def test_cpi_alert_body_has_prior_and_lapse_note(make_app, monkeypatch):
    at = RELEASED["cpi"]
    monkeypatch.setattr(prints_base, "utcnow", lambda: at + timedelta(seconds=40))
    app = make_app()
    mock_fetcher(app, _handler({"cpi": PAGES["cpi"].read_bytes()}, []))
    sent = _spy(monkeypatch)
    app.adapter("bls").poll_event(app.db.get_event(_arm(app, "cpi", at)))
    body = sent[0][1].body_text
    assert "0.6" in body and "-0.2" in body                       # prior Sep m/m and computed change
    assert "lapse in appropriations" in body
    assert "https://www.bls.gov/news.release/cpi.nr0.htm" in body


@requires_db
@pytest.mark.parametrize("window", [datetime(2026, 11, 25, 8, 30, tzinfo=ET), datetime(2026, 12, 10, 8, 30, tzinfo=ET)])
def test_window_run_with_previous_release_still_up_sends_nothing(make_app, monkeypatch, window):
    """`usprint:cpi:2026-11-25` (the date the desk expected) and the next real CPI window both see the
    10 Nov page: its embargo date is not the scheduled date, so nothing is sent and the event stays open."""
    monkeypatch.setattr(prints_base, "utcnow", lambda: window + timedelta(minutes=2))
    app = make_app()
    seen: list = []
    mock_fetcher(app, _handler({"cpi": PAGES["cpi"].read_bytes()}, seen))
    sent = _spy(monkeypatch)
    ref = _arm(app, "cpi", window)

    app.adapter("bls").poll_event(app.db.get_event(ref))

    assert sent == [] and len(seen) == 1
    got = app.db.get_event(ref)
    assert got["status"] != "captured" and not got["meta"].get("stage1_at")


@requires_db
def test_before_release_time_does_not_fetch(make_app, monkeypatch):
    at = RELEASED["cpi"]
    monkeypatch.setattr(prints_base, "utcnow", lambda: at - timedelta(minutes=3))
    app = make_app()
    seen: list = []
    mock_fetcher(app, _handler({"cpi": PAGES["cpi"].read_bytes()}, seen))
    app.adapter("bls").poll_event(app.db.get_event(_arm(app, "cpi", at)))
    assert seen == []
