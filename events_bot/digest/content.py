"""Full text of a release for the digest: fetch (archived), deterministic text, paragraphs.

Every release body is derived from archived bytes by a pure function (`html_body`, `pdf_body`), and
every paragraph the digest quotes is a line of that text. So `paragraph in body.text` holds by
construction, and tests/test_digest.py checks it against real fixtures, the same contract as the
MPC review (stage2/mpc.py).

Per source:
  rbi_pr, rbi_notif  display page `.tablebg` (the full press release / notification text); the
                     PDF link is listed for reference
  sebi_circ, sebi_pr the page embeds the PDF in an iframe (`web/?file=<pdf>`); the PDF is the substance
  pib                release page paragraphs (pib.parse_release)
  imd, mospi, oea,   the item URL is the PDF
  nifty_indices
  cbic               /api/cbic-{notification,circular}-msts/{id} -> docFilePath ->
                     /content/pdf/{docFilePath} = JSON {data: base64 PDF} (the portal's own viewer)
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urljoin, urlparse

from selectolax.parser import HTMLParser

from ..core.models import RawItem
from ..extract.base import html_text

MIN_PARA = 50            # shorter lines are headings, captions, signatures


@dataclass
class Body:
    text: str                                   # deterministic text of the archived document
    paragraphs: list[str]                       # each one is a line of `text`
    page_url: str
    pdf_url: str | None = None
    document_id: int | None = None
    notes: list[str] = field(default_factory=list)


def _norm_ws(s: str) -> str:
    return re.sub(r"[ \t\r\f\v  ​]+", " ", s).strip()


def _paragraphs(text: str) -> list[str]:
    out, seen = [], set()
    for line in text.split("\n"):
        if len(line) >= MIN_PARA and line not in seen:
            seen.add(line)
            out.append(line)
    return out


def html_body(content: bytes, selector: str) -> str:
    return html_text(content, selector)


_PDF_PAGE = re.compile(r"^(?:Page\s+)?\d+\s*(?:of|/)\s*\d+$|^-?\s*\d{1,3}\s*-?$", re.I)
_NUMBERED = re.compile(r"^(?:\(?[a-zA-Z]{1,4}\)|\(?\d+(?:\.\d+)*[.)]|[•●▪❖\-–]\s)\s*")
_ENDS = (".", ";", ":", "!", "?", "”", "\"", "’")
_SALUTE = re.compile(r"^(?:Madam|Sir|Dear|Yours|Encl|Copy to|Subject\s*:|Sub\s*:|Ref\s*:|To,?$)", re.I)


def pdf_body(pdf: bytes) -> str:
    """Paragraph lines in reading order.

    Text blocks are read in order; a block's line breaks become spaces, and a block is joined to the
    previous one when that one stops mid-sentence and this one does not open a numbered clause (many
    government PDFs emit one block per printed line). Page footers ("Page 1 of 8") stay as their own lines.
    Only whitespace changes; hyphenated line ends are kept as printed."""
    import pymupdf
    lines: list[str] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for page in doc:
            for b in page.get_text("blocks", sort=True):
                if b[6] != 0:          # image block
                    continue
                t = _norm_ws(b[4].replace("\n", " "))
                if not t:
                    continue
                prev = lines[-1] if lines else ""
                if (prev and not prev.endswith(_ENDS) and not _PDF_PAGE.match(prev) and not _PDF_PAGE.match(t)
                        and not _NUMBERED.match(t) and not _SALUTE.match(t) and len(prev) > 25):
                    lines[-1] = f"{prev} {t}"
                else:
                    lines.append(t)
    return "\n".join(lines)


def pib_body(content: bytes) -> str:
    from ..sources.india.pib import parse_release
    return "\n".join(parse_release(content)["paragraphs"])


_RBI_SKIP_PDF = re.compile(r"Utkarsh|Accessibility", re.I)


def rbi_pdf_link(content: bytes) -> str | None:
    tree = HTMLParser(content.decode("utf-8", "replace"))
    for a in tree.css(".tablebg a[href], a[href]"):
        h = a.attributes.get("href") or ""
        if re.search(r"\.pdf$", h, re.I) and "rbidocs" in h and not _RBI_SKIP_PDF.search(h):
            return h
    return None


def sebi_pdf_link(content: bytes, page_url: str) -> str | None:
    tree = HTMLParser(content.decode("utf-8", "replace"))
    for fr in tree.css("iframe[src]"):
        src = fr.attributes.get("src") or ""
        q = parse_qs(urlparse(src).query)
        if q.get("file"):
            return urljoin(page_url, q["file"][0])
    for a in tree.css("a[href]"):
        h = a.attributes.get("href") or ""
        if h.lower().endswith(".pdf"):
            return urljoin(page_url, h)
    return None


def sebi_html_body(content: bytes) -> str:
    for sel in (".main_full", ".content_area", "#content", "body"):
        try:
            t = html_text(content, sel)
        except ValueError:
            continue
        if len(t) > 200:
            return t
    return ""


CBIC_API = "https://taxinformation.cbic.gov.in/api/cbic-{kind}-msts/{id}"
CBIC_PDF = "https://taxinformation.cbic.gov.in/content/pdf/{path}"


def cbic_pdf_bytes(content: bytes) -> bytes:
    """/content/pdf/... answers JSON {"data": "<base64 pdf>"}."""
    try:
        data = json.loads(content)["data"]
    except (ValueError, KeyError, TypeError):
        return content if content[:4] == b"%PDF" else b""
    return base64.b64decode(data)


def fetch(ctx, it: RawItem) -> Body:
    """Fetch and archive the release; raise on failure (the caller shows a fetch-failed panel)."""
    f, sid = ctx.fetcher, it.source_id

    def get(url: str, expect: str, doc_type: str, **kw):
        return f.get_with_retry(url, tries=3, base_delay=4, source_id=sid, doc_type=f"digest:{doc_type}",
                                expect=expect, conditional=False, **kw)

    if sid in ("rbi_pr", "rbi_notif"):
        r = get(it.url, "html", "rbi_page")
        text = html_body(r.content, ".tablebg")
        return Body(text, _paragraphs(text), it.url, rbi_pdf_link(r.content), r.document_id)

    if sid in ("sebi_circ", "sebi_pr"):
        r = get(it.url, "html", "sebi_page")
        pdf = sebi_pdf_link(r.content, it.url)
        if pdf:
            p = get(pdf, "pdf", "sebi_pdf", parent_id=r.document_id)
            text = pdf_body(p.content)
            if len(text) > 200:
                return Body(text, _paragraphs(text), it.url, pdf, p.document_id)
        text = sebi_html_body(r.content)
        return Body(text, _paragraphs(text), it.url, pdf, r.document_id,
                    notes=["The PDF had no text layer; quoted from the web page."] if pdf else [])

    if sid == "pib":
        r = get(it.url, "html", "pib_page", impersonate=False)
        text = pib_body(r.content)
        return Body(text, _paragraphs(text), it.url, None, r.document_id)

    if sid == "cbic":
        kind = "circular" if "/Circulars" in it.url else "notification"
        doc_id = it.url.split("/view-pdf/", 1)[1].split("/", 1)[0]
        meta = get(CBIC_API.format(kind=kind, id=doc_id), "json", "cbic_meta")
        path = (json.loads(meta.content) or {}).get("docFilePath")
        if not path:
            raise ValueError("CBIC record has no docFilePath")
        p = get(CBIC_PDF.format(path=path.lstrip("/")), "any", "cbic_pdf", parent_id=meta.document_id)
        pdf = cbic_pdf_bytes(p.content)
        text = pdf_body(pdf) if pdf else ""
        return Body(text, _paragraphs(text), it.url, None, p.document_id)

    # imd, mospi, oea, nifty_indices: the item URL is the PDF itself
    r = get(it.url, "pdf", f"{sid}_pdf")
    text = pdf_body(r.content)
    notes = [] if len(text) > 200 else ["The PDF has no text layer (scanned image); see the PDF."]
    return Body(text, _paragraphs(text), it.url, it.url, r.document_id, notes=notes)
