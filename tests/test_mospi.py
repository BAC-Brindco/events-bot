"""MoSPI: CPI (both table layouts), IIP, GDP extraction; release-list parsing; data-print alert."""
import json
from datetime import datetime, timezone

import httpx
import pytest

from events_bot.core.models import FetchResult
from events_bot.extract import mospi
from events_bot.extract.validator import validate_all

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "mospi"


def _x(name, fn):
    texts, ex = fn((FX / f"{name}.pdf").read_bytes())
    validate_all(ex, texts)
    assert [e for e in ex if not e.ok] == []
    return {e.field: e.value_text for e in ex}, texts


@pytest.mark.parametrize("name,cur,prior", [("cpi_2026-08", "4.82", "4.45"), ("cpi_2026-07", "4.45", "4.38")])
def test_cpi_both_table_layouts(name, cur, prior):
    v, _ = _x(name, mospi.parse_cpi)
    assert v["mospi.cpi.combined.current"] == cur and v["mospi.cpi.combined.prior"] == prior
    assert len(v) == 12


def test_cpi_cross_month_consistency():
    aug, _ = _x("cpi_2026-08", mospi.parse_cpi)
    jul, _ = _x("cpi_2026-07", mospi.parse_cpi)
    for area in ("rural", "urban", "combined"):           # July provisional -> July final, unchanged this time
        assert aug[f"mospi.cpi.{area}.prior"] == jul[f"mospi.cpi.{area}.current"]


def test_iip_with_negative_sector():
    v, t = _x("iip_2026-08", mospi.parse_iip)
    assert v["mospi.iip.growth.current"] == "8.0" and v["mospi.iip.growth.prior"] == "6.7"
    assert v["mospi.iip.mining.current"] == "(-) 5.6"
    assert mospi.highlights("iip", t)[0].startswith("In August 2026, Index of Industrial Production")


def test_gdp_key_highlights():
    v, t = _x("gdp_2026-q1", mospi.parse_gdp)
    assert v["mospi.gdp.real.current"] == "7.8" and v["mospi.gdp.real.year_ago"] == "6.9"
    assert v["mospi.gdp.nominal.current"] == "10.3" and v["mospi.gfcf.real.current"] == "11.9"
    hl = mospi.highlights("gdp", t)
    assert hl[0].startswith("Real GDP has been estimated to grow by 7.8%") and all(h in t["text"] for h in hl)


def test_kind_of_titles():
    assert mospi.kind_of("Press release of CPI for the month of August 2026") == "cpi"
    assert mospi.kind_of("Press Release on Quick Estimates of all India Index of Industrial Production (IIP)") == "iip"
    assert mospi.kind_of("Press Note On Quarterly estimates of Gross Domestic Product for the first quarter") == "gdp"
    assert mospi.kind_of("Press Note on Periodic Labour Force Survey (PLFS) Monthly Bulletin") is None


def test_release_list_and_data_print_alert(make_app):
    app = make_app("dry_run")
    payload = (FX / "latest_2026-10-07.json").read_bytes()
    pdf = (FX / "cpi_2026-08.pdf").read_bytes()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, content=payload, headers={"content-type": "application/json"})
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})

    mock_fetcher(app, handler)
    a = app.adapter("mospi")
    items = a.poll()
    cpi = next(i for i in items if i.ext_id == "id:3606")
    assert cpi.meta["kind"] == "cpi" and cpi.url.endswith(".pdf") and cpi.meta["date_only"]
    msg = a.render_stage1(cpi, first_seen=datetime(2026, 9, 14, 10, 32, tzinfo=timezone.utc), mode="dry_run")
    assert msg.subject.startswith("[MoSPI] CPI inflation | CPI inflation 4.82%")
    assert "Extraction check" not in msg.body_html and "not found in source" not in msg.body_text
    plfs = next(i for i in items if "PLFS" in i.title)
    assert a.render_stage1(plfs, first_seen=datetime.now(timezone.utc), mode="dry_run") is None
