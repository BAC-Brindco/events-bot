"""india1 group: DGFT tables and the Union Budget documents (real fixtures captured 26 Nov 2026)."""
from datetime import datetime, timezone

import httpx
import pytest

from events_bot.core.models import FetchResult, RawItem
from events_bot.digest import scope
from events_bot.sources.india import budget, dgft

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "india1"
LISTING = {k: FX / f"dgft_{k}_2026-11-26.html" for k in ("notification", "public-notice", "trade-notice")}


def _res(path, url="https://x/", headers=None) -> FetchResult:
    return FetchResult(url=url, final_url=url, status=200, fetched_at=datetime.now(timezone.utc),
                       headers=headers or {}, content=path.read_bytes())


# ---- DGFT: pure parse ----------------------------------------------------------------------------

def test_dgft_tables_parse_with_seconds_resolution_crt():
    sizes = {k: len(dgft.parse_listing(p.read_bytes())) for k, p in LISTING.items()}
    assert sizes == {"notification": 233, "public-notice": 178, "trade-notice": 112}
    rows = dgft.parse_listing(LISTING["notification"].read_bytes())
    top = rows[0]
    assert top["number"] == "51/2026-27" and top["crt_raw"] == "25/11/2026 12:54:38"
    assert top["crt"].isoformat() == "2026-11-25T12:54:38+05:30"
    assert top["title"].startswith("Amendment in import policy of raw sugar classified under Exim Code 170114")
    # spaces in attachment names are percent-encoded; the uuid path is kept
    assert top["pdf"] == ("https://content.dgft.gov.in/Website/dgftprod/fa1ec280-9b46-4147-8346-278db70085c1/"
                          "Notification%20No.%2051_0001.pdf")
    # rows are listed by Date, not upload: sorted newest upload first
    assert all(a["crt"] >= b["crt"] for a, b in zip(rows, rows[1:]))
    rodtep = next(r for r in rows if r["title"] == "Continuation of RoDTEP Scheme till December 31st 2026")
    assert rodtep["crt_raw"] == "30/09/2026 20:28:04"          # Phase 0 evidence row


@pytest.mark.parametrize("kind,title,keep", [
    ("notification", "Continuation of RoDTEP Scheme till December 31st 2026", True),
    ("notification", "Amendment in Export Policy of Wheat Flour and related products - reg.", True),
    ("notification", "Prohibition on Import of Goods Produced Using Forced Labour – Insertion of Para 2.20B "
                     "and Para 11.64 in the Foreign Trade Policy (FTP) 2023", True),
    ("notification", "Nomination of Non-official Members of the Board of Trade- reg.", False),
    ("notification", "Streamlining of Halal Certification Process for Meat and Meat Products- reg.", False),
    ("public-notice", "Modalities for export of Wheat - regarding.", True),
    ("public-notice", "Allocation of 8606 MTRV of raw cane sugar to USA under TRQ scheme for US fiscal year 2027",
     True),
    ("public-notice", "Fixation of four new Standard Input-Output Norms (SIONs) at SION Nos. A-3715, A-3716, "
                      "A-3717 & A-3718", False),
    ("public-notice", "Extension of timeline for surrender of unutilised TRQ quantity allocated for import of "
                      "10 LMT of Raw Sugar – reg.", False),
    ("public-notice", "Amendments in Para 2.88 of Handbook of Procedures- reg.", False),
    ("trade-notice", "Review and re-allocation of allocated Export Quota of Wheat under HS Codes 10011900 and "
                     "10019910- reg.", True),
    ("trade-notice", "Introduction of new features in the Bank Guarantee (BG) Repository Module on DGFT Portal "
                     "- regarding", False),
    ("trade-notice", "Advisory against M/s Julfikar Fashions Limited, Bangladesh as per para 8.07 (d) of Foreign "
                     "Trade Policy- 2023– reg.", False),
])
def test_dgft_scope(kind, title, keep):
    hint, reason = dgft.classify(kind, title)
    assert hint == ("india_eod" if keep else "drop"), reason
    assert hint != "realtime"                                   # F-29


def test_dgft_broken_page_fails_loudly(make_app):
    app = make_app()
    stub = FetchResult(url="u", final_url="u", status=200, fetched_at=datetime.now(timezone.utc),
                       content=b"<html><body>maintenance</body></html>")
    with pytest.raises(ValueError):
        app.adapter("dgft").parse(stub, "notification")


# ---- DGFT: poll -> pipeline -> route --------------------------------------------------------------

def _dgft_handler(pdfs: dict | None = None):
    def handler(req: httpx.Request):
        if req.url.host == "www.dgft.gov.in":
            return httpx.Response(200, content=LISTING[req.url.params["opt"]].read_bytes(),
                                  headers={"content-type": "text/html;charset=utf-8"})
        name = req.url.path.rsplit("/", 1)[-1]
        if pdfs and name in pdfs:
            return httpx.Response(200, content=pdfs[name].read_bytes(),
                                  headers={"content-type": "application/octet-stream"})
        return httpx.Response(404)
    return handler


