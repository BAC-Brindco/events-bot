"""IMD press releases: monsoon long-range forecasts, seasonal and monthly outlooks, monsoon onset/withdrawal.

  https://internal.imd.gov.in/pages/press_release_mausam.php     one 4 MB page, about 6,800 rows, newest first

Rows: S.No., Id (sequential), Date ("07 Oct 2026"), Subject, PDF link (../press_release/YYYYMMDD_pr_N.pdf).
English and Hindi releases are separate rows; the Hindi duplicates are skipped. Most rows are the daily
weather bulletin ("PRESS RELEASE DATE: 07TH OCTOBER 2026"), which the imd keyword file does not keep.

Latency: Phase 0 measured the April 2026 LRF stage 1 listed about 44 h after IMD's press conference, so
this page is a backstop; PIB (MoES) usually carries the forecast the same day. No ETag/Last-Modified, so the
source is throttled to one fetch an hour in `tick` (tick_every_minutes).
"""
from __future__ import annotations

import re
from datetime import datetime, time
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST

PAGE = "https://internal.imd.gov.in/pages/press_release_mausam.php"
NEWEST = 80          # rows read per poll; a week is about 20
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def parse_list(content: bytes, newest: int = NEWEST) -> list[dict]:
    # The table is near the top of a 4 MB page; parsing only the head keeps this fast.
    head = content[: 400_000].decode("utf-8", "replace")
    tree = HTMLParser(head)
    out = []
    for tr in tree.css("tr"):
        tds = tr.css("td")
        if len(tds) < 5:
            continue
        pid, raw_date = tds[1].text(strip=True), tds[2].text(strip=True)
        a = tr.css_first("a[href]")
        if not pid.isdigit() or a is None:
            continue
        subject = re.sub(r"\s+", " ", tds[3].text(strip=True)).strip()
        try:
            d = datetime.strptime(raw_date, "%d %b %Y").date()
        except ValueError:
            d = None
        out.append({"id": int(pid), "date": d, "raw_date": raw_date, "subject": subject,
                    "url": urljoin(PAGE, a.attributes.get("href") or ""), "hindi": bool(_DEVANAGARI.search(subject))})
        if len(out) >= newest:
            break
    return out


class ImdPress(SourceAdapter):
    doc_type = "imd"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_list(res.content):
            if r["hindi"] or not r["subject"]:
                continue
            tags = []
            if re.search(r"LONG RANGE FORECAST|SEASONAL OUTLOOK|MONSOON", r["subject"], re.I):
                tags.append("imd:monsoon")
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"pr:{r['id']}", url=r["url"], title=r["subject"],
                source_published_at=datetime.combine(r["date"], time(0, 0), tzinfo=IST) if r["date"] else None,
                published_raw=r["raw_date"], document_id=res.document_id,
                meta={"kind": "Press release", "date_only": True, "priority_tags": tags}))
        return items
