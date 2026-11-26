"""NITI Aayog: reports, discussion / policy papers and report-launch press releases (Tier 3, weekly digest).

Feed: https://www.niti.gov.in/rss.xml (Drupal, site-wide, 10 items, about two weeks deep; Phase 0
india_t23.md). The feed mixes several Drupal node types; each item's <description> is the rendered node,
whose field labels say what it is (verified 2026-11-26):

  "Frontier Type <Articles|Discussion Papers|...>"  Frontier Tech Hub. Articles are op-eds NITI links to on
                                                    newspaper sites (drop); papers carry a NITI PDF (keep)
  "Choose Document Type" (TOC / Docplan)            library "table of contents" alerts (drop)
  "Release Link" -> pib.gov.in PRID                 press release, hosted on PIB (keep only report / paper /
                                                    index launches; drop workshops, visits, MoUs, campaigns)
  "Links" -> /sites/default/files/...pdf            "What's new" publication = the report itself (keep)

Every item has an RFC-822 pubDate (UTC). The item id is the Drupal node id from the guid ("2467 at ...").
Desk scope: Tier 3, so kept items route to the Saturday Weekly Digest ('weekly'); the rest is 'drop'.
"""
from __future__ import annotations

import html as _html
import re
from email.utils import parsedate_to_datetime

import feedparser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...digest import content

PIB_PAGE = "https://www.pib.gov.in/PressReleasePage.aspx?PRID={}&reg=3&lang=1"
_NID = re.compile(r"^(\d+)\s+at\s")
_HREF = re.compile(r'href="([^"]+)"')
_PRID = re.compile(r"PRID=(\d+)")
_FRONTIER = re.compile(r"Select Frontier Type\s+(.+?)(?:\s+Frontier Link|\s*$)")

# A press release is kept only when it launches a publication or an index.
_PR_KEEP = re.compile(
    r"\b(?:report|paper|index|indices|framework|roadmap|road map|strategy|policy|blueprint|outlook|vision|"
    r"assessment|study|ArthNITI|Trade Watch|Fiscal Health|Quarterly|dashboard|white paper)\b", re.I)
_DROP = re.compile(
    r"workshop|visit|\bMoU\b|memorandum of understanding|conclave|felicitat|inaugurat|campaign|celebrat|"
    r"swachh|yoga|hindi pakhwada|karmayogi|meeting of|delegation|webinar|hackathon|award|"
    r"recruit|vacanc|advertisement|tender|empanel|internship|deputation|\bEOI\b|expression of interest|"
    r"\bRFP\b|corrigendum|walk-in|shortlist", re.I)


def _text(desc_html: str) -> str:
    t = re.sub(r"<[^>]+>", " ", desc_html)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def _https(url: str) -> str:
    return re.sub(r"^http://", "https://", url.strip())


def classify(title: str, desc_html: str) -> dict:
    """Node type + desk scope of one feed item. Pure."""
    text = _text(desc_html)
    hrefs = [_html.unescape(h) for h in _HREF.findall(desc_html)]
    pdfs = [_https(h) for h in hrefs if h.lower().endswith(".pdf")]
    out: dict = {"pdf_url": pdfs[0] if pdfs else None}
    if "Frontier Type" in text or "Frontier Image" in text:
        m = _FRONTIER.search(text)
        ftype = (m.group(1).strip() if m else "").strip()
        out["kind"] = ftype.rstrip("s") if ftype else "Frontier Tech item"
        if re.match(r"articles?\b", ftype, re.I) or not pdfs:
            return {**out, "route_hint": "drop", "scope_reason": "NITI Frontier Tech: external op-ed link"}
        return {**out, "route_hint": "weekly", "scope_reason": f"NITI {ftype or 'paper'} (Tier 3)"}
    if "Choose Document Type" in text:
        return {**out, "kind": "Library alert", "route_hint": "drop",
                "scope_reason": "NITI library table-of-contents / docplan alert"}
    if "Release Link" in text:
        prid = next((m.group(1) for h in hrefs if (m := _PRID.search(h))), None)
        out.update(kind="Press release", release_url=PIB_PAGE.format(prid) if prid else None, prid=prid)
        if _DROP.search(title) or not _PR_KEEP.search(title):
            return {**out, "route_hint": "drop", "scope_reason": "NITI press release: event / visit / MoU"}
        return {**out, "route_hint": "weekly", "scope_reason": "NITI report / paper launch (Tier 3)"}
    if pdfs:
        out["kind"] = "Report"
        if _DROP.search(title):
            return {**out, "route_hint": "drop", "scope_reason": "NITI administrative notice"}
        return {**out, "route_hint": "weekly", "scope_reason": "NITI publication (Tier 3)"}
    return {**out, "kind": "Site item", "route_hint": "drop", "scope_reason": "NITI site item outside report scope"}


def parse_feed(data: bytes) -> list[dict]:
    rows = []
    for e in feedparser.parse(data).entries:
        m = _NID.match(e.get("id", "") or e.get("guid", ""))
        nid = m.group(1) if m else (e.get("link", "").rstrip("/").rsplit("/", 1)[-1])
        raw = e.get("published", "")
        try:
            when = parsedate_to_datetime(raw) if raw else None
        except (TypeError, ValueError):
            when = None
        desc = e.get("summary", "") or e.get("description", "")
        title = re.sub(r"\s+", " ", e.get("title", "")).strip()
        rows.append({"nid": nid, "title": title, "url": _https(e.get("link", "")), "published": when,
                     "published_raw": raw or None, **classify(title, desc)})
    return rows


class Niti(SourceAdapter):
    doc_type = "niti"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_feed(res.content):
            meta = {k: r[k] for k in ("kind", "route_hint", "scope_reason", "pdf_url") if r.get(k)}
            if r.get("release_url"):
                meta["release_url"], meta["prid"] = r["release_url"], r["prid"]
            items.append(RawItem(source_id=self.cfg.id, ext_id=f"node:{r['nid']}", url=r["url"], title=r["title"],
                                 source_published_at=r["published"], published_raw=r["published_raw"],
                                 document_id=res.document_id, meta=meta))
        return items

    def digest_body(self, it: RawItem) -> content.Body:
        """Press release: the PIB release page (NITI's releases live on PIB). Report / paper: the PDF."""
        f, meta = self.ctx.fetcher, it.meta or {}

        def get(url: str, expect: str, doc_type: str):
            return f.get_with_retry(url, tries=3, base_delay=4, source_id=self.cfg.id, doc_type=f"digest:{doc_type}",
                                    expect=expect, conditional=False)

        if meta.get("release_url"):
            r = get(meta["release_url"], "html", "niti_pib_page")
            text = content.pib_body(r.content)
            return content.Body(text, content._paragraphs(text), it.url, None, r.document_id,
                                posted=content.pib_posted(r.content))
        if meta.get("pdf_url"):
            r = get(meta["pdf_url"], "pdf", "niti_pdf")
            text = content.pdf_body(r.content)
            notes = [] if len(text) > 200 else ["The PDF has no text layer (scanned image); see the PDF."]
            return content.Body(text, content._paragraphs(text), it.url, meta["pdf_url"], r.document_id, notes=notes)
        r = get(it.url, "html", "niti_page")
        text = content.html_body(r.content, "main")
        return content.Body(text, content._paragraphs(text), it.url, None, r.document_id)
