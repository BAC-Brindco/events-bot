"""Number guard: adversarial cases from BUILD_PROMPT §7, on real extractions (MPC Aug 2026 vs Jun 2026, FOMC Sep)."""
from datetime import date

import pytest

from events_bot.extract import fomc, mpc
from events_bot.extract.guard import NumberGuard
from events_bot.extract.validator import validate_all

from .conftest import FIXTURES


def _mpc(prid: str, d: date):
    t, ex = mpc.parse_resolution((FIXTURES / "rbi_mpc" / f"pr_{prid}.html").read_bytes(), d)
    return validate_all(ex, {"resolution": t}, expected_period=d.isoformat()), t


@pytest.fixture(scope="module")
def guard():
    cur, text = _mpc("63287", date(2026, 8, 5))
    prior, _ = _mpc("62863", date(2026, 6, 5))
    return NumberGuard(cur, prior, [text])


@pytest.mark.parametrize("bullet", [
    "The repo rate was kept at 5.25 per cent and the stance stays neutral.",
    "CPI inflation for 2026-27 is projected at 5.0 per cent, down from 5.1 per cent in June.",
    "GDP growth for Q1:2027-28 is projected at 7.3 per cent.",
    "Q3:2026-27 inflation is projected at 5.9 per cent.",
    "The SDF rate remains at 5.00 per cent and the MSF rate at 5.50 per cent.",
    'The MPC said it "will maintain a close vigil and remain resolute in its commitment to align inflation with the target".',
    "No numbers here, just the neutral stance.",
])
def test_clean_bullets_pass(guard, bullet):
    assert guard.check_text(bullet) is None, guard.check_text(bullet)


@pytest.mark.parametrize("bullet,why", [
    ("CPI inflation for 2026-27 is projected at 5.2 per cent.", "not in validated"),            # invented
    ("Real GDP growth for 2026-27 is projected at 7 per cent.", "not in validated"),             # rounding drift (6.7)
    ("The repo rate is 5.2 per cent.", "not in validated"),                                       # rounding drift (5.25)
    ("The repo rate is 5.250 per cent.", "not in validated"),                                     # precision drift
    ("CPI inflation for 2025-26 is projected at 5.0 per cent.", "fiscal year"),                   # wrong year
    ("Inflation in Q2:2026-27 is projected at 5.9 per cent.", "period"),                          # wrong quarter (Q3's value)
    ("CPI inflation for 2026-27 is projected at 5.1 per cent.", "prior value"),                  # June value as current
    ("Core inflation for 2026-27 is now 4.7 per cent.", "prior value"),                          # June core as current
    ('The MPC said it "will cut rates soon".', "quote not in source"),                            # fabricated quote
    ("Rates were last cut in 2024.", "year 2024"),                                                # unsupported year
])
def test_adversarial_bullets_dropped(guard, bullet, why):
    reason = guard.check_text(bullet)
    assert reason is not None and why in reason, reason


def test_fallback_when_more_than_half_dropped(guard):
    r = guard.check_bullets(["The repo rate was kept at 5.25 per cent.",
                             "GDP is projected at 9.9 per cent.", "CPI is projected at 8.8 per cent."])
    assert len(r.kept) == 1 and len(r.dropped) == 2 and r.fallback


def test_no_fallback_when_half_or_fewer_dropped(guard):
    r = guard.check_bullets(["The repo rate was kept at 5.25 per cent.", "GDP is projected at 9.9 per cent."])
    assert not r.fallback


def test_fomc_fractions_and_bp():
    d = date(2026, 9, 16)
    st, e1 = fomc.parse_statement((FIXTURES / "fed" / "monetary20260916a.htm").read_bytes(), d)
    it, e2 = fomc.parse_impl_note((FIXTURES / "fed" / "monetary20260916a1.htm").read_bytes(), d)
    ex = validate_all(e1 + e2, {"statement": st, "impl": it}, expected_period=d.isoformat())
    g = NumberGuard(ex, None, [st, it])
    assert g.check_text("The Committee raised the target range by 1/4 point to 3-3/4 to 4 percent.") is None
    assert g.check_text("IORB rises to 3.90 percent.") is None
    assert "not in validated" in g.check_text("IORB rises to 3.9 percent and ON RRP to 3.80 percent.")
    assert "not in validated" in g.check_text("The target range is now 3-1/2 to 3-3/4 percent.")
