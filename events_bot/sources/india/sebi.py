"""SEBI: circulars, consultation papers, press releases (incl. Board meeting outcomes).

The SEBI RSS is batch-built and hours stale (Phase 0: 0 of 22 same-day items at 17:30), so the
server-rendered listing pages are polled instead (25 rows each, newest first, same-day):
  circulars     HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0
  consultation  HomeAction.do?doListing=yes&sid=4&ssid=38&smid=35
  press         HomeAction.do?doListing=yes&sid=6&ssid=23&smid=0
Rows carry the date only (no time); the item id is the trailing number of the detail URL.
Board outcomes ("Key decisions taken in the SEBI Board Meeting dated ...") are tagged board_outcome.
"""
from __future__ import annotations

import re
from datetime import datetime, time

from selectolax.parser import HTMLParser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST

_ID = re.compile(r"_(\d+)\.html?$")
_CLICK = re.compile(r"\s*Click here to provide your comments.*$", re.I)
KIND = {"circulars": "Circular", "consultation": "Consultation paper", "press": "Press release"}


def parse_listing(content: bytes) -> list[dict]:
    tree = HTMLParser(content.decode("utf-8", "replace"))
    t = tree.css_first("table#sample_1")
    out = []
    for tr in (t.css("tr")[1:] if t else []):
        tds = tr.css("td")
        a = tr.css_first("a")
        if not tds or a is None:
            continue
        href = a.attributes.get("href") or ""
        m = _ID.search(href)
        if not m:
            continue
        title = _CLICK.sub("", (a.attributes.get("title") or a.text(strip=True)).strip())
        try:
            d = datetime.strptime(tds[0].text(strip=True), "%b %d, %Y").date()
        except ValueError:
            d = None
        number = tds[1].text(strip=True) if len(tds) == 3 else ""
        out.append({"id": int(m.group(1)), "date": d, "title": re.sub(r"\s+", " ", title), "url": href,
                    "number": number})
    return out


class Sebi(SourceAdapter):
    doc_type = "sebi"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_listing(res.content):
            tags = []
            if re.search(r"Key decisions taken in the SEBI Board Meeting", r["title"], re.I):
                tags.append("board_outcome")
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"id:{r['id']}", url=r["url"], title=r["title"],
                # date-only source stamp: midnight IST of the listed day, raw string kept verbatim
                source_published_at=datetime.combine(r["date"], time(0, 0), tzinfo=IST) if r["date"] else None,
                published_raw=r["date"].strftime("%b %d, %Y") if r["date"] else None,
                document_id=res.document_id,
                meta={"kind": KIND.get(url_name, url_name), "number": r["number"], "feed": url_name,
                      "date_only": True, "priority_tags": tags}))
        return items
