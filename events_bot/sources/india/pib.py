"""Press Information Bureau (English, Delhi): every Government of India release.

Two listings, merged by PRID (Phase 0, FEED_MAP):
- allRel.aspx: every release of the day grouped under <h3 class='font104'> ministry headings, so
  the ministry tag (the main filter signal) comes with no per-item fetch. Complete for the day.
- RssMain.aspx (ModId=6, Lang=1, Regid=3, &reg=3 -- without &reg=3 it redirects to Hindi): fast but
  only 20 items, title + link, no time and no ministry. Covers the midnight roll-over of allRel.

Only items that survive the filter are enriched from the release page (#MinistryName, #Titleh2,
#PrDateTime "Posted On: 07 OCT 2026 10:48AM by PIB Delhi" = IST, body text for the excerpt and the
LLM's extractive pick). Hard rejects never cost a request.
"""
from __future__ import annotations

import re
from datetime import datetime

import feedparser
from selectolax.parser import HTMLParser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST

PAGE = "https://www.pib.gov.in/PressReleasePage.aspx?PRID={}&reg=3&lang=1"
_PRID = re.compile(r"PRID=(\d+)", re.I)
_POSTED = re.compile(r"Posted On:\s*(\d{1,2} [A-Z]{3} \d{4} \d{1,2}:\d{2}\s*[AP]M)", re.I)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_allrel(content: bytes) -> list[tuple[int, str, str]]:
    """[(prid, ministry, title)] in page order."""
    tree = HTMLParser(content.decode("utf-8", "replace"))
    out = []
    for h3 in tree.css("h3.font104"):
        ministry = _clean(h3.text())
        ul = h3.next
        while ul is not None and ul.tag != "ul":
            ul = ul.next
        if ul is None:
            continue
        for a in ul.css("a"):
            m = _PRID.search(a.attributes.get("href") or "")
            if m:
                out.append((int(m.group(1)), ministry, _clean(a.attributes.get("title") or a.text())))
    return out


def parse_posted(s: str) -> datetime | None:
    m = _POSTED.search(s or "")
    if not m:
        return None
    s = re.sub(r"\s*([AP]M)$", r"\1", re.sub(r"\s+", " ", m.group(1)).upper())   # "10:48AM" / "10:48 AM"
    return datetime.strptime(s, "%d %b %Y %I:%M%p").replace(tzinfo=IST)


def parse_release(content: bytes) -> dict:
    tree = HTMLParser(content.decode("utf-8", "replace"))

    def t(sel: str) -> str:
        n = tree.css_first(sel)
        return _clean(n.text(separator=" ")) if n else ""

    body_node = tree.css_first(".innner-page-main-about-us-content-right-part") or tree.css_first("#PdfDiv")
    paras: list[str] = []
    if body_node is not None:
        for p in body_node.css("p, li"):
            x = _clean(p.text(separator=" "))
            if x and not x.startswith("Posted On") and x not in paras:
                paras.append(x)
    return {"ministry": t("#MinistryName"), "title": t("#Titleh2"), "posted_raw": t("#PrDateTime"),
            "posted": parse_posted(t("#PrDateTime")), "paragraphs": paras}


class Pib(SourceAdapter):
    doc_type = "pib"

    def poll(self) -> list[RawItem]:
        merged: dict[str, RawItem] = {}
        for name in ("allrel", "rss"):
            url = self.cfg.urls[name]
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"pib:{name}",
                                       expect="html" if name == "allrel" else "xml", conditional=name == "rss")
            if res.not_modified:
                continue
            for it in self.parse(res, name):
                prev = merged.get(it.ext_id)
                if prev is None:
                    merged[it.ext_id] = it
                elif not prev.meta.get("ministry") and it.meta.get("ministry"):
                    prev.meta["ministry"] = it.meta["ministry"]
        return list(merged.values())

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        if url_name == "allrel":
            for prid, ministry, title in parse_allrel(res.content):
                items.append(self._item(prid, title, ministry, res))
        else:
            for e in feedparser.parse(res.content).entries:
                m = _PRID.search(e.get("link", ""))
                if m:
                    items.append(self._item(int(m.group(1)), _clean(e.get("title", "")), "", res))
        return items

    def _item(self, prid: int, title: str, ministry: str, res: FetchResult) -> RawItem:
        return RawItem(source_id=self.cfg.id, ext_id=f"prid:{prid}", url=PAGE.format(prid), title=title,
                       document_id=res.document_id, meta={"ministry": ministry, "prid": prid,
                                                          "feed": "allrel" if ministry else "rss"})

    def enrich(self, it: RawItem) -> RawItem:
        """Release page: ministry, Posted On (IST), body text. Called only for kept/uncertain items."""
        res = self.ctx.fetcher.get(it.url, source_id=self.cfg.id, doc_type="pib:release", expect="html",
                                   conditional=False, parent_id=it.document_id)
        r = parse_release(res.content)
        meta = {**it.meta, "ministry": r["ministry"] or it.meta.get("ministry", ""), "posted_raw": r["posted_raw"],
                "paragraphs": r["paragraphs"][:40], "release_document_id": res.document_id}
        return it.model_copy(update={"title": r["title"] or it.title, "source_published_at": r["posted"],
                                     "published_raw": r["posted_raw"],
                                     "summary": " ".join(r["paragraphs"][:3]) or it.summary, "meta": meta})
