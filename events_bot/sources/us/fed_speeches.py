"""Federal Reserve speeches and testimony -> US Morning Wrap (section "Federal Reserve").

fed_speeches         Board: https://www.federalreserve.gov/feeds/speeches_and_testimony.xml (superset of the
                     speeches and testimony feeds; ETag+LM). Governors only. One testimony item carries an
                     1899 date (Phase 0): dates before 1990 are treated as absent. pubDate = scheduled start.
fed_speeches_rich    Richmond Fed RSS https://www.richmondfed.org/press_room/speeches?cc_view=rss (no
                     validators, ~300 items back to 1993). pubDate is labelled "EST" even in summer, so the
                     label is ignored and the wall time is read as America/New_York.
fed_speeches_dal     Dallas Fed RSS https://www.dallasfed.org/rss/speeches.xml (no validators). "CST" labels,
                     read as America/Chicago wall time.
fed_speeches_ny      NY Fed https://www.newyorkfed.org/newsevents/speeches: no RSS, no validators, ~600 KB,
                     server-rendered table (date, "Speaker: Title"). Throttled with tick_every_minutes.

Scope (drop aggressively, F-29):
  * every item titled as routine opening / welcome / introductory / closing remarks is dropped; testimony is
    always kept (the Chair's semi-annual testimony in particular)
  * Reserve Bank feeds: only the bank's president (Richmond and Dallas feeds also carry research directors and
    senior vice presidents); the NY Fed index keeps the speakers listed in filters.keep_speakers
Other Reserve Banks: Phase 0 found no current feed (Boston/Atlanta stale, Chicago broken, SF 404; Cleveland,
Philadelphia, St. Louis, KC, Minneapolis have none).
"""
from __future__ import annotations

import re
from datetime import datetime, time
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import ET, parse_rfc822
from ...digest import content
from .fed_docs import rss_entries, sane_date

ROUTINE = re.compile(r"^(?:opening|welcome|welcoming|introductory|closing|concluding)\s+remarks\b|^remarks$|"
                     r"^remarks (?:to|at) .*\bstudents\b|^(?:introduction of|introducing)\b", re.I)
PRESIDENT = re.compile(r"(?<!Vice )(?<!Vice-)\bPresident\b")
WRAP = "us_morning_wrap"


def is_routine(title: str) -> bool:
    return bool(ROUTINE.search(title.strip()))


def wall_time(raw: str | None, tz: ZoneInfo) -> datetime | None:
    """RFC-822 date whose zone label is unreliable ('EST' in July): keep the wall time, apply `tz`."""
    if not raw:
        return None
    s = re.sub(r"\s+(?:[A-Z]{2,4}|[+-]\d{4})\s*$", "", raw.strip())
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    return sane_date(dt.replace(tzinfo=tz))


class _BodyMixin:
    body_selector = "#article"

    def digest_body(self, it: RawItem) -> content.Body:
        r = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type=f"digest:{self.cfg.id}_page", expect="html",
                                            conditional=False)
        text = content.html_body(r.content, self.body_selector)
        return content.Body(text, content._paragraphs(text), it.url, None, r.document_id)


class FedBoardSpeeches(_BodyMixin, SourceAdapter):
    doc_type = "fed_speeches"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in rss_entries(res.content):
            if not r["link"]:
                continue
            speaker, _, topic = r["title"].partition(", ")
            testimony = r["category"].lower() == "testimony" or "/testimony/" in r["link"]
            if testimony:
                hint, reason = WRAP, "Board testimony"
            elif speaker in (self.cfg.filters.get("always_keep_speakers") or []):
                hint, reason = WRAP, "Fed Chair: always kept"
            elif is_routine(topic or r["title"]):
                hint, reason = "drop", "routine remarks (title)"
            else:
                hint, reason = WRAP, "Board member speech"
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=r["link"].rsplit("/", 1)[-1], url=r["link"], title=r["title"],
                source_published_at=sane_date(parse_rfc822(r["published_raw"])), published_raw=r["published_raw"],
                summary=re.sub(r"\s+", " ", r["summary"]).strip() or None, document_id=res.document_id,
                meta={"route_hint": hint, "scope_reason": reason, "kind": "Testimony" if testimony else "Speech",
                      "speaker": speaker if topic else None,
                      "time_note": "RSS pubDate is the scheduled start time"}))
        return items


