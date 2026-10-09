from datetime import timedelta

import httpx

from events_bot.core.models import RawItem
from events_bot.core.timeutil import utcnow

from .conftest import FIXTURES, mock_fetcher


def item(source="rbi_pr", ext="prid:1", title="RBI issues Directions on capital for commercial banks",
         url=None, age_h=0.1, summary=None, meta=None):
    return RawItem(source_id=source, ext_id=ext, url=url or f"https://rbi.org.in/x.aspx?{ext.replace(':', '=')}",
                   title=title, source_published_at=utcnow() - timedelta(hours=age_h), summary=summary,
                   meta=meta or {})


def seed_baseline(app, source="rbi_pr"):
    app.pipeline.process(app.sources[source], [item(source=source, ext="prid:0", title="baseline row")])


def test_first_poll_is_baseline_no_alert(make_app):
    app = make_app()
    st = app.pipeline.process(app.sources["rbi_pr"], [item(ext="prid:1"), item(ext="prid:2", title="Other")])
    assert (st.new, st.baseline, st.realtime) == (2, 2, 0)
    assert not (app.settings.out_dir / "dry_run").exists()


def test_in_scope_stream_item_goes_to_the_daily_digest_not_realtime(make_app):
    """F-29: only data prints alert in real time; a kept RBI release waits for the 18:30 digest."""
    app = make_app()
    seed_baseline(app)
    st = app.pipeline.process(app.sources["rbi_pr"], [item(ext="prid:5")])
    assert (st.realtime, st.queued) == (0, 1)
    row = app.db.get_item("item:rbi_pr:prid:5")
    assert row["status"] == "queued" and row["route"] == "india_eod" and row["meta"]["scope"]
    assert not (app.settings.out_dir / "dry_run").exists()


def test_out_of_scope_item_is_logged_not_sent(make_app):
    app = make_app()
    seed_baseline(app)
    st = app.pipeline.process(app.sources["rbi_pr"],
                              [item(ext="prid:6", title="10 NBFCs surrender their Certificates of Registration to the RBI")])
    assert (st.filtered, st.realtime, st.queued) == (1, 0, 0)
    rej = app.db.recent_filtered(5)[0]
    assert rej["rule"] == "out_of_scope"


def test_data_print_is_realtime_dry_run_writes_file_not_sends(make_app):
    app = make_app()
    seed_baseline(app, "mospi")
    st = app.pipeline.process(app.sources["mospi"], [item(
        source="mospi", ext="id:5", title="Press Release of CPI for September 2026",
        url="https://www.mospi.gov.in/uploads/cpi.pdf", meta={"kind": "cpi"})])
    assert st.realtime == 1
    files = list((app.settings.out_dir / "dry_run").glob("*.txt"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "Press Release of CPI for September 2026" in body
    assert app.db.one("select count(*) as n from sends")["n"] == 0


def test_same_item_twice_is_ignored(make_app):
    app = make_app()
    seed_baseline(app)
    cfg = app.sources["rbi_pr"]
    assert app.pipeline.process(cfg, [item(ext="prid:7")]).new == 1
    assert app.pipeline.process(cfg, [item(ext="prid:7")]).new == 0


def test_cross_source_url_duplicate(make_app):
    app = make_app()
    seed_baseline(app, "rbi_pr")
    seed_baseline(app, "rbi_notif")
    app.pipeline.process(app.sources["rbi_pr"], [item(ext="prid:9", url="https://www.rbi.org.in/a.aspx?id=9")])
    st = app.pipeline.process(app.sources["rbi_notif"],
                              [item(source="rbi_notif", ext="id:9", url="http://rbi.org.in/A.aspx?id=9",
                                    title="totally different title text here")])
    assert st.duplicate == 1
    row = app.db.get_item("item:rbi_notif:id:9")
    assert row["status"] == "duplicate" and row["dup_rule"] == "url"


def test_cross_source_title_duplicate_but_not_same_source_recurring(make_app):
    app = make_app()
    seed_baseline(app, "rbi_pr")
    seed_baseline(app, "rbi_notif")
    # Recurring titles that the rbi_pr routine filter does not drop.
    t1 = "Monthly Data on India's International Trade in Services for August 2026"
    t2 = "Monthly Data on India's International Trade in Services for September 2026"
    st = app.pipeline.process(app.sources["rbi_pr"], [item(ext="prid:20", title=t1), item(ext="prid:21", title=t2)])
    assert st.duplicate == 0 and st.queued == 2             # same source, different day: both kept
    st = app.pipeline.process(app.sources["rbi_notif"], [item(source="rbi_notif", ext="id:20", title=t1 + ".")])
    assert st.duplicate == 1                                 # echo on another channel: deduped
    assert app.db.get_item("item:rbi_notif:id:20")["dup_rule"].startswith("title:")


def test_filter_rejection_is_logged(make_app):
    app = make_app()
    cfg = app.sources["rbi_pr"].model_copy(update={"filters": {"include_any": ["repo rate"], "exclude_any": ["quiz"]}})
    seed_baseline(app)
    st = app.pipeline.process(cfg, [item(ext="prid:30", title="RBI quiz for schools on repo rate"),
                                    item(ext="prid:31", title="Statement on repo rate decision"),
                                    item(ext="prid:32", title="Unrelated item about currency notes")])
    assert (st.filtered, st.queued) == (2, 1)
    rules = {r["title"]: r["rule"] for r in app.db.recent_filtered(10)}
    assert rules["RBI quiz for schools on repo rate"] == "exclude_keyword"
    assert rules["Unrelated item about currency notes"] == "no_match"


def test_keyword_word_boundary(make_app):
    app = make_app()
    cfg = app.sources["rbi_pr"].model_copy(update={"filters": {"include_any": ["PLI"]}})
    d = app.pipeline.kf.decide(cfg, item(title="Compliance framework for banks"))
    assert not d.keep                                         # "PLI" must not match "compliance"
    assert app.pipeline.kf.decide(cfg, item(title="Cabinet extends PLI scheme")).keep


def test_old_item_goes_to_digest_not_realtime(make_app):
    app = make_app()
    seed_baseline(app)
    st = app.pipeline.process(app.sources["rbi_pr"], [item(ext="prid:40", age_h=30)])
    assert (st.realtime, st.queued) == (0, 1)
    assert app.db.get_item("item:rbi_pr:prid:40")["route"] == "india_eod"


def test_watchlist_tags(make_app, tmp_path):
    app = make_app()
    from events_bot.filter.watchlist import Watchlist
    wl = tmp_path / "wl.csv"
    wl.write_text("ticker,name,aliases\nSUNPHARMA,Sun Pharmaceutical Industries,Sun Pharma;Halol\n", encoding="utf-8")
    app.pipeline.watchlist = Watchlist(wl)
    seed_baseline(app)
    app.pipeline.process(app.sources["rbi_pr"], [item(ext="prid:50", title="FDA warning letter to Sun Pharma Halol unit")])
    assert app.db.get_item("item:rbi_pr:prid:50")["tags"] == ["watch:SUNPHARMA"]


def test_poll_end_to_end_with_mock_feed(make_app):
    app = make_app()
    rss = (FIXTURES / "rbi" / "pressreleases_rss_2026-10-03.xml").read_bytes()
    mock_fetcher(app, lambda req: httpx.Response(200, content=rss, headers={"etag": '"e1"'}))
    st = app.poll_source("rbi_pr")
    assert st.baseline == 10
    h = app.db.health_rows()[0]
    assert h["consecutive_errors"] == 0 and h["last_success_at"] is not None
