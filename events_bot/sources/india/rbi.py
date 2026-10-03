"""RBI press-release and notification RSS (FEED_MAP: verified 2026-10-01, lag <= 2 min).

Feed quirks (FLAGS T-03, T-04):
- pubDate is naive IST ("Fri, 02 Oct 2026 17:05:00"); feedparser returns None for it.
- Only 10 items deep (about a day of press releases). prid is sequential, so a gap
  after an outage can be backfilled by id (Phase 4).
- The body starts with a UTF-8 BOM.
"""
from __future__ import annotations

import re

import feedparser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import parse_rbi_pubdate

_ID = re.compile(r"[?&](prid|id)=(\d+)", re.I)


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
            out.append(RawItem(
                source_id=self.cfg.id, ext_id=f"{m.group(1).lower()}:{m.group(2)}",
                url=link, title=re.sub(r"\s+", " ", e.get("title") or "").strip(),
                source_published_at=parse_rbi_pubdate(raw_pub), published_raw=raw_pub,
                document_id=res.document_id, summary=e.get("summary"),
                meta={"feed": url_name}))
        return out
