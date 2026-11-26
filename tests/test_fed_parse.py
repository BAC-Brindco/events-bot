"""Source group `fed` (Fed documents and speeches, U.S. Treasury): pure parse tests on real captured bytes
(tests/fixtures/fed/docs, fetched live 2026-11-26)."""
from datetime import datetime, timezone

from events_bot.core.config import load_sources
from events_bot.core.models import FetchResult
from events_bot.core.registry import resolve
from events_bot.core.settings import ROOT
from events_bot.digest import content
from events_bot.sources.us import fed_docs as fd
from events_bot.sources.us import fed_speeches as fs
from events_bot.sources.us import treasury as tr

from .conftest import FIXTURES

FX = FIXTURES / "fed" / "docs"
SOURCES = load_sources(ROOT / "config")


def res(name: str, at=datetime(2026, 11, 26, 6, 0, tzinfo=timezone.utc)) -> FetchResult:
    return FetchResult(url="u", final_url="u", status=200, fetched_at=at, content=(FX / name).read_bytes())


def parse(sid: str, name: str):
    cfg = SOURCES[sid]
    return {i.ext_id: i for i in resolve(cfg.adapter)(cfg, None).parse(res(name), next(iter(cfg.urls)))}


def test_yaml_entries():
    for sid in ("fed_press", "fed_beige", "fed_speeches", "fed_speeches_rich", "fed_speeches_dal", "fed_speeches_ny",
                "treasury_auctions", "treasury_press", "treasury_debt_limit"):
        cfg = SOURCES[sid]
        assert cfg.country == "US" and cfg.enabled and resolve(cfg.adapter)
        assert cfg.section == ("Treasury" if sid.startswith("treasury") else "Federal Reserve")


# ---- fed_press -------------------------------------------------------------------------------------------

def test_press_minutes_realtime_statement_dropped_discount_minutes_wrap():
    items = parse("fed_press", "press_monetary_2026-11-26.xml")
    assert len(items) == 15
    m = items["monetary20261118a.htm"]
    assert m.title == "Minutes of the Federal Open Market Committee, October 27-28, 2026"
    assert m.meta["route_hint"] == "realtime" and m.meta["kind"] == "FOMC minutes"
    assert m.source_published_at == datetime(2026, 11, 18, 19, 0, tzinfo=timezone.utc)   # scheduled 14:00 EST
    st = items["monetary20261028a.htm"]
    assert st.title == "Federal Reserve issues FOMC statement" and st.meta["route_hint"] == "drop"
    assert items["monetary20260916b.htm"].meta["route_hint"] == "drop"                  # SEP press release
    assert items["monetary20261124a.htm"].meta["route_hint"] == "us_morning_wrap"       # discount-rate minutes
    assert items["monetary20261112a.htm"].meta["route_hint"] == "us_morning_wrap"       # 2027 schedule


def test_minutes_urls_and_policy_paragraphs():
    urls = fd.minutes_urls((FX / "monetary20261118a.htm").read_bytes(), "x")
    assert urls == {"html": "https://www.federalreserve.gov/monetarypolicy/fomcminutes20261028.htm",
                    "pdf": "https://www.federalreserve.gov/monetarypolicy/files/fomcminutes20261028.pdf"}
    assert fd.minutes_urls(None, "Minutes of the Federal Open Market Committee, July 28–29, 2026")["html"] \
        .endswith("fomcminutes20260729.htm")
    text = fd.minutes_text((FX / "fomcminutes20261028.htm").read_bytes(), None)
    paras = fd.minutes_policy_paragraphs(text)
    assert len(paras) == 3 and all(p in text.split("\n") for p in paras)
    assert paras[0].startswith("In their consideration of monetary policy at this meeting, most participants")
    assert paras[1].startswith("With regard to the outlook for monetary policy in the months ahead")
    assert "10 members agreed to maintain the target range for the federal funds rate at 3-3/4 to 4 percent" in paras[2]
    assert fd.strip_cpa_head(paras[2]).startswith("In support of the Committee's dual-mandate goals")


# ---- fed_beige ----------------------------------------------------------------------------------------------

def test_beige_dates_from_calendar_json():
    dates = fd.beige_dates((FX / "calendar_2026-11-26.json").read_bytes())       # file starts with a UTF-8 BOM
    assert (FX / "calendar_2026-11-26.json").read_bytes()[:3] == b"\xef\xbb\xbf"
    assert dates[0] == datetime(2026, 11, 25, 14, 0, tzinfo=fd.ET)
    assert dates[0].astimezone(timezone.utc) == datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc)
    assert datetime(2026, 10, 14, 14, 0, tzinfo=fd.ET) in dates and len(dates) == 56


