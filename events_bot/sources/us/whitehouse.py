"""White House presidential actions RSS: executive orders, proclamations, memoranda (F-08).

  https://www.whitehouse.gov/presidential-actions/feed/     30 items (~5 weeks), ETag + Last-Modified

Each item carries the full text in content:encoded (the feed is ~500 KB), so the poll is always a conditional
GET (core.fetch sends If-None-Match). The EO / proclamation is posted the evening it is signed, 4-5 days
before the Federal Register (docs/phase0/us_policy.md).

Scope (adapter decides, digest/scope.py `_generic`):
  Nominations & Appointments                    drop
  Proclamations                                 kept only when they act on trade (section 232/301, IEEPA,
                                                HTSUS, duties, tariffs) or name India / H-1B; observances
                                                ("Thanksgiving Day, 2026", "National Adoption Month, 2026")
                                                and other ceremonial proclamations are dropped
  Executive orders, memoranda, determinations   kept, except honours / flags / clemency
  kept items                                    'realtime_if_keyword': real time when title + summary match
                                                delivery.us_realtime_keywords (India, tariff, Section 232, ...),
                                                else the US Morning Wrap
The summary holds the body's trade / India sentences, so "ADJUSTING IMPORTS OF ROBOTICS ..." (section 232,
no keyword in the title) still alerts in real time.
"""
from __future__ import annotations

import re
from email.utils import parsedate_to_datetime

import feedparser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...digest import content
from . import uspol_common as uc

_P = re.compile(r"[?&]p=(\d+)")
NOMINATION = re.compile(r"^Nominations?\b|\bWithdrawals? Sent to the Senate\b|\bAppointments?\b", re.I)
# Observance proclamations: "Thanksgiving Day, 2026", "NATIONAL ADOPTION MONTH, 2026", "Day of Remembrance ...".
CEREMONIAL = re.compile(
    r",\s*\d{4}\s*$|\b(?:Day|Week|Month)\b|\bRemembrance\b|\bHonou?ring\b|\bAnniversary\b|\bin Memory\b|"
    r"\bDeath of\b|\bHalf-Staff\b|\bNational Monument\b|\bHeritage\b|\bObservance\b|\bCommemorat", re.I)
EO_NOISE = re.compile(r"\bHalf-Staff\b|\bFlags?\b|\bMedal\b|\bPosthumous\b|\bDeath of\b|\bPardon|\bCommutation|"
                      r"\bClemency\b|\bHonou?ring\b", re.I)
PROCLAMATION_KEEP = re.compile(r"Adjusting Imports|\bImports? of\b|\bDuties\b|\bTariff|Restriction on (?:the )?Entry|"
                               r"\bTrade\b|\bQuota", re.I)

KIND_OF = {"Executive Orders": "Executive order", "Proclamations": "Proclamation",
           "Presidential Memoranda": "Presidential memorandum", "Nominations & Appointments": "Nomination"}


def parse_feed(content_bytes: bytes) -> list[dict]:
    feed = feedparser.parse(content_bytes)
    out = []
    for e in feed.entries:
        cats = [t.get("term", "") for t in (e.get("tags") or [])]
        html = (e.get("content") or [{}])[0].get("value", "") if e.get("content") else ""
        guid = e.get("id") or e.get("link")
        m = _P.search(guid or "")
        try:
            published = parsedate_to_datetime(e.get("published")) if e.get("published") else None
        except (TypeError, ValueError):
            published = None
        out.append({"ext_id": f"p:{m.group(1)}" if m else guid, "title": re.sub(r"\s+", " ", e.get("title", "")).strip(),
                    "url": e.get("link"), "published": published, "published_raw": e.get("published"),
                    "categories": [c for c in cats if c != "Presidential Actions"],
                    "paragraphs": uc.html_paragraphs(html)})
    return out


def classify(row: dict) -> tuple[str, str, str]:
    """(route_hint, scope_reason, kind) for one feed entry."""
    title, cats, paras = row["title"], row["categories"], row["paragraphs"]
    kind = next((KIND_OF[c] for c in cats if c in KIND_OF), cats[0] if cats else "Presidential action")
    body = " ".join(paras[:200])
    if "Nominations & Appointments" in cats or NOMINATION.search(title):
        return "drop", "White House nomination / appointment", kind
    if "Proclamations" in cats:
        # An observance ("National Manufacturing Day, 2026") may praise tariffs in its body: still ceremonial.
        if PROCLAMATION_KEEP.search(title) or uc.TRADE_RX.search(title):
            return "realtime_if_keyword", "White House trade proclamation (F-08)", kind
        if CEREMONIAL.search(title):
            return "drop", "ceremonial proclamation", kind
        if uc.TRADE_RX.search(body):
            return "realtime_if_keyword", "White House trade proclamation (F-08)", kind
        return "drop", "proclamation outside macro scope", kind
    if EO_NOISE.search(title):
        return "drop", "White House honour / clemency / flag action", kind
    return "realtime_if_keyword", f"White House {kind.lower()} (F-08)", kind


class WhiteHouse(SourceAdapter):
    doc_type = "whitehouse"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_feed(res.content):
            if not r["url"] or not r["title"]:
                continue
            hint, reason, kind = classify(r)
            keys = uc.key_sentences(r["paragraphs"]) if hint != "drop" else []
            lead = next((p for p in r["paragraphs"] if len(p) >= 80), "")
            summary = " ".join(([lead[:400]] if lead else []) + [k for k in keys if k not in lead]) or None
            tags = ["wh:trade"] if hint != "drop" and uc.TRADE_RX.search(f"{r['title']} {summary or ''}") else []
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=r["ext_id"], url=r["url"], title=r["title"],
                source_published_at=r["published"], published_raw=r["published_raw"],
                document_id=res.document_id, summary=summary,
                meta={"kind": kind, "categories": r["categories"], "route_hint": hint, "scope_reason": reason,
                      "priority_tags": tags}))
        return items

    def digest_body(self, it: RawItem) -> content.Body:
        """The action's own page: `.entry-content` holds the full signed text (heading, preamble, sections)."""
        r = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:whitehouse_page", expect="html", conditional=False,
                                            parent_id=it.document_id)
        text = uc.html_body_first(r.content, (".entry-content", ".wp-block-post-content", "main"))
        return uc.body(text, it.url, r.document_id)
