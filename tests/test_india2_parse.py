"""India Tier 2 (group india2): pure parse + scope tests on real captures (2026-11-26, tests/fixtures/india2)."""
from datetime import date, datetime, timezone

import pytest

from events_bot.core.config import load_sources
from events_bot.core.models import FetchResult
from events_bot.core.registry import resolve
from events_bot.core.settings import ROOT
from events_bot.digest import content
from events_bot.sources.india import autosales as A
from events_bot.sources.india import regulators as R

from .conftest import FIXTURES

FX = FIXTURES / "india2"
SOURCES = load_sources(ROOT / "config")


def b(name: str) -> bytes:
    return (FX / name).read_bytes()


def parsed(sid: str, url_name: str, fixture: str):
    cfg = SOURCES[sid]
    a = resolve(cfg.adapter)(cfg, None)
    res = FetchResult(url=cfg.urls[url_name], final_url=cfg.urls[url_name], status=200,
                      fetched_at=datetime.now(timezone.utc), content=b(fixture))
    return a.parse(res, url_name)


def by_title(items, needle):
    hits = [i for i in items if needle in i.title]
    assert hits, needle
    return hits[0]


def test_yaml_group_is_tier2_digest_only():
    mine = {k: v for k, v in SOURCES.items() if v.adapter.startswith(("india.regulators:", "india.autosales:"))}
    assert set(mine) == {"trai", "irdai", "pfrda", "cerc", "power_whatsnew", "dot_whatsnew", "meity_whatsnew",
                         "pngrb", "ppac", "cdsco", "cdsco_nsq", "npci", "siam", "fada"}
    for sid, cfg in mine.items():
        assert cfg.tier == 2 and cfg.country == "IN" and cfg.route is None, sid
        assert cfg.section == ("Industry data" if sid in ("siam", "fada") else "Regulators"), sid
        assert cfg.issuer and cfg.label
    assert not mine["npci"].enabled and all(c.enabled for s, c in mine.items() if s != "npci")


# (source, url name, fixture, kept title fragment, dropped title fragment or None)
CASES = [
    ("trai", "press", "trai_press_2026-11-26.html", "Telecom Subscription Data as on September 2026",
     "Mobile Network Quality in Leh"),
    ("trai", "press", "trai_press_2026-11-26.html", "Recommendations on the Framework for Satellite",
     "Extension of last date to receive comments"),
    ("irdai", "press", "irdai_press_2026-11-26.html", "Consultation paper on Distribution Reforms",
     "Order in the matter of Star Health"),
    ("irdai", "circulars", "irdai_circulars_2026-11-26.html", "Master Circular on Registration, Capital Structure",
     "Observance of National Cyber Security Awareness Month"),
    ("pfrda", "regulations", "pfrda_regulations_2026-11-26.html", "(Regulatory Sandbox) Regulations",
     "Procedure for Inquiry by Adjudicating Officer"),
    ("pfrda", "circulars", "pfrda_circulars_2026-11-26.html", "Standardised Framework for Classification",
     "KYC requirements for NRI"),
    ("cerc", "drafts", "cerc_draft_reg_2026-11-26.html", "(Deviation Settlement Mechanism and Related Matters) (Third",
     "(Appointment of Consultants) (Sixth Amendment)"),
    ("cerc", "regulations", "cerc_current_reg_2026-11-26.html",
     "(Connectivity and General Network Access to the inter-State Transmission System) (Fourth Amendment)",
     "(Conduct of Business) Regulations, 2023"),
    ("power_whatsnew", "whats_new", "power_whats_new_2026-11-26.json",
     "Directions, under Section 11 of the Electricity Act", "Selection for the post of Managing Director"),
    ("dot_whatsnew", "whats_new", "dot_whats_new_2026-11-26.json", "Telecom Manufacturing Zone Scheme",
     "HMoC's Address Note"),
    ("meity_whatsnew", "whats_new", "meity_whats_new_2026-11-26.json", None, "Digital India Internship Scheme"),
    ("pngrb", "whats_new", "pngrb_whats_new_2026-11-26.js", "introduction of LNG Trading",
     "Additional Comments on Draft"),
    ("ppac", "home", "ppac_home_2026-11-26.html", "Domestic Natural Gas Price for the period 1st November 2026",
     "RSP of Petrol in Delhi"),
    ("ppac", "home", "ppac_home_2026-11-26.html", "Gas Price Ceiling for the period 1st October 2026",
     "Indian Crude Basket (ICB) Ratio-November 2026"),
    ("cdsco", "notices", "cdsco_public_notices_2026-11-26.html", "drug products containing Enclomiphene",
     "Surrender of Cosmetic Registration Certificate"),
    ("cdsco_nsq", "nsq", "cdsco_nsq_2026-11-26.json", "NSQ) drug alert for OCT-2026", None),
    ("siam", "press", "siam_press_2026-11-26.html", "Auto Industry Performance of October-2026",
     "National EV Charging Day"),
    ("fada", "press", "fada_press_2026-11-26.html", "FADA Releases October 2026 Vehicle Retail Data",
     "Joint Training Centre at Agra"),
]


@pytest.mark.parametrize("sid,url_name,fixture,kept,dropped", CASES)
def test_scope_kept_and_dropped(sid, url_name, fixture, kept, dropped):
    items = parsed(sid, url_name, fixture)
    assert items
    assert len({i.ext_id for i in items}) >= len(items) - 3, "ext_ids must be (nearly) unique"
    for i in items:
        assert i.source_id == sid and i.meta["route_hint"] in ("india_eod", "drop") and i.meta["scope_reason"]
        assert i.source_published_at is None or i.source_published_at.tzinfo is not None
    if kept:
        assert by_title(items, kept).meta["route_hint"] == "india_eod"
    if dropped:
        assert by_title(items, dropped).meta["route_hint"] == "drop"


