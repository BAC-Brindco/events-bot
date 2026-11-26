"""Shared plumbing for the US / India Tier 2-3 expansion (FLAGS F-30): sources.d loading, adapter-declared
scope (route_hint), the US print alert renderer, and the US Morning Wrap / Weekly digests."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import shutil

from events_bot.core.config import DeliveryConfig, config_problems, load_delivery, load_sources
from events_bot.core.models import RawItem, ScheduledEvent, SourceConfig
from events_bot.core.settings import ROOT
from events_bot.core.timeutil import utcnow
from events_bot.digest import content, run as dg, scope
from events_bot.digest.kinds import US_MORNING_WRAP, WEEKLY, ordered_sections, INDIA_EOD
from events_bot.extract.base import Extraction
from events_bot.stage1 import usprint
from events_bot.stage1.render import Doc

from .test_sends_and_health import FakeChannel


def _cfg(**kw) -> SourceConfig:
    base = dict(id="x_src", country="US", tier=1, kind="stream", feed_type="rss", urls={"a": "https://e.x/"},
                poll_interval=900, adapter="us.fed:Fomc")
    return SourceConfig.model_validate({**base, **kw})


def _item(title="Title", **meta) -> RawItem:
    return RawItem(source_id="x_src", ext_id="1", url="https://e.x/1", title=title, meta=meta)


# ---- config -------------------------------------------------------------------------------------

def test_sources_d_files_are_merged_and_duplicates_rejected(tmp_path):
    shutil.copy(ROOT / "config" / "sources.yaml", tmp_path / "sources.yaml")
    (tmp_path / "sources.d").mkdir()
    (tmp_path / "sources.d" / "zz.yaml").write_text(
        "sources:\n  - {id: extra_one, country: US, tier: 2, kind: stream, feed_type: rss,\n"
        "     urls: {a: 'https://e.x/'}, poll_interval: 900, adapter: 'us.fed:Fomc', section: Treasury}\n")
    srcs = load_sources(tmp_path)
    assert "rbi_pr" in srcs and srcs["extra_one"].section == "Treasury"
    (tmp_path / "sources.d" / "zzz.yaml").write_text((tmp_path / "sources.d" / "zz.yaml").read_text())
    try:
        load_sources(tmp_path)
        raise AssertionError("duplicate id accepted")
    except ValueError as e:
        assert "duplicate" in str(e)


# ---- scope ----------------------------------------------------------------------------------------

DELIVERY = DeliveryConfig.model_validate({
    "us_morning_wrap": {"hour": 7}, "india_eod": {"hour": 18, "minute": 30}, "weekly": {"hour": 9},
    "heartbeat": {"hour": 8}, "us_realtime_keywords": ["India", "tariff", "Section 232"]})


def test_route_hints():
    c = _cfg()
    assert scope.decide(c, _item(route_hint="drop", scope_reason="nomination")) == scope.Scope(None, "nomination")
    assert scope.decide(c, _item(route_hint="realtime")).route == "realtime"
    assert scope.decide(c, _item(route_hint="weekly")).route == "weekly"
    # F-08: tariff / India actions go real time, the rest of the White House feed to the morning wrap
    hot = scope.decide(c, _item("Adjusting Imports of Pharmaceuticals Under Section 232", route_hint="realtime_if_keyword"),
                       DELIVERY)
    assert hot.route == "realtime" and "Section 232" in hot.reason
    cold = scope.decide(c, _item("Restoring Gold Standard Science", route_hint="realtime_if_keyword"), DELIVERY)
    assert cold.route == "us_morning_wrap"
    # whole-word match only: "Indiana" is not "India"
    assert scope.decide(c, _item("Disaster declaration for Indiana", route_hint="realtime_if_keyword"),
                        DELIVERY).route == "us_morning_wrap"


def test_no_hint_routes_by_country_and_tier():
    assert scope.decide(_cfg(), _item()).route == "us_morning_wrap"
    assert scope.decide(_cfg(country="IN", id="trai"), _item()).route == "india_eod"
    assert scope.decide(_cfg(country="IN", tier=1, id="dgft"), _item()).route == "india_eod"   # F-29: never realtime
    assert scope.decide(_cfg(tier=3), _item()).route == "weekly"
    # the F-29 India rules are untouched
    assert scope.decide(_cfg(id="bse_notices", country="IN"), _item()).route is None


# ---- US print alert ---------------------------------------------------------------------------------

TEXT = ("The Consumer Price Index for All Urban Consumers (CPI-U) increased 0.3 percent on a seasonally adjusted "
        "basis in October, after rising 0.2 percent in September. Over the last 12 months, the all items index "
        "increased 2.9 percent before seasonal adjustment.")


def _ex(field, value, period, unit="growth_pct", occurrence=0):
    i = -1
    for _ in range(occurrence + 1):
        i = TEXT.index(value, i + 1)
    return Extraction(field=field, value_text=value, value_norm=Decimal(value), unit=unit, period=None,
                      char_start=i, char_end=i + len(value), doc="nr0", meta={"period": period})


SPEC = usprint.PrintSpec(
    kind="cpi", name="CPI inflation", tag="BLS",
    headline=(("CPI m/m", "bls.cpi.mom.current"), ("y/y", "bls.cpi.yoy.current")),
    cards=(usprint.Card("CPI-U, m/m SA", "bls.cpi.mom.current", "bls.cpi.mom.prior"),
           usprint.Card("CPI-U, 12-month NSA", "bls.cpi.yoy.current")),
    required=("bls.cpi.mom.current", "bls.cpi.yoy.current", "bls.core.mom.current"),
    table_title="Month-on-month, per cent")


def test_us_print_alert_quotes_figures_and_flags_missing():
    from events_bot.extract.validator import validate_all
    ex = [_ex("bls.cpi.mom.current", "0.3", "October"), _ex("bls.cpi.mom.prior", "0.2", "September"),
          _ex("bls.cpi.yoy.current", "2.9", "October")]
    validate_all(ex, {"nr0": TEXT})
    cap = usprint.Capture(period="October 2026", texts={"nr0": TEXT}, extractions=ex,
                          docs=[Doc("News release", "https://www.bls.gov/news.release/cpi.nr0.htm")],
                          bullets=[TEXT.split(". ")[0] + "."],
                          release_at=datetime(2026, 11, 25, 13, 30, tzinfo=timezone.utc))
    m = usprint.build(SPEC, cap, ref="usprint:cpi:2026-11-25", first_seen=cap.release_at, mode="dry_run")
    assert m.subject == "[BLS] CPI inflation | October 2026 | CPI m/m 0.3; y/y 2.9"
    assert "change +0.1 (computed)" in m.body_text
    assert "19:00 IST" in m.body_text                       # 08:30 ET in US winter
    assert "bls.core.mom.current: not found in source" in m.body_text      # required but absent
    assert "U.S. Bureau of Labor Statistics" in m.body_html


# ---- digests ----------------------------------------------------------------------------------------

def test_sections_order():
    assert ordered_sections(US_MORNING_WRAP, {"FDA", "Treasury", "Zeta"}) == ["Treasury", "FDA", "Zeta"]
    assert ordered_sections(INDIA_EOD, {"Index changes", "Government"}) == ["Government", "Index changes"]


class _Hook:
    """Stands in for a new source's adapter: digest_body returns the archived text as Body."""

    def __init__(self, text):
        self.text = text

    def digest_body(self, it):
        return content.Body(self.text, content._paragraphs(self.text), it.url)


