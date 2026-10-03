from datetime import datetime, timezone

from events_bot.core.timeutil import IST, fmt_ist, parse_rbi_pubdate, parse_rfc822


def test_rbi_naive_pubdate_is_ist_not_utc():
    # FLAGS T-03: "Fri, 02 Oct 2026 17:05:00" is 17:05 IST = 11:35 UTC.
    dt = parse_rbi_pubdate("Fri, 02 Oct 2026 17:05:00")
    assert dt == datetime(2026, 10, 2, 11, 35, tzinfo=timezone.utc)
    assert fmt_ist(dt) == "02 Oct 2026 17:05 IST"


def test_rfc822_with_zone():
    assert parse_rfc822("Wed, 17 Sep 2025 18:00:00 GMT") == datetime(2025, 9, 17, 18, 0, tzinfo=timezone.utc)
    assert parse_rfc822("Wed, 17 Sep 2025 14:00:00 -0400") == datetime(2025, 9, 17, 18, 0, tzinfo=timezone.utc)


def test_rfc822_naive_is_refused_unless_zone_given():
    assert parse_rfc822("Fri, 02 Oct 2026 17:05:00") is None
    got = parse_rfc822("Fri, 02 Oct 2026 17:05:00", naive_tz=IST)
    assert got == datetime(2026, 10, 2, 11, 35, tzinfo=timezone.utc)


def test_garbage_dates():
    assert parse_rbi_pubdate("") is None
    assert parse_rbi_pubdate(None) is None
    assert parse_rfc822("not a date") is None
