"""FOMC adapter: calendar parsing and the in-window flow against a mocked federalreserve.gov."""
from datetime import date, datetime, timedelta, timezone

import httpx

from events_bot.core.timeutil import ET
from events_bot.sources.us import fed

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "fed"


def test_calendar_parses_2026_meetings_with_sep_flags_and_links():
    rows = fed.parse_calendar((FX / "fomccalendars_2026-10-06.htm").read_bytes())
    y26 = [r for r in rows if r["date"].year == 2026]
    assert [r["date"] for r in y26] == [date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
                                        date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9)]
    assert [r["sep"] for r in y26] == [False, True, False, True, False, True, False, True]
    sep = next(r for r in y26 if r["date"] == date(2026, 9, 16))
    assert sep["links"]["statement"].endswith("monetary20260916a.htm")
    assert sep["links"]["sep"].endswith("fomcprojtabl20260916.htm")
    assert "presser" in sep["links"]
    assert rows == sorted(rows, key=lambda r: r["date"])


def test_release_time_is_1400_eastern_across_dst():
    assert fed.release_at(date(2026, 9, 16)).astimezone(timezone.utc).hour == 18   # EDT
    assert fed.release_at(date(2026, 12, 9)).astimezone(timezone.utc).hour == 19   # EST


class FakeFed:
    """Serves the fixture documents only once `live` is set; 404 before (unpublished)."""

    def __init__(self):
        self.live = False
        self.hits: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        name = req.url.path.rsplit("/", 1)[-1]
        self.hits.append(name)
        if name == "fomccalendars.htm":
            return httpx.Response(200, content=(FX / "fomccalendars_2026-10-06.htm").read_bytes())
        p = FX / name
        is_prior = "20260729" in name
        if p.exists() and (self.live or is_prior):
            return httpx.Response(200, content=p.read_bytes(), headers={"content-type": "text/html"})
        return httpx.Response(404, content=b"<html>Not found</html>")


def _event(app, d: date, sep: bool, prior: date) -> dict:
    from events_bot.core.models import ScheduledEvent
    at = fed.release_at(d)
    ev = ScheduledEvent(ref=f"fomc:{d.isoformat()}", source_id="fomc", event_type="fomc_decision", title="t",
                        scheduled_at=at, window_start=at - fed.WINDOW_BEFORE, window_end=at + fed.WINDOW_AFTER,
                        calendar_url="u", meta={"sep": sep, "links": {}, "prior": prior.isoformat()})
    app.db.upsert_event(ev, at)
    return app.db.get_event(ev.ref)


def test_in_window_flow_stage1_then_stage2_once(make_app, monkeypatch, tmp_path):
    app = make_app("dry_run")
    site = FakeFed()
    mock_fetcher(app, site)
    d, prior = date(2026, 9, 16), date(2026, 7, 29)
    at = fed.release_at(d).astimezone(timezone.utc)
    clock = {"now": at - timedelta(minutes=5)}
    monkeypatch.setattr(fed, "utcnow", lambda: clock["now"])
    adapter = app.adapter("fomc")
    written = lambda: sorted(p.name for p in app.settings.out_dir.rglob("*.html"))  # noqa: E731

    adapter.poll_event(_event(app, d, True, prior))          # before release: 404, nothing sent
    assert written() == []

    site.live = True
    clock["now"] = at + timedelta(seconds=40)
    adapter.poll_event(app.db.get_event("fomc:2026-09-16"))
    s1 = written()
    assert len(s1) == 1 and "stage1" in s1[0]
    ev = app.db.get_event("fomc:2026-09-16")
    assert ev["meta"]["stage1_at"] and ev["meta"]["stage1_complete"] is True and ev["status"] == "captured"

    clock["now"] = at + timedelta(minutes=10)                # still before Stage 2 time: no new message
    adapter.poll_event(app.db.get_event("fomc:2026-09-16"))
    assert written() == s1

    clock["now"] = at + timedelta(minutes=61)
    adapter.poll_event(app.db.get_event("fomc:2026-09-16"))
    out = written()
    assert len(out) == 2 and any("stage2" in n for n in out)
    clock["now"] = at + timedelta(minutes=80)
    adapter.poll_event(app.db.get_event("fomc:2026-09-16"))  # done: no more fetches or messages
    n_hits = len(site.hits)
    adapter.poll_event(app.db.get_event("fomc:2026-09-16"))
    assert len(site.hits) == n_hits and written() == out

    # extractions were stored against the archived documents, all validated
    rows = app.db.q("select field, value_text, validated, validation_error from extractions")
    assert any(r["field"] == "fomc.target_low" and r["value_text"] == "3-3/4" for r in rows)
    assert all(r["validated"] and r["validation_error"] is None for r in rows)


def test_stage1_waits_for_sep_then_sends_without_it(make_app, monkeypatch):
    app = make_app("dry_run")
    site = FakeFed()
    site.live = True

    def no_sep(req):  # SEP page late
        if "fomcprojtabl" in req.url.path:
            return httpx.Response(404, content=b"nf")
        return site(req)
    mock_fetcher(app, no_sep)
    d = date(2026, 9, 16)
    at = fed.release_at(d).astimezone(timezone.utc)
    clock = {"now": at + timedelta(seconds=30)}
    monkeypatch.setattr(fed, "utcnow", lambda: clock["now"])
    adapter = app.adapter("fomc")
    adapter.poll_event(_event(app, d, True, date(2026, 7, 29)))
    assert not list(app.settings.out_dir.rglob("*.html"))     # waiting for the SEP
    clock["now"] = at + timedelta(minutes=6)
    adapter.poll_event(app.db.get_event("fomc:2026-09-16"))
    assert len(list(app.settings.out_dir.rglob("*.html"))) == 1
    assert app.db.get_event("fomc:2026-09-16")["meta"]["stage1_complete"] is False


def test_stale_statement_is_ignored(make_app, monkeypatch):
    """Yesterday's statement served at today's URL (cache) must not alert."""
    app = make_app("dry_run")
    sept = (FX / "monetary20260729a.htm").read_bytes()
    mock_fetcher(app, lambda req: httpx.Response(200, content=sept))
    d = date(2026, 9, 16)
    monkeypatch.setattr(fed, "utcnow", lambda: fed.release_at(d).astimezone(timezone.utc) + timedelta(minutes=2))
    app.adapter("fomc").poll_event(_event(app, d, False, date(2026, 7, 29)))
    assert not list(app.settings.out_dir.rglob("*.html"))


def test_build_messages_numbers_all_come_from_sources():
    """Every number in the Stage 1 text appears verbatim in a source document."""
    import re
    docs = {k: (FX / f).read_bytes() for k, f in (("statement", "monetary20260916a.htm"),
                                                    ("impl", "monetary20260916a1.htm"),
                                                    ("sep", "fomcprojtabl20260916.htm"))}
    m1, _, ex, texts = fed.build_messages(date(2026, 9, 16), docs, None, None, fed.doc_urls(date(2026, 9, 16)),
                                          first_seen=datetime(2026, 9, 16, 18, 0, 40, tzinfo=timezone.utc),
                                          mode="dry_run", stage2=False)
    body = m1.body_text.split("\nRelease:")[0]          # timestamps and URLs below are not source figures
    allsrc = "\n".join(texts.values())
    for tok in re.findall(r"\d+(?:[.\-/‑]\d+)*", body):
        assert tok in allsrc, tok
