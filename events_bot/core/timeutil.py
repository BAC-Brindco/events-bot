"""Time handling. Everything is stored in UTC and rendered in IST.

Source timestamps are messy (FLAGS T-02, T-03): RBI's RSS pubDate has no zone and
is IST, feedparser drops it, and a generic RFC-822 parser would read it as UTC
(5h30m wrong). Parsers here take the zone explicitly instead of guessing.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")

_TZ_SUFFIX = re.compile(r"(GMT|UTC|Z|[+-]\d{2}:?\d{2}|\b[A-Z]{2,4})\s*$")


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; attach the source's zone before converting")
    return dt.astimezone(UTC)


def to_ist(dt: datetime) -> datetime:
    return to_utc(dt).astimezone(IST)


def fmt_ist(dt: datetime | None, with_date: bool = True) -> str:
    if dt is None:
        return "n/a"
    d = to_ist(dt)
    return d.strftime("%d %b %Y %H:%M IST" if with_date else "%H:%M IST")


def parse_rfc822(s: str | None, naive_tz: ZoneInfo | timezone | None = None) -> datetime | None:
    """Parse an RFC-822 date. A stamp with no zone is only accepted when the
    caller states the zone (naive_tz); otherwise None, never a guess."""
    if not s or not s.strip():
        return None
    s = s.strip()
    has_tz = bool(_TZ_SUFFIX.search(s))
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if not has_tz or dt.tzinfo is None:
        if naive_tz is None:
            return None
        dt = dt.replace(tzinfo=naive_tz)
    return to_utc(dt)


def parse_rbi_pubdate(s: str | None) -> datetime | None:
    """RBI RSS: "Fri, 02 Oct 2026 17:05:00" with no zone, which is IST."""
    if not s:
        return None
    try:
        return to_utc(datetime.strptime(s.strip(), "%a, %d %b %Y %H:%M:%S").replace(tzinfo=IST))
    except ValueError:
        return parse_rfc822(s, naive_tz=IST)
