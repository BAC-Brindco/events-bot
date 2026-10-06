"""FOMC extraction against the archived last-6 (+1 prior) meetings and the golden YAMLs."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from events_bot.extract import fomc
from events_bot.extract.base import parse_number
from events_bot.extract.validator import validate_all

from .conftest import FIXTURES

GOLD = Path(__file__).parent / "golden" / "fomc"
MEETINGS = sorted(p.stem for p in GOLD.glob("*.yaml"))


def _extract(d: str):
    md = date(int(d[:4]), int(d[4:6]), int(d[6:]))
    fx = FIXTURES / "fed"
    st, e1 = fomc.parse_statement((fx / f"monetary{d}a.htm").read_bytes(), md)
    it, e2 = fomc.parse_impl_note((fx / f"monetary{d}a1.htm").read_bytes(), md)
    texts, ex = {"statement": st, "impl": it}, e1 + e2
    sep = fx / f"fomcprojtabl{d}.htm"
    if sep.exists():
        s, e3 = fomc.parse_sep(sep.read_bytes(), md)
        texts["sep"], ex = s, ex + e3
    return validate_all(ex, texts, expected_period=md.isoformat()), texts


def test_six_meetings_plus_prior_archived():
    assert len(MEETINGS) >= 7


@pytest.mark.parametrize("d", MEETINGS)
def test_matches_golden(d):
    g = yaml.safe_load((GOLD / f"{d}.yaml").read_text(encoding="utf-8"))
    ex, _ = _extract(d)
    assert [e for e in ex if not e.ok] == []
    got = {e.field: e.value_text for e in ex
           if e.field not in ("fomc.vote_for", "fomc.vote_against", "fomc.dissent_pref", "fomc.balance_sheet_line")}
    assert got == g["fields"]
    assert [e.value_text for e in ex if e.field == "fomc.vote_for"] == g["vote_for"]
    assert [{"name": e.value_text, "preferred": e.meta["preferred"]} for e in ex
            if e.field == "fomc.vote_against"] == g["vote_against"]


@pytest.mark.parametrize("d", MEETINGS)
def test_every_value_is_verbatim_in_its_document(d):
    ex, texts = _extract(d)
    for e in ex:
        assert texts[e.doc][e.char_start:e.char_end] == e.value_text


@pytest.mark.parametrize("d", MEETINGS)
def test_cross_document_consistency(d):
    ex, _ = _extract(d)
    assert fomc.consistency(ex) == []


def test_sep_prior_row_matches_prior_meetings_own_medians():
    sep_now, _ = _extract("20260916")
    sep_june, _ = _extract("20260617")
    june = {e.field.split(".", 2)[2]: e.value_text for e in sep_june if e.field.startswith("sep.median.")}
    prior = {e.field.split(".", 2)[2]: e.value_text for e in sep_now if e.field.startswith("sep.prior_median.")}
    assert prior and all(june[k] == v for k, v in prior.items())


def test_tampered_document_fails_verbatim_check():
    ex, texts = _extract("20260916")
    lo = next(e for e in ex if e.field == "fomc.target_low")
    tampered = texts["statement"].replace("to 3-3/4 to 4 percent", "to 3-1/4 to 4 percent")
    from events_bot.extract.validator import validate
    assert validate(lo, tampered).validation_error


def test_stale_page_fails_period_check():
    md = date(2026, 10, 28)
    _, ex = fomc.parse_statement((FIXTURES / "fed" / "monetary20260916a.htm").read_bytes(), md)
    # yesterday's statement served for today's meeting: the dated line is absent
    assert not any(e.field == "fomc.doc_date" for e in ex)


def test_large_move_needs_stated_size():
    ex, _ = _extract("20260916")
    prior, _ = _extract("20260729")
    # simulate a 150 bp jump that statement and directive agree on but the text does not state
    for f in ("fomc.target_low", "fomc.directive_target_low"):
        next(e for e in ex if e.field == f).value_norm = Decimal("5.00")
    for f in ("fomc.target_high", "fomc.directive_target_high"):
        next(e for e in ex if e.field == f).value_norm = Decimal("5.25")
    problems = fomc.consistency(ex, prior)
    assert any("not stated" in p for p in problems)
    assert not next(e for e in ex if e.field == "fomc.target_low").ok


def test_inconsistent_statement_and_directive_flagged():
    ex, _ = _extract("20260916")
    next(e for e in ex if e.field == "fomc.target_low").value_norm = Decimal("4.00")
    assert any("!= directive" in p for p in fomc.consistency(ex))


@pytest.mark.parametrize("s,v", [("3-3/4", "3.75"), ("3\u20111/2", "3.5"), ("1/4", "0.25"), ("4", "4"),
                                 ("3.90", "3.90"), ("$160", "160"), ("abc", None)])
def test_parse_number(s, v):
    assert parse_number(s) == (Decimal(v) if v else None)
