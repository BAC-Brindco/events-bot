"""Helpers shared by the US policy stream adapters (whitehouse, ustr, congress, fda).

Scope words used by these adapters: the realtime decision for White House / USTR items is made by digest/scope.py
from `delivery.us_realtime_keywords` against the item's title + summary. The adapters therefore put into
`summary` the sentences of the release that carry trade / India terms, so a proclamation whose title says
"Adjusting Imports of ..." still goes real time when its body invokes section 232 or names India.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time

from selectolax.parser import HTMLParser

from ...core.timeutil import ET
from ...digest import content

# Terms that make a US action relevant to the book. "India" only (never "Indian": proclamations speak of
# "American Indian" heritage). \bIndia\b does not match "Indian".
TRADE_RX = re.compile(
    r"\btariffs?\b|\bsection 232\b|\bsection 301\b|\bIEEPA\b|International Emergency Economic Powers|"
    r"Harmonized Tariff Schedule|\bHTSUS\b|\bad valorem\b|\badditional dut(?:y|ies)\b|\brates? of duty\b|"
    r"\bimport duties\b|\bcountervailing\b|\banti-?dumping\b|\breciprocal trade\b|\btrade agreement\b|"
    r"\bIndia\b|\bH-1B\b|\bgeneric drugs?\b|\bpharmaceutical",
    re.I)
INDIA_RX = re.compile(r"\bIndia\b|\bIndian (?:firm|compan|manufactur|national|pharma|drug|facilit)", re.I)

_SENT = re.compile(r"(?<=[.;])\s+(?=[A-Z(“\"])")


def _matching(paragraphs: list[str], rx: re.Pattern, limit: int, max_len: int, skip: list[str]) -> list[str]:
    if limit <= 0:
        return []
    out: list[str] = []
    for p in paragraphs:
        for s in _SENT.split(p):
            s = s.strip()
            if s and rx.search(s) and s not in out and s not in skip:
                out.append(s if len(s) <= max_len else s[:max_len].rsplit(" ", 1)[0] + " ...")
                if len(out) >= limit:
                    return out
    return out


def key_sentences(paragraphs: list[str], rx: re.Pattern = TRADE_RX, limit: int = 4, max_len: int = 400) -> list[str]:
    """Sentences (verbatim, document order within each group) that match `rx`, at most `limit`; sentences
    naming India come first so a long tariff text that names India late still carries it in the summary."""
    india = _matching(paragraphs, re.compile(r"\bIndia\b"), 2, max_len, [])
    return india + _matching(paragraphs, rx, max(0, limit - len(india)), max_len, india)


def html_paragraphs(html: str) -> list[str]:
    """Text of each <p> (whitespace collapsed), in order. Used on RSS content:encoded bodies."""
    tree = HTMLParser(html or "")
    out = []
    for p in tree.css("p"):
        t = re.sub(r"[\s ]+", " ", p.text(separator=" ")).strip()
        if t:
            out.append(content.tidy(t))
    return out


def et_midnight(d: date | None) -> datetime | None:
    return datetime.combine(d, time(0, 0), tzinfo=ET) if d else None


def fix_mojibake(s: str) -> str:
    """Congress.gov API titles carry UTF-8 read as cp1252 ("Veteransâ€™")."""
    if "â€" in s or "Ã" in s:
        try:
            return s.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return s


def body(text: str, page_url: str, document_id: int | None, *, pdf_url: str | None = None,
         notes: list[str] | None = None) -> content.Body:
    return content.Body(text, content._paragraphs(text), page_url, pdf_url, document_id, notes=list(notes or []))


def html_body_first(page: bytes, selectors: tuple[str, ...], min_len: int = 200) -> str:
    """content.html_body with the first selector that yields real text."""
    for sel in selectors:
        try:
            t = content.html_body(page, sel)
        except ValueError:
            continue
        if len(t) >= min_len:
            return t
    return ""
