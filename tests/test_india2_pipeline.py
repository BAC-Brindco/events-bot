"""India Tier 2 (group india2): poll -> pipeline routing (india_eod vs dropped) and digest bodies, on real captures."""
from datetime import datetime, timezone

import httpx
import pytest

from events_bot.core.models import RawItem

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "india2"

# url -> fixture, keyed by a fragment of the request URL (path + query)
ROUTES = {
    "/notifications/press-release": "trai_press_2026-11-26.html",
    "irdai.gov.in/press-releases": "irdai_press_2026-11-26.html",
    "irdai.gov.in/circulars": "irdai_circulars_2026-11-26.html",
    "/regulatory-framework/regulations": "pfrda_regulations_2026-11-26.html",
    "/circulars/active-circulars": "pfrda_circulars_2026-11-26.html",
    "Draft_reg.html": "cerc_draft_reg_2026-11-26.html",
    "Current_reg.html": "cerc_current_reg_2026-11-26.html",
    "powermin.gov.in/cms/wp-json/post-page/whats_new": "power_whats_new_2026-11-26.json",
    "powermin.gov.in/cms/wp-json/post-page/post?id=26020": "power_post_26020_2026-11-26.json",
    "dot.gov.in/cms/wp-json/post-page/whats_new": "dot_whats_new_2026-11-26.json",
    "meity.gov.in/cms/wp-json/post-page/whats_new": "meity_whats_new_2026-11-26.json",
    "whats-new-json.js": "pngrb_whats_new_2026-11-26.js",
    "ppac.gov.in/": "ppac_home_2026-11-26.html",
    "Public-Notices": "cdsco_public_notices_2026-11-26.html",
    "publicNsqDrugTable": "cdsco_nsq_2026-11-26.json",
    "siam.in/news-&-updates/press-releases/auto-industry-performance-of-october-2026/624":
        "siam_detail_624_2026-11-26.html",
    "siam.in/news-&-updates/press-releases": "siam_press_2026-11-26.html",
    "press-release-list.php": "fada_press_2026-11-26.html",
    "16aed49cca8e14FADA_Press_Release_Oct_26.pdf": "fada_pr_oct26_2026-11-26.pdf",
    "PR_No.157of2026.pdf.pdf": "trai_pr_157_2026-11-26.pdf",
    "download_file_division.jsp?num_id=MTQ4MDQ=": "cdsco_download_14804_2026-11-26.html",
    "UploadPublic_NoticesFiles/Testing%20of%20Raw%20materials": "cdsco_notice_14804_2026-11-26.pdf",
}


def handler(req: httpx.Request) -> httpx.Response:
    u = str(req.url).replace("%26", "&")
    for frag, name in sorted(ROUTES.items(), key=lambda kv: -len(kv[0])):
        if frag in u:
            return httpx.Response(200, content=(FX / name).read_bytes())
    return httpx.Response(404, content=b"not found")


