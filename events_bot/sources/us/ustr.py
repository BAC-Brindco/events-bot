"""USTR press releases (F-08). No RSS anywhere on ustr.gov (docs/phase0/us_policy.md), so the server-rendered
listing is polled; it sends an ETag, so most polls are a 304.

  https://ustr.gov/about-us/policy-offices/press-office/press-releases     newest ~30, "YYYY-MM-DD <br> <a>"

Item links use both the /about/ and the /about-us/ prefix; the item id is the path after "press-releases/"
(e.g. 2026/november/ustr-makes-determinations-...), so the same release under either prefix is one item.
Rows carry a date only.

Scope: trade actions -> 'realtime_if_keyword' (real time on India / tariff / Section 232 / Section 301, else the
US Morning Wrap). Readouts, remarks, tours, awards, op-eds and the USMCA Rapid Response labour cases carry no
action and are dropped, unless the release itself names India: `enrich` reads the release page (archived) and
puts its India / trade sentences into the summary, so the realtime keyword check sees the body too
("... 14 economies ... and India" in a Section 301 determination).
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...digest import content
from . import uspol_common as uc

BASE = "https://ustr.gov"
_DATE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\b")
_PATH = re.compile(r"/press-releases/(\d{4}/[^?#]+?)/?$")
ROUTINE = re.compile(
    r"^Readout\b|\bReadout of\b|\bRemarks\b|\bto Tour\b|\bTouring\b|\bAward\b|\bLetter to the Editor\b|\bOp-?Ed\b|"
    r"\bWorkers.? Rights\b|\bRapid Response Labor Mechanism\b|\bMemorandum of Understanding at\b|"
    r"\bCourse of Remediation\b|\bto Travel\b|\bwill Travel\b|\bMeets? with\b|\bMeeting with\b|\bCall with\b|"
    r"\bWelcomes?\b|\bCongratulates?\b|\bStatement on the (?:Passing|Death)\b",
    re.I)
BODY_SELECTORS = (".field--name-body", "article", "#main")


def parse_listing(content_bytes: bytes) -> list[dict]:
    tree = HTMLParser(content_bytes.decode("utf-8", "replace"))
    out, seen = [], set()
    for li in tree.css("li"):
        a = li.css_first("a[href]")
        if a is None:
            continue
        href = a.attributes.get("href") or ""
        m = _PATH.search(href)
        d = _DATE.match(li.text(separator=" ", strip=False))
        if not m or not d:
            continue
        key = m.group(1)
        if key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "date": datetime.strptime(d.group(1), "%Y-%m-%d").date(), "raw_date": d.group(1),
                    "title": re.sub(r"\s+", " ", a.text(separator=" ")).strip(), "url": urljoin(BASE, href)})
    return out


def classify(title: str) -> tuple[str, str]:
    if ROUTINE.search(title) and not uc.INDIA_RX.search(title):
        return "drop", "USTR readout / remarks / labour case with no trade action"
    return "realtime_if_keyword", "USTR trade release (F-08)"


class Ustr(SourceAdapter):
    doc_type = "ustr"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_listing(res.content):
            hint, reason = classify(r["title"])
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=r["key"], url=r["url"], title=r["title"],
                source_published_at=uc.et_midnight(r["date"]), published_raw=r["raw_date"],
                document_id=res.document_id,
                meta={"kind": "Press release", "date_only": True, "route_hint": hint, "scope_reason": reason,
                      "priority_tags": ["ustr:section301"] if re.search(r"Section 301", r["title"], re.I) else []}))
        return items

    @staticmethod
    def apply_page(it: RawItem, page: bytes, document_id: int | None = None) -> RawItem:
        """Pure: the release page's India / trade sentences into summary; a routine readout that names India
        is kept after all."""
        text = uc.html_body_first(page, BODY_SELECTORS)
        paras = [p for p in text.split("\n") if p.strip()]
        keys = uc.key_sentences(paras)
        lead = next((p for p in paras if len(p) >= 80), "")
        summary = " ".join(([lead[:400]] if lead else []) + [k for k in keys if k not in lead]) or it.summary
        meta = {**it.meta, "page_document_id": document_id}
        india = bool(uc.INDIA_RX.search(text))
        if india:
            meta["priority_tags"] = sorted(set(meta.get("priority_tags") or []) | {"ustr:india"})
            if meta.get("route_hint") == "drop":
                meta["route_hint"], meta["scope_reason"] = "realtime_if_keyword", "USTR release naming India"
        return it.model_copy(update={"summary": summary, "meta": meta})

    def enrich(self, it: RawItem) -> RawItem:
        r = self.ctx.fetcher.get(it.url, source_id=self.cfg.id, doc_type="ustr:release", expect="html",
                                 conditional=False, parent_id=it.document_id)
        return self.apply_page(it, r.content, r.document_id)

    def digest_body(self, it: RawItem) -> content.Body:
        r = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:ustr_page", expect="html", conditional=False,
                                            parent_id=it.document_id)
        return uc.body(uc.html_body_first(r.content, BODY_SELECTORS), it.url, r.document_id)