def test_trai_fields():
    it = by_title(parsed("trai", "press", "trai_press_2026-11-26.html"), "Port Charges) (Third Amendment)")
    assert it.ext_id == "pr:2026:157" and it.meta["number"] == "PR No. 157/2026"
    assert it.source_published_at.date() == date(2026, 11, 9) and it.meta["date_only"]
    assert it.url.startswith("https://www.trai.gov.in/notifications/press-release/")
    assert it.meta["pdf_url"] == "https://www.trai.gov.in/sites/default/files/2026-11/PR_No.157of2026.pdf.pdf"


def test_irdai_english_half_of_bilingual_title():
    it = by_title(parsed("irdai", "press", "irdai_press_2026-11-26.html"), "Star Health")
    assert it.title == "Order in the matter of Star Health and Allied Insurance Company Limited"
    assert it.ext_id == "doc:10054059" and it.url == "https://irdai.gov.in/document-detail?documentId=10054059"
    assert it.meta["pdf_url"].startswith("https://irdai.gov.in/documents/")


def test_cerc_rows_and_urls():
    final = R.cerc_parse(b("cerc_current_reg_2026-11-26.html"), True)
    top = final[0]
    assert top["sl"] == 209 and top["date"] == date(2026, 11, 19) and top["gazette"] == "686"
    assert top["url"] == "https://cercind.gov.in/regulations/209-Noti.pdf"
    assert top["title"].endswith("(Fourth Amendment) Regulations, 2026")
    drafts = R.cerc_parse(b("cerc_draft_reg_2026-11-26.html"), False)
    d120 = next(r for r in drafts if r["sl"] == 120)
    assert d120["url"] == "https://cercind.gov.in/2026/draft_reg/DR_IEGC-2ndAmnd.pdf"   # not the extension letter
    assert "1. Public Notice" not in d120["title"]


def test_nic_json_and_file_resolution():
    rows = R.nic_parse(b("power_whats_new_2026-11-26.json"))
    assert rows[0]["id"] == 26024 and rows[0]["file_ids"] == [26020]
    assert rows[0]["gmt"] == datetime(2026, 11, 23, 11, 7, 17, tzinfo=timezone.utc)
    pdf, fdate = R.nic_file_pdf(b("power_post_26020_2026-11-26.json"))
    assert pdf == "https://powermin.gov.in/static/uploads/2026/11/91cbea9d2d77a3f233f8cfce17c3329a.pdf"
    assert fdate == "23/11/2026"
    meity = R.nic_parse(b("meity_whats_new_2026-11-26.json"))
    assert meity[0]["pdf"].endswith("54bd52b8b79c582828a5dce48f3bcbb3.pdf")


def test_pngrb_commented_js_parses():
    rows = R.pngrb_parse(b("pngrb_whats_new_2026-11-26.js"))
    assert len(rows) > 1000
    assert not any(r["desc"] == "DPNG Consumer Grievance Redressal Dashboard" for r in rows)   # commented out
    first = rows[0]
    assert first["cat"] == "Public Notice" and first["date"] == date(2026, 11, 23)
    assert first["url"].startswith("https://www.pngrb.gov.in/eng-web/public_notice.html?item=")
    assert any(r["url"] == "https://www.pngrb.gov.in/pdf/ppp/Govt-Authorization/20261118_MHBPL.pdf" for r in rows)


def test_ppac_epoch_is_upload_time_and_whatsnew_duplicates_skipped():
    rows = R.ppac_parse(b("ppac_home_2026-11-26.html"))
    gas = [r for r in rows if r["title"].startswith("Domestic Natural Gas Price for the period 1st November")]
    assert len(gas) == 1 and gas[0]["section"] == "importantnews"
    assert gas[0]["when"] == datetime.fromtimestamp(1793446029, tz=timezone.utc)
    assert all(r["section"] in ("importantnews", "govtnotlaws") for r in rows)


def test_cdsco_notice_ids_and_nsq_month():
    rows = R.cdsco_parse(b("cdsco_public_notices_2026-11-26.html"))
    assert rows[0]["num"] == 14851 and rows[0]["date"] == date(2026, 11, 11)
    nsq = R.nsq_parse(b("cdsco_nsq_2026-11-26.json"))
    assert list(nsq) == ["OCT-2026"] and len(nsq["OCT-2026"]) == 324
    text = R.nsq_text("OCT-2026", nsq["OCT-2026"])
    assert text.startswith("Drug samples declared Not of Standard Quality (NSQ) for the reporting month OCT-2026: "
                           "324 samples")
    assert all(p in text for p in content._paragraphs(text))


def test_siam_and_fada_listing_fields():
    s = A.siam_parse(b("siam_press_2026-11-26.html"))
    oct_ = next(r for r in s if r["id"] == 624)
    assert oct_["date"] == date(2026, 11, 13) and oct_["title"] == "Auto Industry Performance of October-2026"
    f = A.fada_parse(b("fada_press_2026-11-26.html"))
    assert f[1]["key"] == "16aed49cca8e14" and f[1]["month"] == date(2026, 11, 1)
    assert f[1]["url"] == "https://www.fada.in/images/press-release/16aed49cca8e14FADA_Press_Release_Oct_26.pdf"
    assert " " not in f[0]["url"]           # spaces in FADA file names are percent-encoded
