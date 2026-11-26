"""Election Commission of India press notes (Tier 3, weekly digest): poll schedules and results only.

Source: the eci.gov.in React SPA's own JSON backend (not a published API; Phase 0 india_t23.md), the
"Press Releases" list the site renders at /issue-details-page/press-releases:

  https://www.eci.gov.in/eci-backend/public/api/get-file?categories=<enc("11")>          (page 1, 20 rows)

Query values are encrypted by the front end: AES-128-ECB, PKCS7, base64, then URL-encoded, with the key
hard-coded in the site's main.<hash>.js (`function yl(e)`, key "4WS8851W824R456Y" on 2026-11-26).
enc("11") = "Y6fPfuS/XeuZJeu7qk0stw==". If ECI rotates the key or the category id, the backend answers
with something that is not the expected list; `parse` then raises, so the source's health check fires
(it never silently yields nothing). Re-derive the token from the new main.js `yl` key.

Each row: id, document_title (often just "PRESS NOTE"), reference_number ("ECI/PN/122/2026"),
date_of_creation ("Tuesday 06 Oct 2026, 9:15 PM", IST) and document_attachments[].record_location, the
PDF (`/api/download?url=<enc path>`, verified 200 application/pdf on 2026-11-26).

Desk scope: keep the announcement of a general election's schedule (state assemblies, Lok Sabha,
President / Vice-President) and results / counting summaries; drop everything else (bye-elections,
Council of States / Legislative Council polls, SIR roll revision, seizures, observers, conferences, media).
Generic titles ("PRESS NOTE") are classified in `enrich` from the PDF's heading lines.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST
from ...digest import content

_GENERIC = re.compile(r"^\s*(?:press\s*note|press\s*release|pn[-\s]?\d*)\s*[.:]?\s*$", re.I)

# Schedule announcements. ECI titles the schedule note with just the election's name, e.g.
# "General Election to the Legislative Assemblies of Assam, Kerala, Tamil Nadu, West Bengal and
# Puducherry, 2026" (PN-029, 15 Mar 2026); follow-ups carry a suffix ("...: Seizures surpass", "- Media
# Coverage ...", "-Allotment of Broadcast time"), so the bare "<election>, <year>" form is the schedule.
_ELECTION = (r"General Elections? to (?:the )?(?:Legislative Assembl(?:y|ies)\b[^:\-–]*?|Lok Sabha|"
             r"House of the People)|(?:Presidential|Vice[- ]Presidential) Election")
_SCHEDULE_TITLE = re.compile(rf"^(?:Schedule for (?:the )?)?(?:{_ELECTION}),?\s*(?:20\d\d)?\s*(?:[-–]\s*reg\.?)?\s*$",
                             re.I)
_SCHEDULE_ANY = re.compile(rf"Schedule (?:for|of) (?:the )?(?:{_ELECTION})|announce[sd]? (?:the )?schedule", re.I)
_RESULTS = re.compile(r"\b(?:results? (?:of|for|declared)|counting of votes|declared elected|trends and results)\b",
                      re.I)
_BYE_ONLY = re.compile(r"^(?:Schedule for )?Bye[- ]?elections?\b", re.I)
_SUBJECT = re.compile(r"Subject\s*:\s*(.+?)(?:\s*[-–]\s*reg\.?|\n)", re.I)


def classify(title: str, head: str = "") -> tuple[str, str]:
    """(route_hint, scope_reason) from the title and, for generic titles, the PDF's heading text."""
    t = re.sub(r"\s+", " ", title).strip()
    if _GENERIC.match(t):
        if not head:
            # The PDF could not be read: a schedule announcement must not be lost, so it goes to the
            # weekly digest flagged rather than being dropped.
            return "weekly", "ECI press note with a generic title; PDF not read, kept for review"
        m = _SUBJECT.search(head)
        subject = m.group(1).strip() if m else ""
        if subject and (_SCHEDULE_TITLE.match(subject) or _SCHEDULE_ANY.search(subject)):
            return "weekly", "ECI general election schedule"
        if _SCHEDULE_ANY.search(head) or _RESULTS.search(head):
            return "weekly", "ECI election schedule / results"
        return "drop", "ECI press note: administrative (from PDF heading)"
    if _BYE_ONLY.match(t):
        return "drop", "ECI bye-election schedule (not macro)"
    if _SCHEDULE_TITLE.match(t) or _SCHEDULE_ANY.search(t):
        return "weekly", "ECI general election schedule"
    if _RESULTS.search(t):
        return "weekly", "ECI election results"
    return "drop", "ECI press note: administrative / campaign logistics"


def parse_list(data: bytes) -> list[dict]:
    doc = json.loads(data)
    try:
        rows = doc["results"]["data"]
    except (KeyError, TypeError) as e:
        raise ValueError(f"ECI get-file: unexpected shape (encrypted params rotated?): {str(doc)[:200]}") from e
    if not isinstance(rows, list):
        raise ValueError("ECI get-file: results.data is not a list")
    out = []
    for r in rows:
        atts = r.get("document_attachments") or []
        att = next((a for a in atts if (a.get("pdf_type") or "").upper() == "EN"), atts[0] if atts else None)
        raw = (r.get("date_of_creation") or "").strip()
        try:
            when = datetime.strptime(raw, "%A %d %b %Y, %I:%M %p").replace(tzinfo=IST) if raw else None
        except ValueError:
            when = None
        out.append({"id": r["id"], "title": re.sub(r"\s+", " ", r.get("document_title") or "").strip(),
                    "number": (r.get("reference_number") or "").strip(), "published": when,
                    "published_raw": raw or None, "pdf_url": att.get("record_location") if att else None})
    return out


def pdf_head(pdf: bytes, n: int = 1500) -> str:
    return content.pdf_body(pdf)[:n]


class Eci(SourceAdapter):
    doc_type = "eci"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_list(res.content):
            if not r["pdf_url"]:
                continue
            hint, reason = classify(r["title"])
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"id:{r['id']}", url=r["pdf_url"], title=r["title"] or "PRESS NOTE",
                source_published_at=r["published"], published_raw=r["published_raw"], document_id=res.document_id,
                meta={"kind": "Press note", "number": r["number"], "route_hint": hint, "scope_reason": reason,
                      "generic_title": bool(_GENERIC.match(r["title"] or "PRESS NOTE"))}))
        return items

    def enrich(self, it: RawItem) -> RawItem:
        """Generic "PRESS NOTE" titles: read the PDF's heading to classify (new items only, a few a week)."""
        if not it.meta.get("generic_title"):
            return it
        res = self.ctx.fetcher.get(it.url, source_id=self.cfg.id, doc_type="eci:pdf", expect="pdf",
                                   conditional=False, parent_id=it.document_id)
        head = pdf_head(res.content)
        hint, reason = classify(it.title, head)
        m = _SUBJECT.search(head)
        meta = {**it.meta, "route_hint": hint, "scope_reason": reason, "pdf_document_id": res.document_id}
        if m:
            meta["subject"] = m.group(1).strip()
        return it.model_copy(update={"meta": meta})

    def digest_body(self, it: RawItem) -> content.Body:
        r = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:eci_pdf", expect="pdf", conditional=False)
        text = content.pdf_body(r.content)
        notes = [] if len(text) > 200 else ["The PDF has no text layer (scanned image); see the PDF."]
        return content.Body(text, content._paragraphs(text), it.url, it.url, r.document_id, notes=notes)
