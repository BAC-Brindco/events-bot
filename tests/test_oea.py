"""OEA: WPI and core-industries extraction, homepage discovery, data-print alert."""
from datetime import datetime, timezone

import httpx
import pytest

from events_bot.extract import oea
from events_bot.extract.validator import validate_all
from events_bot.sources.india import oea as src

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "oea"


@pytest.mark.parametrize("name,cur,prior,fuel", [("press_release_202608", "9.92", "9.78", "22.93"),
                                                 ("press_release_202606", "9.87", "9.68", "27.41")])
def test_wpi(name, cur, prior, fuel):
    t, ex = oea.parse_wpi((FX / f"{name}.pdf").read_bytes())
    validate_all(ex, t)
    v = {e.field: e.value_text for e in ex if e.ok}
    assert len(v) == 11 == len(ex)
    assert v["oea.wpi.headline.current"] == cur and v["oea.wpi.headline.prior"] == prior
    assert v["oea.wpi.fuel.current"] == fuel
    assert all(h in t["text"] for h in oea.highlights(t)) and len(oea.highlights(t)) == 4


def test_ici_and_scanned_archive_yields_nothing():
    t, ex = oea.parse_ici((FX / "Press_Release_ICI_20260921.pdf").read_bytes())
    validate_all(ex, t)
    v = {e.field: e.value_text for e in ex if e.ok}
    assert v == {"oea.ici.growth.current": "4.8", "oea.ici.growth.prior": "5.0",
                 "oea.ici.cumulative.current": "4.3", "oea.ici.cumulative.year_ago": "2.4"}
    t2, ex2 = oea.parse_ici((FX / "IPR_2026_07.pdf").read_bytes())   # image-only PDF: no text layer
    assert ex2 == []


def test_home_discovery():
    rows = src.parse_home((FX / "home_2026-10-07.html").read_bytes())
    kinds = {r["kind"] for r in rows}
    assert {"wpi", "ici"} <= kinds
    assert any(r["title"] == "Latest WPI & PPI Press Release for the Month of August,2026" for r in rows)


def test_alert_and_missing_fields_flagged(make_app):
    app = make_app("dry_run")
    pdfs = {"press_release_202608.pdf": FX / "press_release_202608.pdf",
            "Press_Release_ICI_20260921.pdf": FX / "IPR_2026_07.pdf"}       # scanned copy stands in for ICI

    def handler(req):
        name = req.url.path.rsplit("/", 1)[-1]
        if name in pdfs:
            return httpx.Response(200, content=pdfs[name].read_bytes())
        return httpx.Response(200, content=(FX / "home_2026-10-07.html").read_bytes())

    mock_fetcher(app, handler)
    a = app.adapter("oea")
    items = {i.meta["kind"]: i for i in a.poll() if i.meta["kind"]}
    now = datetime(2026, 9, 14, 6, 35, tzinfo=timezone.utc)
    wpi = a.render_stage1(items["wpi"], first_seen=now, mode="dry_run")
    assert wpi.subject == "[OEA] WPI inflation | WPI inflation 9.92% in August 2026; prior 9.78% in July 2026"
    ici = a.render_stage1(items["ici"], first_seen=now, mode="dry_run")
    assert "EXTRACTION FAILED" in ici.subject and "not found in source" in ici.body_text   # never silent