BODY = ("The Treasury today announced the results of the 10-Year Note auction, with a high yield of 4.125 percent "
        "and a bid-to-cover ratio of 2.51, against 2.47 at the previous reopening.\n"
        "Indirect bidders took 68.2 percent of the competitive amount, direct bidders 18.1 percent.")


def _add_source(app, sid, **kw):
    app.sources[sid] = _cfg(id=sid, **kw)
    app.db.sync_sources(app.sources)


def _queue(app, sid, route, title, hours=3):
    it = RawItem(source_id=sid, ext_id=f"{sid}:{title[:10]}", url=f"https://e.x/{sid}", title=title,
                 source_published_at=utcnow() - timedelta(hours=hours), meta={"route_hint": route})
    iid = app.db.insert_item(it, url_norm=it.url, title_norm=title.lower(), tier=1,
                             first_seen_at=utcnow() - timedelta(hours=hours))
    app.db.update_item(iid, status="queued", route=route, meta=it.meta)
    return iid


def test_us_morning_wrap_sends_once_with_source_sections(make_app, monkeypatch):
    app = make_app("live")
    ch = FakeChannel()
    app.dispatcher.channels = [ch]
    _add_source(app, "treasury_auctions", section="Treasury", issuer="U.S. Department of the Treasury")
    monkeypatch.setattr(app, "adapter", lambda sid: _Hook(BODY))
    iid = _queue(app, "treasury_auctions", "us_morning_wrap", "10-Year Note Auction Results")
    _queue(app, "pib", "india_eod", "Recommendations of the 57th Meeting of the GST Council")
    r = dg.run(app, kind=US_MORNING_WRAP)
    assert r.status == "sent" and r.items == 1 and ch.sent == [r.ref] and r.ref.startswith("digest:us_morning_wrap:")
    html = r.message_path.read_text(encoding="utf-8")
    assert "US Morning Wrap" in html and "Treasury" in html and "U.S. Department of the Treasury" in html
    assert "4.125" in html
    assert app.db.one("select status from items where id = %s", (iid,))["status"] == "digested"
    assert app.db.one("select kind from digests")["kind"] == "us_morning_wrap"
    assert dg.run(app, kind=US_MORNING_WRAP).status == "empty"
    # the India digest still sees only its own queue
    assert dg.run(app).items == 1


def test_weekly_digest_carries_forward_calendar_even_without_items(make_app):
    app = make_app("dry_run")
    at = utcnow() + timedelta(days=3)
    app.db.upsert_event(ScheduledEvent(ref="usprint:cpi:test", source_id="fomc", event_type="cpi",
                                       title="CPI inflation (test)", scheduled_at=at, window_start=at,
                                       window_end=at + timedelta(hours=1), calendar_url="https://e.x/"), utcnow())
    r = dg.run(app, kind=WEEKLY)
    assert r.status == "written" and r.items == 0
    html = r.message_path.read_text(encoding="utf-8")
    assert "Weekly Digest" in html and "Next 14 days" in html and "CPI inflation (test)" in html


def test_shipped_config_has_a_source_behind_every_delivery_promise(tmp_path):
    cfg_dir = ROOT / "config"
    srcs, delivery = load_sources(cfg_dir), load_delivery(cfg_dir)
    assert config_problems(srcs, delivery) == []
    # the November 2026 failure: prints promised, none configured
    us_prints = {k: v for k, v in srcs.items() if k not in ("bls", "bea", "census")}
    assert config_problems(us_prints, delivery) == [
        "delivery.us_realtime_prints is true but no enabled US ScheduledPrint source exists"]
