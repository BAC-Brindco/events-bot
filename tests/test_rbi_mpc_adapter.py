"""RBI MPC adapter: schedule/index parsing and the in-window flow against a mocked rbi.org.in."""
import re
from datetime import date, datetime, timedelta, timezone

import httpx

from events_bot.core.models import ScheduledEvent
from events_bot.sources.india import rbi_mpc

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "rbi_mpc"
RSS = (FIXTURES / "rbi" / "pressreleases_rss_2026-10-03.xml").read_bytes()   # max prid 63719, no MPC items


def test_schedule_both_fiscal_years_including_cross_month():
    fy27 = rbi_mpc.parse_schedule((FX / "Annualpolicy_2026-10-06.html").read_bytes())
    assert fy27 == [date(2026, 4, 8), date(2026, 6, 5), date(2026, 8, 5), date(2026, 10, 7), date(2026, 12, 4),
                    date(2027, 2, 5)]
    fy26 = rbi_mpc.parse_schedule((FX / "Annualpolicy_FY2025-26.html").read_bytes())
    assert date(2025, 10, 1) in fy26          # "September 29, 30 and October 1, 2025"


def test_index_maps_meetings_to_prids():
    idx = rbi_mpc.parse_index((FX / "Annualpolicy_2026-10-06.html").read_bytes())
    assert idx[date(2026, 8, 5)] == {"resolution": 63287, "governor": 63288}
    idx26 = rbi_mpc.parse_index((FX / "Annualpolicy_FY2025-26.html").read_bytes())
    assert idx26[date(2025, 10, 1)]["resolution"] == 61332


class FakeRbi:
    """RSS without the MPC item; the resolution appears at prid 63721 once `live`."""

    def __init__(self, via_rss: bool = False):
        self.live, self.via_rss = False, via_rss
        self.hits: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        self.hits.append(u)
        if u.endswith("pressreleases_rss.xml"):
            body = RSS
            if self.live and self.via_rss:
                item = (b"<item><title>Monetary Policy Statement, 2026-27 Resolution of the Monetary Policy Committee "
                        b"(MPC) August 3 to 5, 2026</title><link>https://www.rbi.org.in/Scripts/"
                        b"BS_PressReleaseDisplay.aspx?prid=63721</link><pubDate>Wed, 05 Aug 2026 10:00:00</pubDate>"
                        b"</item>")
                body = body.replace(b"<item>", item + b"<item>", 1)
            return httpx.Response(200, content=body)
        m = re.search(r"prid=(\d+)", u)
        if m:
            prid = int(m.group(1))
            if prid == 63721 and self.live:
                return httpx.Response(200, content=(FX / "pr_63287.html").read_bytes())
            if prid == 62863:                                       # prior meeting (Jun 5)
                return httpx.Response(200, content=(FX / "pr_62863.html").read_bytes())
            return httpx.Response(200, content=b"<html><body><table><tr><td>shell</td></tr></table></body></html>")
        return httpx.Response(404)


def _event(app, d: date) -> dict:
    at = rbi_mpc.release_at(d)
    app.db.upsert_event(ScheduledEvent(
        ref=f"rbi_mpc:{d.isoformat()}", source_id="rbi_mpc", event_type="rbi_mpc_decision", title="t",
        scheduled_at=at, window_start=at - rbi_mpc.WINDOW_BEFORE, window_end=at + rbi_mpc.WINDOW_AFTER,
        calendar_url="u", meta={"prior": "2026-06-05", "prior_resolution_prid": 62863}), at)
    return app.db.get_event(f"rbi_mpc:{d.isoformat()}")


def _run(make_app, monkeypatch, via_rss: bool):
    app = make_app("dry_run")
    site = FakeRbi(via_rss)
    mock_fetcher(app, site)
    d = date(2026, 8, 5)
    at = rbi_mpc.release_at(d).astimezone(timezone.utc)
    clock = {"now": at - timedelta(minutes=5)}
    monkeypatch.setattr(rbi_mpc, "utcnow", lambda: clock["now"])
    a = app.adapter("rbi_mpc")
    out = lambda: sorted(p.name for p in app.settings.out_dir.rglob("*.html"))  # noqa: E731
    a.poll_event(_event(app, d))
    assert out() == []
    site.live = True
    clock["now"] = at + timedelta(minutes=1)
    a.poll_event(app.db.get_event("rbi_mpc:2026-08-05"))
    assert len(out()) == 1 and "stage1" in out()[0]
    ev = app.db.get_event("rbi_mpc:2026-08-05")
    assert ev["meta"]["resolution_prid"] == 63721 and ev["status"] == "captured"
    clock["now"] = at + timedelta(minutes=61)
    a.poll_event(app.db.get_event("rbi_mpc:2026-08-05"))
    assert len(out()) == 2
    n = len(site.hits)
    a.poll_event(app.db.get_event("rbi_mpc:2026-08-05"))
    assert len(site.hits) == n
    s2 = next(p for p in app.settings.out_dir.rglob("*stage2*.html")).read_text(encoding="utf-8")
    assert "Resolution redline" in s2 and "05 Jun 2026" in s2
    return app


def test_discovered_by_prid_probe(make_app, monkeypatch):
    _run(make_app, monkeypatch, via_rss=False)


def test_discovered_by_rss(make_app, monkeypatch):
    _run(make_app, monkeypatch, via_rss=True)


def test_stage1_numbers_all_from_resolution():
    m1, _, ex, text = rbi_mpc.build_messages(date(2026, 8, 5), (FX / "pr_63287.html").read_bytes(), None, None,
                                             {"resolution": "u"}, first_seen=datetime(2026, 8, 5, 4, 31,
                                                                                      tzinfo=timezone.utc),
                                             mode="dry_run", stage2=False)
    assert m1.subject == "[RBI] MPC decision | Repo unchanged at 5.25 per cent; stance neutral"
    body = m1.body_text.split("\nRelease:")[0]
    for tok in re.findall(r"\d+(?:\.\d+)?(?:-\d+)?", body):
        assert tok in text, tok
