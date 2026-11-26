"""Union Budget (indiabudget.gov.in): the Budget Speech and Budget at a Glance PDFs, once a year.

Phase 0 (india_t1): the documents sit at stable paths that are overwritten each year, with no feed:
  speech  https://www.indiabudget.gov.in/doc/Budget_Speech.pdf          (LM 1 Feb 2026 06:11 IST, pre-staged)
  glance  https://www.indiabudget.gov.in/doc/Budget_at_Glance/budget_at_a_glance.pdf  (LM 1 Feb 2026 02:30 IST)
IIS answers If-Modified-Since with 304 (verified 26 Nov 2026), so polls inside the window are cheap.

The item key is the document plus the fiscal year printed on its cover ("BUDGET 2026-2027"), with "interim"
added for an interim budget, so a July full budget in an election year is a new item. The file at a path
changes once a year; the first poll of a window records last year's documents as the silent baseline.

Outside the active windows (`filters.windows`, "MM-DD..MM-DD" in IST, default Jan 15 - Feb 15) a poll makes
no request at all. In an election year add the July window to the YAML.
Routing: india_eod (F-29: India items never alert in real time).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST, utcnow

DOC = {"speech": "Budget Speech", "glance": "Budget at a Glance"}
DEFAULT_WINDOWS = ("01-15..02-15",)
_FY = re.compile(r"\bBUDGET(?:\s+AT\s+A\s+GLANCE)?\s+(20\d{2})\s*[-–]\s*(20\d{2}|\d{2})\b", re.I)
_INTERIM = re.compile(r"\bINTERIM\s+BUDGET\b", re.I)


def in_window(d: date, windows) -> bool:
    for w in windows or DEFAULT_WINDOWS:
        a, b = (tuple(int(x) for x in s.strip().split("-")) for s in w.split(".."))
        md = (d.month, d.day)
        if (a <= md <= b) if a <= b else (md >= a or md <= b):
            return True
    return False


def cover(pdf: bytes) -> dict:
    """Fiscal year and interim flag from the first two pages."""
    import pymupdf
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        head = " ".join(doc[i].get_text() for i in range(min(2, doc.page_count)))
    m = _FY.search(head)
    fy = None
    if m:
        y2 = m.group(2)
        fy = f"{m.group(1)}-{y2 if len(y2) == 4 else m.group(1)[:2] + y2}"
    return {"fy": fy, "interim": bool(_INTERIM.search(head))}


class UnionBudget(SourceAdapter):
    doc_type = "india_budget"

    def poll(self) -> list[RawItem]:
        today = utcnow().astimezone(IST).date()
        if not in_window(today, self.cfg.filters.get("windows")):
            return []                      # cheap no-op: no request outside the Budget window
        return super().poll()

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        if res.content[:4] != b"%PDF":
            raise ValueError(f"india_budget {url_name}: not a PDF")
        c = cover(res.content)
        lm = res.headers.get("last-modified")
        try:
            published = parsedate_to_datetime(lm) if lm else None
        except (TypeError, ValueError):
            published = None
        tag = c["fy"] or (f"lm-{published:%Y%m%d}" if published else f"sha-{(res.sha256 or '')[:12]}")
        if c["interim"]:
            tag += "-interim"
        label = f"{'Interim ' if c['interim'] else ''}Union Budget {c['fy'] or ''}".replace("  ", " ").strip()
        return [RawItem(
            source_id=self.cfg.id, ext_id=f"{url_name}:{tag}", url=res.url,
            title=f"{label}: {DOC.get(url_name, url_name)}", source_published_at=published, published_raw=lm,
            document_id=res.document_id,
            meta={"kind": DOC.get(url_name, url_name), "fy": c["fy"], "interim": c["interim"], "feed": url_name,
                  "route_hint": "india_eod", "scope_reason": "Union Budget document",
                  "priority_tags": ["budget"]})]

    def digest_body(self, it: RawItem):
        from ...digest import content
        r = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:budget_pdf", expect="pdf", conditional=False)
        text = content.pdf_body(r.content)
        notes = [] if len(text) > 200 else ["The PDF has no text layer; see the PDF."]
        return content.Body(text, content._paragraphs(text), it.url, it.url, r.document_id, notes=notes)