def test_dgft_poll_routes_kept_and_dropped(make_app):
    app = make_app("dry_run")
    mock_fetcher(app, _dgft_handler())
    # one existing row so this poll is not the silent baseline
    seed = RawItem(source_id="dgft", ext_id="seed", url="https://www.dgft.gov.in/CP/?opt=notification", title="seed")
    app.db.insert_item(seed, url_norm=seed.url, title_norm="seed", tier=1, first_seen_at=datetime.now(timezone.utc))
    st = app.poll_source("dgft")
    assert st is not None and st.seen == 180 and st.realtime == 0
    kept = app.db.one("select status, route from items where source_id = 'dgft' and ext_id = %s",
                      ("notification:20261110213254",))           # 49/2026-27 Li-ion black mass export policy
    assert kept == {"status": "queued", "route": "india_eod"}
    dropped = app.db.one("select status from items where source_id = 'dgft' and ext_id = %s",
                         ("trade-notice:20261021130346",))       # advisory against a Bangladeshi firm
    assert dropped["status"] == "filtered"
    f = app.db.one("select rule, reason from filtered_items where title like 'Advisory against M/s Julfikar%%'")
    assert f["rule"] == "out_of_scope" and "DGFT trade notice" in f["reason"]


def test_dgft_digest_body_text_and_scanned(make_app):
    app = make_app()
    mock_fetcher(app, _dgft_handler({
        "Notification%20No.%2051_0001.pdf": FX / "dgft_notification_51_scanned.pdf",
        "Notification No. 51_0001.pdf": FX / "dgft_notification_51_scanned.pdf",
        "Trade%20Notice-%20MAS%20Amendment%2017.11.2026%20digitally%20signed.pdf": FX / "dgft_trade_notice_35.pdf",
        "Trade Notice- MAS Amendment 17.11.2026 digitally signed.pdf": FX / "dgft_trade_notice_35.pdf"}))
    a = app.adapter("dgft")
    items = {i.meta["number"]: i for i in a.parse(_res(LISTING["trade-notice"]), "trade-notice")}
    b = a.digest_body(items["35/2026-27"])
    assert len(b.text) > 1000 and b.paragraphs and all(p in b.text for p in b.paragraphs) and not b.notes
    n51 = a.parse(_res(LISTING["notification"]), "notification")[0]
    s = a.digest_body(n51)
    assert s.text == "" and s.notes and "scan" in s.notes[0]


# ---- Union Budget ---------------------------------------------------------------------------------

SPEECH, GLANCE = FX / "Budget_Speech_2026.pdf", FX / "budget_at_a_glance_2026.pdf"
LM = {"speech": "Sun, 01 Feb 2026 00:41:42 GMT", "glance": "Sat, 31 Jan 2026 21:00:38 GMT"}


def test_budget_cover_and_items(make_app):
    assert budget.cover(SPEECH.read_bytes()) == {"fy": "2026-2027", "interim": False}
    assert budget.cover(GLANCE.read_bytes()) == {"fy": "2026-2027", "interim": False}
    app = make_app()
    a = app.adapter("india_budget")
    [it] = a.parse(_res(SPEECH, "https://www.indiabudget.gov.in/doc/Budget_Speech.pdf",
                        {"last-modified": LM["speech"]}), "speech")
    assert it.ext_id == "speech:2026-2027" and it.title == "Union Budget 2026-2027: Budget Speech"
    assert it.source_published_at.isoformat() == "2026-02-01T00:41:42+00:00"
    assert it.meta["route_hint"] == "india_eod"
    assert scope.decide(app.sources["india_budget"], it).route == "india_eod"


def test_budget_window():
    from datetime import date
    assert budget.in_window(date(2027, 1, 15), None) and budget.in_window(date(2027, 2, 1), None)
    assert not budget.in_window(date(2026, 11, 26), None) and not budget.in_window(date(2027, 2, 16), None)
    assert budget.in_window(date(2029, 7, 23), ["01-15..02-15", "07-01..07-31"])


def test_budget_outside_window_makes_no_request(make_app, monkeypatch):
    app = make_app()
    calls = []
    mock_fetcher(app, lambda req: calls.append(req) or httpx.Response(500))
    monkeypatch.setattr(budget, "utcnow", lambda: datetime(2026, 11, 26, 6, 0, tzinfo=timezone.utc))
    assert app.adapter("india_budget").poll() == [] and calls == []


