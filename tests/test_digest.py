"""Daily Macro Digest (F-29): scope rules, verbatim write-ups from real releases, size budget, send-once."""
from datetime import timedelta
from html import escape

import httpx
import pytest

from events_bot.core.models import RawItem
from events_bot.core.timeutil import IST, utcnow
from events_bot.digest import content, run as dg, scope, select
from events_bot.digest.render import shown

from .conftest import FIXTURES, mock_fetcher
from .test_sends_and_health import FakeChannel

FX = FIXTURES / "digest"

# (fixture, how its text is derived, title the poll saw)
BODIES = [
    ("rbi_pr_63749.html", "rbi", "RBI issues Directions on 'Credit Valuation Adjustment (CVA) Framework’"),
    ("rbi_notif_13735.html", "rbi", "Reserve Bank of India (All India Financial Institutions (AIFIs) – Prudential Norms "
                                    "on Capital Adequacy) Fifth Amendment Directions, 2026"),
    ("sebi_105081.pdf", "pdf", "Introduction of Credit Risk-o-Meter as an additional disclosure mechanism for debt securities"),
    ("sebi_board_104725.pdf", "pdf", "Key decisions taken in the SEBI Board Meeting dated 24th September, 2026"),
    ("pib_2320934.html", "pib", "Recommendations of the 57th Meeting of the GST Council"),
    ("mospi_plfs.pdf", "pdf", "Periodic Labour Force Survey (PLFS) Monthly Bulletin August 2026"),
]


def _text(name: str, how: str) -> str:
    b = (FX / name).read_bytes()
    return {"rbi": lambda: content.html_body(b, ".tablebg"), "pib": lambda: content.pib_body(b),
            "pdf": lambda: content.pdf_body(b)}[how]()


# ---- verbatim contract ----------------------------------------------------------------------

@pytest.mark.parametrize("name,how,title", BODIES)
def test_every_quoted_string_is_an_exact_substring_of_the_source_text(name, how, title):
    text = _text(name, how)
    paras = content._paragraphs(text)
    assert all(p in text for p in paras)
    ops = select.operative(paras, n=10, title=title)
    assert len(ops) >= 3
    for p in ops:
        assert p in text and shown(p) in text            # the cut paragraph is a verbatim prefix
    for f in select.figures(text, ops):
        assert f.ex.ok and text[f.ex.char_start:f.ex.char_end] == f.ex.value_text
        assert f.context in text and f.ex.value_text in f.context
    for w in select.applies_to(text):
        assert w in text
    eff = select.effective(text)
    assert eff is None or eff in text
    for r in select.reference(text):
        assert r in text


def test_rbi_directions_write_up_has_the_substance():
    text = _text("rbi_pr_63749.html", "rbi")
    ops = select.operative(content._paragraphs(text), title=BODIES[0][2])
    assert any("capital charge for CVA risk" in p for p in ops)
    assert select.effective(text) == "These instructions will come into effect from April 1, 2027."
    assert select.reference(text) == ["Press Release: 2026-2027/1271"]
    assert not any(p.startswith("RBI issues Directions on") for p in ops)     # the title is not repeated


def test_sebi_circular_addressees_and_commencement():
    text = _text("sebi_105081.pdf", "pdf")
    who = select.applies_to(text)
    assert who[0].startswith("Issuers of debt securities; Entities operating as Online Bond Platform Providers")
    assert select.effective(text) == ("The provisions of circular shall come into force after 45 days from the "
                                      "date of issuance.")


def test_gst_council_figures_come_with_their_sentence():
    text = _text("pib_2320934.html", "pib")
    ops = select.operative(content._paragraphs(text), n=10, title=BODIES[4][2])
    figs = {f.ex.value_text: f.context for f in select.figures(text, ops)}
    assert "₹40 crore" in figs and "pre-deposit" in figs["₹40 crore"]
    assert not any(v.endswith(",") for v in figs)


# ---- scope ------------------------------------------------------------------------------------

def _it(source, title, **meta):
    return RawItem(source_id=source, ext_id="x:1", url="https://x/1", title=title, meta=meta)