def _seed(app, sid):
    """A stored item makes the next poll a live one (the first poll of a source is a silent baseline)."""
    app.db.insert_item(RawItem(source_id=sid, ext_id="seed", url=f"https://example.invalid/{sid}", title="seed"),
                       url_norm=f"https://example.invalid/{sid}", title_norm="seed", tier=2,
                       first_seen_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def _row(app, sid, needle):
    rows = app.db.q("select title, status, route, meta from items where source_id = %s and position(%s in title) > 0",
                    (sid, needle))
    assert rows, (sid, needle)
    return rows[0]


def test_first_poll_is_silent_baseline(make_app):
    app = make_app("dry_run")
    mock_fetcher(app, handler)
    st = app.poll_source("trai")
    assert st is not None and st.new == 30 and st.baseline == 30 and st.queued == 0


CASES = [
    ("trai", "Telecom Subscription Data as on September 2026", "Mobile Network Quality in Leh"),
    ("irdai", "Master Circular on Registration, Capital Structure", "Order in the matter of Star Health"),
    ("pfrda", "(Regulatory Sandbox) Regulations", "KYC requirements for NRI"),
    ("cerc", "(Connectivity and General Network Access to the inter-State Transmission System) (Fourth Amendment)",
     "(Appointment of Consultants) (Sixth Amendment)"),
    ("power_whatsnew", "Rights of Consumer) Amendment Rules", "Selection for the post of Managing Director"),
    ("dot_whatsnew", "Telecom Manufacturing Zone Scheme", "HMoC's Address Note"),
    ("meity_whatsnew", None, "Digital India Internship Scheme"),
    ("pngrb", "introduction of LNG Trading", "Additional Comments on Draft"),
    ("ppac", "Domestic Natural Gas Price for the period 1st November 2026", "RSP of Petrol in Delhi"),
    ("cdsco", "drug products containing Enclomiphene", "Surrender of Cosmetic Registration Certificate"),
    ("cdsco_nsq", "NSQ) drug alert for OCT-2026", None),
    ("siam", "Auto Industry Performance of October-2026", "National EV Charging Day"),
    ("fada", "FADA Releases October 2026 Vehicle Retail Data", "Joint Training Centre at Agra"),
]


@pytest.mark.parametrize("sid,kept,dropped", CASES)
def test_poll_routes_kept_to_eod_and_drops_noise(make_app, sid, kept, dropped):
    app = make_app("dry_run")
    mock_fetcher(app, handler)
    _seed(app, sid)
    st = app.poll_source(sid)
    assert st is not None and st.new > 0 and st.realtime == 0        # India items never alert in real time
    if kept:
        r = _row(app, sid, kept)
        assert (r["status"], r["route"]) == ("queued", "india_eod"), r
    if dropped:
        r = _row(app, sid, dropped)
        assert r["status"] == "filtered" and r["route"] is None, r
        f = app.db.one("select rule, reason from filtered_items where source_id = %s and position(%s in title) > 0",
                       (sid, dropped))
        assert f["rule"] == "out_of_scope" and f["reason"]


def test_power_new_item_resolves_pdf_url(make_app):
    app = make_app("dry_run")
    mock_fetcher(app, handler)
    _seed(app, "power_whatsnew")
    app.poll_source("power_whatsnew")
    r = app.db.one("select url, meta from items where source_id = 'power_whatsnew' and ext_id = 'post:26024'")
    assert r["url"] == "https://powermin.gov.in/static/uploads/2026/11/91cbea9d2d77a3f233f8cfce17c3329a.pdf"
    assert r["meta"]["file_date"] == "23/11/2026"


def test_digest_bodies(make_app):
    app = make_app("dry_run")
    mock_fetcher(app, handler)
    trai = app.adapter("trai").digest_body(RawItem(
        source_id="trai", ext_id="pr:2026:157", title="TRAI releases ... (Port Charges) (Third Amendment) Regulations",
        url="https://www.trai.gov.in/notifications/press-release/trai-releases-telecommunication-interconnection-port"
            "-charges-third",
        meta={"pdf_url": "https://www.trai.gov.in/sites/default/files/2026-11/PR_No.157of2026.pdf.pdf"}))
    assert trai.pdf_url.endswith("PR_No.157of2026.pdf.pdf")
    assert any("take effect from 1st January 2027" in p for p in trai.paragraphs)

    siam = app.adapter("siam").digest_body(RawItem(
        source_id="siam", ext_id="pr:624", title="Auto Industry Performance of October-2026",
        url="https://www.siam.in/news-&-updates/press-releases/auto-industry-performance-of-october-2026/624"))
    assert "Passenger Vehicles 2 sales were 5,19,060 units in October 2026." in siam.text

    fada = app.adapter("fada").digest_body(RawItem(
        source_id="fada", ext_id="pdf:16aed49cca8e14", title="FADA Releases October 2026 Vehicle Retail Data",
        url="https://www.fada.in/images/press-release/16aed49cca8e14FADA_Press_Release_Oct_26.pdf"))
    assert "Overall Retail: 31,72,466 units" in fada.text

    nsq = app.adapter("cdsco_nsq").digest_body(RawItem(
        source_id="cdsco_nsq", ext_id="nsq:OCT-2026", title="CDSCO NSQ drug alert for OCT-2026",
        url="https://cdsco.gov.in/opencms/opencms/en/Notifications/nsq-drugs/?month=OCT-2026",
        meta={"month": "OCT-2026", "listing_url": "https://cdscoonline.gov.in/CDSCO/publicNsqDrugTable"}))
    assert "324 samples" in nsq.text
    gone = app.adapter("cdsco_nsq").digest_body(RawItem(
        source_id="cdsco_nsq", ext_id="nsq:SEP-2026", title="x", url="https://example.invalid/x",
        meta={"month": "SEP-2026"}))
    assert gone.notes and not gone.paragraphs

    cdsco = app.adapter("cdsco").digest_body(RawItem(
        source_id="cdsco", ext_id="notice:14804", title="Strict compliance with the Drugs Rules, 1945, ...",
        url="https://cdsco.gov.in/opencms/opencms/system/modules/CDSCO.WEB/elements/download_file_division.jsp"
            "?num_id=MTQ4MDQ="))
    assert cdsco.pdf_url == ("https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadPublic_NoticesFiles/"
                             "Testing%20of%20Raw%20materials%20and%20finished%20formulation%20reg..pdf")
    assert cdsco.notes and "scanned" in cdsco.notes[0]          # this notice is an image-only PDF

    for body in (trai, siam, fada, nsq):
        assert body.paragraphs and all(p in body.text for p in body.paragraphs)


def test_npci_disabled_and_refuses(make_app):
    app = make_app("dry_run")
    assert app.sources["npci"].enabled is False
    with pytest.raises(RuntimeError):
        app.adapter("npci").poll()