def test_beige_parse_is_history_only():
    items = parse("fed_beige", "calendar_2026-11-26.json")
    assert "beige:20261125" not in items           # fresh: poll() emits it only once the PDF is live
    it = items["beige:20261014"]
    assert it.url == "https://www.federalreserve.gov/monetarypolicy/files/BeigeBook_20261014.pdf"
    assert it.meta["route_hint"] == "us_morning_wrap"


def test_beige_national_summary_is_verbatim():
    text = content.pdf_body((FX / "BeigeBook_20261125.pdf").read_bytes())
    paras = fd.beige_national_summary(text)
    assert len(paras) == 4 and all(p in text.split("\n") for p in paras)
    assert paras[0].startswith("Eight of the twelve Federal Reserve Districts reported growth in the slight to "
                               "moderate range, while four Districts reported no change.")
    assert paras[-1].startswith("Prices increased moderately overall")


# ---- speeches -----------------------------------------------------------------------------------------------

def test_board_speeches_routine_remarks_dropped():
    items = parse("fed_speeches", "speeches_and_testimony_2026-11-26.xml")
    assert items["jefferson20261119a.htm"].meta["route_hint"] == "us_morning_wrap"
    assert items["waller20261120a.htm"].title == "Waller, Opening Remarks"
    assert items["waller20261120a.htm"].meta["route_hint"] == "drop"
    assert items["bowman20261110a.htm"].source_published_at == datetime(2026, 11, 10, 20, 30, tzinfo=timezone.utc)


def test_testimony_feed_1899_date_is_ignored():
    """Phase 0 capture of testimony.xml (2026-10-01): several items carry 'Sat, 30 Dec 1899'."""
    assert b"30 Dec 1899" in (FX / "testimony_2026-10-01.xml").read_bytes()
    items = parse("fed_speeches", "testimony_2026-10-01.xml")
    bogus = items["barr20241120a.htm"]
    assert bogus.source_published_at is None and bogus.published_raw.endswith("1899 15:00:00 GMT")
    assert bogus.meta["route_hint"] == "us_morning_wrap" and bogus.meta["kind"] == "Testimony"
    assert items["powell20240709a.htm"].title == "Powell, Semiannual Monetary Policy Report to the Congress"


def test_richmond_dallas_president_only_and_wall_time():
    rich = parse("fed_speeches_rich", "richmond_speeches_2026-11-26.xml")
    b = rich["barkin_speech_20261109"]
    assert b.meta["route_hint"] == "us_morning_wrap"
    assert b.source_published_at.astimezone(timezone.utc) == datetime(2026, 11, 9, 17, 15, tzinfo=timezone.utc)
    sep = rich["barkin_speech_20260922"]          # "EST" label in September = EDT wall time
    assert sep.source_published_at.astimezone(timezone.utc) == datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)
    assert rich["kovner_speech_20260506"].meta["route_hint"] == "drop"      # Executive Vice President
    dal = parse("fed_speeches_dal", "dallas_speeches_2026-11-26.xml")
    assert dal["lkl261110"].meta["route_hint"] == "us_morning_wrap"
    assert dal["lkl261110"].source_published_at.astimezone(timezone.utc) == datetime(2026, 11, 10, 15, 50,
                                                                                      tzinfo=timezone.utc)
    assert dal["261113eg"].meta["route_hint"] == "drop"                     # opening remarks
    assert dal["261102ssw"].meta["route_hint"] == "drop"                    # not the president


def test_nyfed_index():
    rows = fs.parse_nyfed_index((FX / "nyfed_speeches_2026-11-26.html").read_bytes())
    assert len(rows) > 500
    assert rows[0] == {"url": "https://www.newyorkfed.org/newsevents/speeches/2026/wil261123",
                       "title": "Williams: The Economic Mixdown", "date": datetime(2026, 11, 23).date(),
                       "speaker": "Williams"}
    items = parse("fed_speeches_ny", "nyfed_speeches_2026-11-26.html")
    assert len(items) == fs.NY_ROWS
    assert items["wil261123"].meta["route_hint"] == "us_morning_wrap"
    assert items["per260922"].meta["route_hint"] == "us_morning_wrap"
    assert items["sca261104"].meta["route_hint"] == "drop"