@pytest.mark.parametrize("source,title,meta,route", [
    ("rbi_pr", "RBI issues Directions on 'Credit Valuation Adjustment (CVA) Framework’", {}, "india_eod"),
    ("rbi_pr", "Government Stock - Full Auction Results", {}, None),
    ("rbi_pr", "10 NBFCs surrender their Certificates of Registration to the RBI", {}, None),
    ("rbi_pr", "Governor’s Statement, October 7, 2026", {}, None),
    ("rbi_pr", "Foreign Exchange Turnover Data: September 21, 2026 – September 25, 2026", {}, None),
    ("rbi_pr", "RBI releases the results of Forward Looking Surveys", {}, "india_eod"),
    ("rbi_notif", "Reserve Bank of India (Payments Banks – Prudential Norms on Capital Adequacy) Third Amendment "
                  "Directions, 2026", {}, None),
    ("rbi_notif", "Liquidity Adjustment Facility - Change in rates", {}, "india_eod"),
    ("pib", "Recommendations of the 57th Meeting of the GST Council", {"ministry": "Ministry of Finance"}, "india_eod"),
    ("pib", "CCI approves acquisition of certain shareholding in Prestige Hospitality Ventures Limited", {}, None),
    ("pib", "Jal Shakti Minister Shri C.R. Paatil chairs 20th Meeting of Empowered Task Force on Ganga Rejuvenation",
     {"ministry": "Ministry of Jal Shakti"}, None),
    ("pib", "Cabinet approves Minimum Support Prices for Rabi crops", {"ministry": "Cabinet"}, "india_eod"),
    ("sebi_circ", "Introduction of Credit Risk-o-Meter as an additional disclosure mechanism", {}, "india_eod"),
    ("sebi_pr", "SEBI opens a local office in Chandigarh", {}, None),
    ("sebi_pr", "Key decisions taken in the SEBI Board Meeting dated 24th September, 2026",
     {"priority_tags": ["board_outcome"]}, "india_eod"),
    ("bse_notices", "Additions to the BSE Indices", {}, None),
    ("nse_surv", "NSE GSM lists updated", {}, None),
    ("cbdt", "Notification No. 134/2026", {}, None),
    ("cbic", "45/2026: Clarification regarding scope of the term Power Bank", {"priority_tags": ["cbic:customs"]}, None),
    ("cbic", "52/2026-Customs: Seeks to amend notification", {"priority_tags": ["cbic:customs", "cbic:rate"]}, "india_eod"),
    ("imd", "Press release on Long Range Forecast for the 2027 Southwest Monsoon", {}, "india_eod"),
    ("imd", "PRESS RELEASE DATE: 07TH OCTOBER 2026", {}, None),
    ("mospi", "Press Release of CPI for September 2026", {"kind": "cpi"}, "realtime"),
    ("mospi", "Periodic Labour Force Survey Monthly Bulletin", {"kind": None}, "india_eod"),
    ("nifty_indices", "Replacement in Nifty indices", {"rows": [{"index": "Nifty 50", "action": "excluded",
                                                                 "company": "Wipro Ltd.", "symbol": "WIPRO"}]}, "india_eod"),
    ("nifty_indices", "Changes in Nifty SME Emerge index", {"rows": [{"index": "Nifty SME Emerge"}]}, None),
])
def test_scope(make_app, source, title, meta, route):
    app = make_app()
    assert scope.decide(app.sources[source], _it(source, title, **meta)).route == route


# ---- end to end ---------------------------------------------------------------------------------

RBI_URL = "https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63749"
SEBI_URL = ("https://www.sebi.gov.in/legal/circulars/oct-2026/introduction-of-credit-risk-o-meter-as-an-additional-"
            "disclosure-mechanism-for-debt-securities_105081.html")
SEBI_PDF = "https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1791436170839.pdf"
PIB_URL = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2320934&reg=3&lang=1"
ROUTES = {RBI_URL: ("rbi_pr_63749.html", "text/html"), SEBI_URL: ("sebi_105081.html", "text/html"),
          SEBI_PDF: ("sebi_105081.pdf", "application/pdf"), PIB_URL: ("pib_2320934.html", "text/html")}


def _handler(req: httpx.Request) -> httpx.Response:
    name, ct = ROUTES.get(str(req.url), (None, None))
    if name is None:
        return httpx.Response(404)
    return httpx.Response(200, content=(FX / name).read_bytes(), headers={"content-type": ct})


def _queue(app, source, ext, url, title, **meta):
    it = RawItem(source_id=source, ext_id=ext, url=url, title=title, source_published_at=utcnow() - timedelta(hours=2),
                 meta=meta)
    iid = app.db.insert_item(it, url_norm=url, title_norm=title.lower(), tier=1, first_seen_at=utcnow() - timedelta(hours=1))
    app.db.update_item(iid, status="queued", route="india_eod", meta=meta)
    return iid


def _seed(app):
    return [_queue(app, "rbi_pr", "prid:63749", RBI_URL, BODIES[0][2], priority_tags=["rbi:directions"]),
            _queue(app, "sebi_circ", "id:105081", SEBI_URL, BODIES[2][2], kind="Circular", date_only=True),
            _queue(app, "pib", "prid:2320934", PIB_URL, BODIES[4][2], ministry="Ministry of Finance")]