class RegionalFedRss(_BodyMixin, SourceAdapter):
    """Richmond / Dallas speech RSS. filters: {tz: America/New_York, body_selector: '#content', bank: Richmond}"""
    doc_type = "fed_speeches_regional"

    @property
    def body_selector(self) -> str:  # type: ignore[override]
        return self.cfg.filters.get("body_selector", "main")

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        tz = ZoneInfo(self.cfg.filters.get("tz", "America/New_York"))
        bank = self.cfg.filters.get("bank", self.cfg.tag)
        items = []
        for r in rss_entries(res.content):
            if not r["link"]:
                continue
            summary = re.sub(r"\s+", " ", HTMLParser(r["summary"] or "<p></p>").text()).strip()
            if is_routine(r["title"]):
                hint, reason = "drop", "routine remarks (title)"
            elif not PRESIDENT.search(summary):
                hint, reason = "drop", f"{bank} Fed official other than the president"
            else:
                hint, reason = WRAP, f"{bank} Fed president speech"
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=r["link"].rstrip("/").rsplit("/", 1)[-1], url=r["link"],
                title=r["title"], source_published_at=wall_time(r["published_raw"], tz),
                published_raw=r["published_raw"], summary=summary or None, document_id=res.document_id,
                meta={"route_hint": hint, "scope_reason": reason, "kind": "Speech", "bank": bank}))
        return items


NY_BASE = "https://www.newyorkfed.org"
NY_ROWS = 40          # newest first; the index holds ~640 speeches back years


def parse_nyfed_index(raw: bytes) -> list[dict]:
    tree = HTMLParser(raw.decode("utf-8", "replace"))
    out, seen = [], set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if not re.match(r"^(?:https://www\.newyorkfed\.org)?/newsevents/speeches/\d{4}/[a-z0-9_-]+$", href):
            continue
        tr = a.parent
        while tr is not None and tr.tag != "tr":
            tr = tr.parent
        m = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", tr.text(separator=" ") if tr is not None else "")
        url = urljoin(NY_BASE, href)
        if url in seen:
            continue
        seen.add(url)
        title = re.sub(r"\s+", " ", a.text(strip=True))
        d = datetime.strptime(" ".join(m.groups()), "%b %d %Y").date() if m else None
        out.append({"url": url, "title": title, "date": d,
                    "speaker": title.split(":", 1)[0].strip() if ":" in title else None})
    return out


class NyFedSpeeches(_BodyMixin, SourceAdapter):
    doc_type = "fed_speeches_ny"
    body_selector = ".ts-article-text"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        keep = {s.lower() for s in (self.cfg.filters.get("keep_speakers") or [])}
        items = []
        for r in parse_nyfed_index(res.content)[:NY_ROWS]:
            topic = r["title"].split(":", 1)[1].strip() if r["speaker"] else r["title"]
            if is_routine(topic):
                hint, reason = "drop", "routine remarks (title)"
            elif keep and (r["speaker"] or "").lower() not in keep:
                hint, reason = "drop", "NY Fed speaker outside filters.keep_speakers"
            else:
                hint, reason = WRAP, "NY Fed speech"
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=r["url"].rsplit("/", 1)[-1], url=r["url"], title=r["title"],
                source_published_at=datetime.combine(r["date"], time(0), tzinfo=ET) if r["date"] else None,
                published_raw=r["date"].strftime("%b %d, %Y") if r["date"] else None, document_id=res.document_id,
                meta={"route_hint": hint, "scope_reason": reason, "kind": "Speech", "speaker": r["speaker"],
                      "date_only": True}))
        return items
