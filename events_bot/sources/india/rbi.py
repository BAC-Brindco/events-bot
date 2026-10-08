"""RBI press-release and notification RSS (FEED_MAP: verified 2026-10-01, lag <= 2 min).

Feed quirks (FLAGS T-03, T-04):
- pubDate is naive IST ("Fri, 02 Oct 2026 17:05:00"); feedparser returns None for it.
- Only 10 items deep. Press releases cover about a day; notifications come in bursts (7 Oct 2026:
  8 Directions at 17:25-17:40), so one burst can push items out between two polls.
- The body starts with a UTF-8 BOM.

Gap backfill: prid and notification Id are sequential. When the oldest id in the feed is more than one
past the newest id already stored, the missing ids are fetched one by one from their display pages
(an unpublished id returns HTTP 200 with an empty shell, so a page counts only when `.tablebg` exists).
Those pages carry a date but no time, so backfilled items are date-only.

Classification (title rules, Phase 0 §d): master directions and Directions, amendment directions, drafts
for comment, policy-rate notices and the monthly Bulletin get priority tags. Routine market-operation
press releases (VRRR/VRR results, Money Market Operations, auction results) are excluded by the rbi_pr
keyword file and logged to filtered_items.
"""
from __future__ import annotations

import re
from datetime import datetime, time

import feedparser
from selectolax.parser import HTMLParser

from ...core.fetch import FetchError
from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST, parse_rbi_pubdate

_ID = re.compile(r"[?&](prid|id)=(\d+)", re.I)
PAGES = {"prid": "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={}",
         "id": "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id={}&Mode=0"}
MAX_BACKFILL = 25

_RULES: list[tuple[str, re.Pattern]] = [
    ("rbi:draft", re.compile(r"\bdraft\b", re.I)),
    ("rbi:master_direction", re.compile(r"\bmaster direction", re.I)),
    ("rbi:amendment_directions", re.compile(r"\bamendment directions?\b", re.I)),
    ("rbi:directions", re.compile(r"\bdirections?,\s*20\d\d\b|\bissues (?:amendment )?directions\b", re.I)),
    ("rbi:policy_rates", re.compile(r"change in (?:the )?(?:bank )?rates?\b|\bpolicy repo rate\b", re.I)),
    ("rbi:bulletin", re.compile(r"\bRBI Bulletin\b", re.I)),
    ("rbi:enforcement", re.compile(r"monetary penalty|cancels? (?:the )?(?:certificate|licence|license)|"
                                   r"imposes (?:business )?restrictions|all-inclusive directions", re.I)),
]


def classify(title: str) -> list[str]:
    tags = [tag for tag, rx in _RULES if rx.search(title)]
    if "rbi:amendment_directions" in tags and "rbi:directions" in tags:
        tags.remove("rbi:directions")
    return tags


_DATE_PR = re.compile(r"Date\s*:\s*([A-Z][a-z]{2} \d{1,2}, \d{4})")
_DATE_LONG = re.compile(r"\b((?:January|February|March|April|May|June|July|August|September|October|"
                        r"November|December) \d{1,2}, \d{4})\b")


def parse_page(content: bytes) -> dict | None:
    """A press-release or notification display page -> {title, date}; None for an unpublished id."""
    tree = HTMLParser(content.decode("utf-8", "replace"))
    tb = tree.css_first(".tablebg")
    if tb is None:
        return None
    heads = [re.sub(r"\s+", " ", td.text(strip=True)) for td in tb.css("td.tableheader")]
    title, d = "", None
    for h in heads:
        if re.fullmatch(r"\(\s*[\d.]+\s*[kKmM][bB]\s*\)", h):
            continue
        m = _DATE_PR.search(h)
        if m:
            d = datetime.strptime(m.group(1), "%b %d, %Y").date()
            continue
        title = title or h
    if not title:
        return None
    if d is None:
        m = _DATE_LONG.search(tb.text(separator=" "))
        if m:
            d = datetime.strptime(m.group(1), "%B %d, %Y").date()
    return {"title": title, "date": d}


class RbiRss(SourceAdapter):
    doc_type = "rss"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        fp = feedparser.parse(res.content)
        out: list[RawItem] = []
        for e in fp.entries:
            link = (e.get("link") or "").strip()
            m = _ID.search(link)
            if not link or not m:
                continue
            raw_pub = e.get("published")
            title = re.sub(r"\s+", " ", e.get("title") or "").strip()
            out.append(RawItem(
                source_id=self.cfg.id, ext_id=f"{m.group(1).lower()}:{m.group(2)}",
                url=link, title=title,
                source_published_at=parse_rbi_pubdate(raw_pub), published_raw=raw_pub,
                document_id=res.document_id, summary=e.get("summary"),
                meta={"feed": url_name, "priority_tags": classify(title)}))
        return out

    # ---- gap backfill -------------------------------------------------------
    def poll(self) -> list[RawItem]:
        items = super().poll()
        if items and self.ctx.db is not None:
            try:
                items += self.backfill(items)
            except FetchError:
                pass  # the feed items still go through; the next poll retries the gap
        return items

    def _max_known(self, kind: str) -> int | None:
        row = self.ctx.db.one(
            "select max(split_part(ext_id, ':', 2)::bigint) as m from items "
            "where source_id = %s and ext_id like %s", (self.cfg.id, f"{kind}:%"))
        return row["m"] if row else None

    def backfill(self, items: list[RawItem]) -> list[RawItem]:
        """Fetch ids skipped between the last stored id and the oldest new id in this feed snapshot."""
        by_kind: dict[str, list[int]] = {}
        for it in items:
            k, n = it.ext_id.split(":", 1)
            if n.isdigit():
                by_kind.setdefault(k, []).append(int(n))
        out: list[RawItem] = []
        for kind, ids in by_kind.items():
            known = self._max_known(kind)
            if known is None or kind not in PAGES:
                continue          # first poll of the source is the baseline: nothing to backfill
            # Only the run of ids above the stored maximum matters: the feed also carries old ids
            # (re-posted circulars), which must not trigger a backfill.
            recent = sorted(i for i in ids if i > known)
            if not recent:
                continue
            have = set(ids)
            missing = [i for i in range(known + 1, recent[0]) if i not in have][-MAX_BACKFILL:]
            for n in missing:
                url = PAGES[kind].format(n)
                res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"rbi:{kind}_page",
                                           expect="html", conditional=False)
                p = parse_page(res.content)
                if p is None:
                    continue
                out.append(RawItem(
                    source_id=self.cfg.id, ext_id=f"{kind}:{n}", url=url, title=p["title"],
                    source_published_at=datetime.combine(p["date"], time(0, 0), tzinfo=IST) if p["date"] else None,
                    published_raw=p["date"].strftime("%b %d, %Y") if p["date"] else None,
                    document_id=res.document_id,
                    meta={"feed": "backfill", "date_only": True, "priority_tags": classify(p["title"])}))
        return out