def test_digest_dry_run_renders_detailed_items_and_marks_nothing(make_app):
    app = make_app("dry_run")
    mock_fetcher(app, _handler)
    ids = _seed(app)
    r = dg.run(app)
    assert r.status == "written" and r.items == 3 and r.size < dg.GMAIL_SAFE
    html = r.message_path.read_text(encoding="utf-8")
    for needle in ("Daily Macro Digest", "RAAS Research Capital", "What it says", "Key figures",
                   "These instructions will come into effect from April 1, 2027.",
                   "capital charge for CVA risk", "Credit Risk-o-Meter", "57", "Who it applies to",
                   "Government", "RBI", "SEBI", SEBI_PDF):
        assert escape(needle, quote=False) in html or needle in html, needle
    # nothing marked in dry run; the archived release text is stored on its document
    assert {x["status"] for x in app.db.q("select status from items where id = any(%s)", (ids,))} == {"queued"}
    assert app.db.one("select count(*) n from documents where text is not null")["n"] >= 3


def test_digest_live_sends_once_and_marks_items(make_app):
    app = make_app("live")
    mock_fetcher(app, _handler)
    ch = FakeChannel()
    app.dispatcher.channels = [ch]
    ids = _seed(app)
    r = dg.run(app)
    assert r.status == "sent" and len(ch.sent) == 1 and ch.sent[0].startswith("digest:india_eod:")
    rows = app.db.q("select status, digest_id from items where id = any(%s)", (ids,))
    assert {x["status"] for x in rows} == {"digested"} and all(x["digest_id"] for x in rows)
    d = app.db.one("select * from digests")
    assert sorted(d["item_ids"]) == sorted(ids) and d["send_id"]
    assert dg.run(app).status == "empty"                       # nothing left in the queue
    _queue(app, "rbi_pr", "prid:63750", RBI_URL.replace("63749", "63750"), "RBI issues Amendment Directions on SA-CCR")
    assert dg.run(app).status == "already_sent"                # one digest per date
    assert len(ch.sent) == 1


def test_sample_goes_to_operator_only_and_marks_nothing(make_app):
    app = make_app("live")
    mock_fetcher(app, _handler)
    ch = FakeChannel()
    app.dispatcher.channels = [ch]
    ids = _seed(app)
    for i in ids:
        app.db.update_item(i, status="routed", route="realtime")   # how they looked before F-29
    since, until = dg.default_window(utcnow(), 1)
    r = dg.run(app, since=since, until=until + timedelta(hours=1), sample=True, to_operator=True)
    assert r.status == "sent" and r.items == 3
    row = app.db.one("select subject, recipients from sends")
    assert row["subject"].startswith("SAMPLE [MACRO] Daily digest") and row["recipients"] == ["ops@example.com"]
    assert {x["status"] for x in app.db.q("select status from items where id = any(%s)", (ids,))} == {"routed"}


def test_sample_without_operator_never_sends(make_app):
    app = make_app("live")
    mock_fetcher(app, _handler)
    ch = FakeChannel()
    app.dispatcher.channels = [ch]
    _seed(app)
    since, until = dg.default_window(utcnow(), 1)
    r = dg.run(app, since=since, until=until + timedelta(hours=1), sample=True)
    assert r.status == "written" and ch.sent == []


def test_fetch_failure_still_gives_the_opening_paragraphs(make_app):
    app = make_app("dry_run")
    mock_fetcher(app, lambda req: httpx.Response(503))
    paras = ["The 57 th Meeting of the GST Council was held today in New Delhi, under the chairpersonship of the "
             "Union Finance & Corporate Affairs Minister Smt. Nirmala Sitharaman."]
    _queue(app, "pib", "prid:1", PIB_URL, BODIES[4][2], ministry="Ministry of Finance", paragraphs=paras)
    r = dg.run(app)
    html = r.message_path.read_text(encoding="utf-8")
    assert "could not be fetched" in html and "chairpersonship" in html


def test_size_budget_shortens_lowest_priority_items_but_keeps_three_paragraphs(make_app, monkeypatch):
    app = make_app("dry_run")
    mock_fetcher(app, _handler)
    _seed(app)
    monkeypatch.setattr(dg, "GMAIL_SAFE", 30_000)
    r = dg.run(app)
    assert r.trimmed >= 1
    html = r.message_path.read_text(encoding="utf-8")
    assert "Shortened to the leading paragraphs" in html
