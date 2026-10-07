"""PIB: listing/release parsers and the rule set against the hand-labelled releases."""
import json
from datetime import datetime
from pathlib import Path

from events_bot.core.config import load_sources
from events_bot.core.models import RawItem
from events_bot.core.settings import ROOT
from events_bot.core.timeutil import IST
from events_bot.filter.keywords import KeywordFilter
from events_bot.sources.india import pib

from .conftest import FIXTURES

LABELS = Path(__file__).parent / "golden" / "pib" / "triage_labels.json"


def test_allrel_gives_ministry_per_release():
    rows = pib.parse_allrel((FIXTURES / "pib" / "allRel_2026-10-07.html").read_bytes())
    assert len(rows) >= 10
    by = {p: (m, t) for p, m, t in rows}
    assert by[2319908][0] == "Ministry of Commerce & Industry"
    assert "India–U.S. Trade" in by[2319908][1]


def test_release_page_posted_on_is_ist_and_body_paragraphs():
    r = pib.parse_release((FIXTURES / "pib" / "release_2319908.html").read_bytes())
    assert r["ministry"] == "Ministry of Commerce & Industry"
    assert r["posted"] == datetime(2026, 10, 7, 10, 48, tzinfo=IST)
    assert r["paragraphs"][0].startswith("Union Minister of Commerce and Industry Shri Piyush Goyal")


def test_posted_on_formats():
    assert pib.parse_posted("Posted On: 30 SEP 2026 3:15PM by PIB Delhi") == datetime(2026, 9, 30, 15, 15, tzinfo=IST)
    assert pib.parse_posted("Posted On: 01 OCT 2026 2:31 PM by PIB Delhi").hour == 14
    assert pib.parse_posted("no date") is None


def test_rules_never_reject_a_relevant_release_and_keeps_are_precise():
    """Guards config/keywords/pib.yaml: an edit that drops a relevant release fails here."""
    cfg = load_sources(ROOT / "config")["pib"]
    kf = KeywordFilter(ROOT / "config")
    items = json.loads(LABELS.read_text(encoding="utf-8"))["items"]
    rejected_relevant, kept_noise, kept = [], [], 0
    for it in items:
        d = kf.decide(cfg, RawItem(source_id="pib", ext_id="x", url="u", title=it["title"],
                                   meta={"ministry": it["ministry"]}))
        if d.keep:
            kept += 1
            if not it["relevant"]:
                kept_noise.append(it["title"])
        elif d.rule != "uncertain" and it["relevant"]:
            rejected_relevant.append(it["title"])
    assert rejected_relevant == []
    assert kept >= 20 and len(kept_noise) <= 1, kept_noise