def test_speech_bodies_select_the_text():
    for f, sel, start in [("jefferson20261119a.htm", "#article", "November 19, 2026"),
                          ("barkin_speech_20261109.html", ".tmplt__content", "Thanks for the kind introduction"),
                          ("lkl261110.html", "#content", "Economic and monetary policy outlook"),
                          ("wil261123.html", ".ts-article-text", "Introduction")]:
        text = content.html_body((FX / f).read_bytes(), sel)
        assert text.startswith(start) and len(content._paragraphs(text)) >= 5, f


# ---- treasury -----------------------------------------------------------------------------------------------

def test_auctions_coupon_kept_bill_dropped():
    items = parse("treasury_auctions", "td_auctioned_2026-11-26.json")
    note = items["91282CSA0:2026-11-25"]
    assert note.title == "7-Year Note auction results" and note.meta["route_hint"] == "us_morning_wrap"
    assert note.meta["result_xml"] == "https://www.treasurydirect.gov/xml/R_20261125_3.xml"
    assert note.url == "https://www.treasurydirect.gov/instit/annceresult/press/preanre/2026/R_20261125_3.pdf"
    assert note.source_published_at.astimezone(timezone.utc) == datetime(2026, 11, 25, 16, 33, 44, tzinfo=timezone.utc)
    bill = items["912797WH6:2026-11-25"]
    assert bill.title == "4-Week Bill (reopening) auction results" and bill.meta["route_hint"] == "drop"
    assert items["912797VA2:2026-11-19"].meta["route_hint"] == "drop"        # 9-day CMB
    assert items["91282CRE3:2026-11-19"].meta["route_hint"] == "us_morning_wrap"   # TIPS
    assert items["91282CRS2:2026-11-24"].meta["route_hint"] == "us_morning_wrap"   # FRN
    assert sum(i.meta["route_hint"] != "drop" for i in items.values()) == 5


def test_auction_result_xml_lines_are_verbatim():
    raw = (FX / "R_20261125_3.xml").read_bytes()
    lines = tr.auction_lines(tr.parse_result_xml(raw))
    text = raw.decode()
    assert lines[1].startswith("High yield 4.708 percent, allotted at high 41.41 percent")
    assert "Bid-to-cover ratio 2.43;" in lines[2]
    for v in ("4.708", "41.41", "2.43", "25325938800", "14445058100", "3748000000", "43518996900"):
        assert f">{v}<" in text
    assert "indirect 58.2%, direct 33.2%, primary dealers 8.6%" in lines[3]
    frn = tr.auction_lines(tr.parse_result_xml((FX / "R_20261124_4.xml").read_bytes()))
    assert frn[1].startswith("High discount margin 0.050 percent") and "Bid-to-cover ratio 2.87" in frn[2]
    tips = tr.auction_lines(tr.parse_result_xml((FX / "R_20261119_3.xml").read_bytes()))
    assert tips[1].startswith("High yield 2.903 percent (real yield; inflation-protected)")


def test_treasury_press_keeps_financing_drops_noise():
    items = parse("treasury_press", "treasury_govdelivery_2026-11-26.xml")
    kept = {i.title for i in items.values() if i.meta["route_hint"] != "drop"}
    assert "Treasury Announces Marketable Borrowing Estimates" in kept
    assert items["433f774"].meta["route_hint"] == "drop"         # Trump Accounts
    assert items["4336192"].meta["route_hint"] == "drop"         # cartel sanctions
    # a sanctions designation whose release names Indian entities is kept
    assert items["431a6b8"].meta["route_hint"] == "us_morning_wrap"
    assert len(kept) == 4
    text = content.html_body((FX / "govdelivery_43161a8.html").read_bytes(), "#bulletin_body")
    assert any("privately-held net marketable borrowing" in p for p in content._paragraphs(text))


def test_debt_limit_page():
    items = parse("treasury_debt_limit", "debt_limit_2026-11-26.html")
    it = items["Debt-Limit-Letter-to-Congress-06-25-2025.pdf"]
    assert it.title == "Debt Limit Letter to Congress (06/25/2025)"
    assert it.url == "https://home.treasury.gov/system/files/136/Debt-Limit-Letter-to-Congress-06-25-2025.pdf"
    assert it.source_published_at.date() == datetime(2025, 6, 25).date() and it.meta["date_only"]
    assert "Fact-Sheet-Department-of-Education-and-Department-of-the-Treasury.pdf" not in items   # mega menu
    assert all(i.meta["route_hint"] == "us_morning_wrap" for i in items.values())
