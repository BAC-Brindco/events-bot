"""RBI MPC resolution extraction against the archived last 6 (+1 prior) meetings."""
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from events_bot.extract import mpc
from events_bot.extract.validator import validate, validate_all

from .conftest import FIXTURES
from .mpc_meetings import MEETINGS

GOLD = Path(__file__).parent / "golden" / "mpc"


def _extract(d):
    t, ex = mpc.parse_resolution((FIXTURES / "rbi_mpc" / f"pr_{MEETINGS[d]['resolution']}.html").read_bytes(), d)
    return validate_all(ex, {"resolution": t}, expected_period=d.isoformat()), t


def test_six_meetings_plus_prior():
    assert len(MEETINGS) >= 7 and len(list(GOLD.glob("*.yaml"))) == len(MEETINGS)


@pytest.mark.parametrize("d", list(MEETINGS))
def test_matches_golden(d):
    g = yaml.safe_load((GOLD / f"{d.isoformat()}.yaml").read_text(encoding="utf-8"))
    ex, _ = _extract(d)
    assert [e for e in ex if not e.ok] == []
    assert {e.field: e.value_text for e in ex if e.field != "mpc.member" and not e.field.endswith("_dissent")} \
        == g["fields"]
    assert [e.value_text for e in ex if e.field == "mpc.member"] == g["members"]
    assert [{"field": e.field, "member": e.value_text, "view": e.meta["view"]}
            for e in ex if e.field.endswith("_dissent")] == g["dissents"]


@pytest.mark.parametrize("d", list(MEETINGS))
def test_required_fields_and_corridor(d):
    ex, t = _extract(d)
    fields = {e.field for e in ex}
    assert {"mpc.repo", "mpc.sdf", "mpc.msf", "mpc.bank_rate", "mpc.stance", "mpc.rate_vote", "mpc.action",
            "mpc.doc_date"} <= fields
    assert len([e for e in ex if e.field == "mpc.member"]) == 6
    assert mpc.consistency(ex) == []
    for e in ex:
        assert t[e.char_start:e.char_end] == e.value_text


def test_decimal_not_split():
    """Phase 0 hazard 7: '5.25 per cent' must never become '5.'"""
    ex, _ = _extract(min(d for d in MEETINGS if d.year == 2026 and d.month == 8))
    assert next(e for e in ex if e.field == "mpc.repo").value_text == "5.25"


def test_unanimity_found_in_rationale_paragraph():
    from datetime import date
    ex, _ = _extract(date(2025, 8, 6))
    assert next(e for e in ex if e.field == "mpc.rate_vote").value_text == "unanimously voted"


def test_feb_2026_has_no_gdp_projection_and_none_is_invented():
    from datetime import date
    ex, _ = _extract(date(2026, 2, 6))
    assert not [e for e in ex if e.field.startswith("mpc.proj.gdp")]


def test_wrong_page_date_rejected():
    from datetime import date
    t, ex = mpc.parse_resolution((FIXTURES / "rbi_mpc" / "pr_63287.html").read_bytes(), date(2026, 10, 7))
    assert not any(e.field == "mpc.doc_date" for e in ex)


def test_corridor_violation_flagged():
    from datetime import date
    ex, _ = _extract(date(2026, 8, 5))
    next(e for e in ex if e.field == "mpc.sdf").value_norm = Decimal("4.75")
    assert mpc.consistency(ex)


def test_tampered_text_fails():
    from datetime import date
    ex, t = _extract(date(2026, 8, 5))
    repo = next(e for e in ex if e.field == "mpc.repo")
    assert validate(repo, t.replace("unchanged at 5.25", "unchanged at 5.50")).validation_error