def test_budget_day_poll_queues_both_documents_then_304(make_app, monkeypatch):
    app = make_app("dry_run")
    files = {"Budget_Speech.pdf": (SPEECH, LM["speech"]), "budget_at_a_glance.pdf": (GLANCE, LM["glance"])}

    def handler(req: httpx.Request):
        path, lm = files[req.url.path.rsplit("/", 1)[-1]]
        if req.headers.get("if-modified-since") == lm:
            return httpx.Response(304)
        return httpx.Response(200, content=path.read_bytes(),
                              headers={"content-type": "application/pdf", "last-modified": lm})

    mock_fetcher(app, handler)
    monkeypatch.setattr(budget, "utcnow", lambda: datetime(2027, 2, 1, 6, 30, tzinfo=timezone.utc))
    seed = RawItem(source_id="india_budget", ext_id="speech:2025-2026", url="u", title="Union Budget 2025-2026")
    app.db.insert_item(seed, url_norm="u", title_norm="seed", tier=1, first_seen_at=datetime.now(timezone.utc))
    st = app.poll_source("india_budget")
    assert st.new == 2 and st.queued == 2 and st.realtime == 0
    rows = app.db.q("select ext_id, route from items where source_id = 'india_budget' and status = 'queued' "
                    "order by ext_id")
    assert [(r["ext_id"], r["route"]) for r in rows] == [("glance:2026-2027", "india_eod"),
                                                        ("speech:2026-2027", "india_eod")]
    assert app.adapter("india_budget").poll() == []            # unchanged files: 304, nothing new


def test_budget_digest_body(make_app):
    app = make_app()
    mock_fetcher(app, lambda req: httpx.Response(200, content=SPEECH.read_bytes()))
    a = app.adapter("india_budget")
    it = RawItem(source_id="india_budget", ext_id="speech:2026-2027",
                 url="https://www.indiabudget.gov.in/doc/Budget_Speech.pdf", title="t")
    b = a.digest_body(it)
    assert "Fiscal Consolidation" in b.text and len(b.paragraphs) > 50
    assert all(p in b.text for p in b.paragraphs)


# ---- PIB / RBI coverage of GST Council, CGA and the borrowing calendar (shared scope.py) ------------

def _pib(name):
    from events_bot.sources.india.pib import parse_release
    return parse_release((FX / name).read_bytes())


def test_gst_council_57th_is_kept_via_pib(make_app):
    app = make_app()
    r = _pib("pib_2320934_gst57.html")
    assert r["title"] == "Recommendations of the 57th Meeting of the GST Council"
    assert r["posted"].isoformat() == "2026-10-08T19:03:00+05:30"
    it = RawItem(source_id="pib", ext_id="prid:2320934", url="u", title=r["title"],
                 meta={"ministry": r["ministry"]})
    assert scope.decide(app.sources["pib"], it).route == "india_eod"


def test_cga_monthly_accounts_title_changed(make_app):
    """Sep-2026 accounts were retitled ("... accounts of Government of India upto September 2026"). Today it
    survives only through the Ministry-of-Finance + crore fallback after enrichment; on its title alone the
    scope rule drops it and the keyword filter sends it to LLM triage. See the report's scope.py change."""
    app = make_app()
    r = _pib("pib_2330455_cga_sep2026.html")
    assert r["title"] == "Monthly review of accounts of Government of India upto September 2026 (FY 2026-27)"
    assert r["posted"].isoformat() == "2026-10-30T17:02:00+05:30"
    it = RawItem(source_id="pib", ext_id="prid:2330455", url="u", title=r["title"],
                 summary=" ".join(r["paragraphs"][:3]), meta={"ministry": r["ministry"]})
    assert scope.decide(app.sources["pib"], it).route == "india_eod"


@pytest.mark.parametrize("sid,title", [
    ("pib", "Monthly review of accounts of Government of India upto September 2026 (FY 2026-27)"),
    ("pib", "Government’s Borrowing Plan for the second half of FY 2026-27"),
    ("pib", "Issuance Calendar for Marketable Dated Securities for October 2026-March 2027"),
    ("rbi_pr", "Issuance Calendar for Marketable Dated Securities for October 2026 - March 2027"),
    ("rbi_pr", "Calendar for Auction of Government of India Treasury Bills"),
])
def test_borrowing_calendar_and_cga_titles_kept_on_title(make_app, sid, title):
    app = make_app()
    it = RawItem(source_id=sid, ext_id="x", url="u", title=title, meta={"ministry": "Ministry of Finance"})
    assert scope.decide(app.sources[sid], it).route == "india_eod"


def test_rbi_calendar_titles_from_fixtures():
    from events_bot.extract.base import html_text
    t1 = html_text((FX / "rbi_63668_issuance_calendar.html").read_bytes(), ".tablebg")
    t2 = html_text((FX / "rbi_63669_tbill_calendar.html").read_bytes(), ".tablebg")
    assert "Issuance Calendar for Marketable Dated Securities for October 2026 - March 2027" in t1
    assert "Calendar for Auction of Government of India Treasury Bills" in t2
