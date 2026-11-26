"""Monthly auto sales: SIAM (wholesale / production) and FADA (retail registrations). Digest section
"Industry data", 18:30 IST Daily Macro Digest (route india_eod).

Phase 0 (docs/phase0/india_t23.md), re-verified 2026-11-26:
  siam   /news-&-updates/press-releases   ASP.NET, server-rendered cards: "dd-Mon-yyyy", <h6> title, detail link
                                          /news-&-updates/press-releases/<slug>/<id> (sequential id). The free
                                          "Auto Industry Performance of <Month-YYYY>" release carries the
                                          headline numbers (~13th-15th). Slow host (16-18 s per page).
  fada   /press-release-list.php          PHP behind Cloudflare (plain httpx fine). Cards with month-only
                                          dates ("November, 2026") and a PDF whose name has an unpredictable
                                          14-hex prefix. The monthly "Vehicle Retail Data" PDF (~6th-7th)
                                          carries the headline numbers.
Only the sales releases are kept; events, awards, partnerships and office-bearer news are dropped.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from ...core.models import FetchResult, RawItem
from .regulators import EOD, _Regulator, _date, _midnight, _tree, _ws

SIAM = "https://www.siam.in"
FADA = "https://www.fada.in/"
_SIAM_ID = re.compile(r"/press-releases/[^/]+/(\d+)/?$")
_SIAM_KEEP = re.compile(r"Auto Industry (?:Sales )?Performance", re.I)
_FADA_KEEP = re.compile(r"Vehicle Retail Data|Retail Sales", re.I)
_FADA_HEX = re.compile(r"press-release/([0-9a-f]{14})", re.I)


def siam_parse(content_: bytes) -> list[dict]:
    out = []
    for box in _tree(content_).css(".pressbx_large, .pressbx_small, .press_grid_one > div, .press_grid_two > div"):
        a = box.css_first('a[href*="/press-releases/"]')
        h = box.css_first("h6")
        if a is None or h is None:
            continue
        m = _SIAM_ID.search(a.attributes.get("href") or "")
        if not m:
            continue
        lis = [_ws(li.text()) for li in box.css(".press_caption li")]
        raw = next((x for x in lis if re.match(r"\d{2}-[A-Za-z]{3}-\d{4}$", x)), "")
        out.append({"id": int(m.group(1)), "title": _ws(h.text()), "raw": raw, "date": _date(raw, "%d-%b-%Y"),
                    "url": urljoin(SIAM, a.attributes["href"])})
    seen, uniq = set(), []
    for r in out:
        if r["id"] not in seen:
            seen.add(r["id"])
            uniq.append(r)
    return uniq


def fada_parse(content_: bytes) -> list[dict]:
    out = []
    for c in _tree(content_).css(".card-body"):
        h = c.css_first("h3")
        a = c.css_first('a[href*="press-release/"]')
        if h is None or a is None:
            continue
        href = a.attributes["href"]
        m = _FADA_HEX.search(href)
        month = c.css_first(".item7-card-desc a")
        raw = _ws(month.text()) if month else ""
        mo = _date(raw, "%B, %Y")
        out.append({"key": m.group(1) if m else href, "title": _ws(h.text()), "raw": raw, "month": mo,
                    "url": quote(urljoin(FADA, href), safe=":/?&=%#;,+@!$'()*~")})
    return out


class Siam(_Regulator):
    doc_type = "siam"
    body_selector = ".press_details, .press_inner, .container, body"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in siam_parse(res.content):
            keep = _SIAM_KEEP.search(r["title"])
            sc = (EOD, "SIAM monthly auto industry performance") if keep else \
                ("drop", "SIAM release other than the monthly sales performance")
            items.append(self._item(ext_id=f"pr:{r['id']}", url=r["url"], title=r["title"],
                                    when=_midnight(r["date"]), raw=r["raw"], res=res, scope=sc,
                                    kind="Monthly sales release" if keep else "Press release"))
        return items

    def digest_body(self, it: RawItem):
        from ...digest import content
        r = self._get(it.url, "html", "page")
        text = ""
        for sel in self.body_selector.split(", "):
            try:
                text = content.html_body(r.content, sel)
            except ValueError:
                continue
            if len(text) > 300:
                break
        return content.Body(text, content._paragraphs(text), it.url, None, r.document_id)


class Fada(_Regulator):
    """The listing gives the month only; the PDF dateline ("06th November'26") is the release date, so items
    are date-less (source_published_at None) with meta month."""
    doc_type = "fada"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in fada_parse(res.content):
            keep = _FADA_KEEP.search(r["title"])
            sc = (EOD, "FADA monthly vehicle retail data") if keep else \
                ("drop", "FADA release other than vehicle retail data")
            items.append(self._item(ext_id=f"pdf:{r['key']}", url=r["url"], title=r["title"], when=None,
                                    raw=r["raw"], res=res, scope=sc, date_only=False,
                                    kind="Monthly retail data" if keep else "Press release",
                                    month=r["month"].strftime("%Y-%m") if r["month"] else None))
        return items

