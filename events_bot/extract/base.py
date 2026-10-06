"""Extraction primitives shared by every document type.

A document's *text* is a deterministic function of its archived bytes (`html_text`,
`table_text`, ...), stored in `documents.text`. Every extraction points into that text
by character offsets, so the validator (and anyone auditing an email) can check that
`text[char_start:char_end] == value_text` -- the exact source string, never a rewrite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from decimal import Decimal, InvalidOperation

from selectolax.parser import HTMLParser

# Hyphen-like characters the Fed and RBI use inside numbers ("3‑3/4" with U+2011).
HYPHENS = "-‐‑‒–—−"
_HY = f"[{HYPHENS}]"
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "li", "td", "th"}


@dataclass
class Extraction:
    field: str
    value_text: str                    # exact source substring
    value_norm: Decimal | None = None
    unit: str | None = None
    period: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    json_path: str | None = None
    page: int | None = None
    snippet: str | None = None
    doc: str = ""                      # which document of the event it came from (statement, sep, ...)
    validated: bool = False
    validation_error: str | None = None
    meta: dict = dc_field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.validated and not self.validation_error


def _clean_lines(raw: str) -> str:
    out = []
    for line in raw.split("\n"):
        line = re.sub(r"[ \t\r\f\v ]+", " ", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def html_text(content: bytes | str, selector: str, *, inline_join: bool = True) -> str:
    """Visible text of the first node matching `selector`, one block per line.

    Inline elements (spans, links) can split a sentence across text nodes; with
    inline_join the pieces of one paragraph are re-joined with a single space so
    a sentence is one line, which keeps regexes and redlines sane.
    """
    html = content.decode("utf-8-sig", "replace") if isinstance(content, bytes) else content
    tree = HTMLParser(html)
    for bad in tree.css("script, style, noscript"):
        bad.decompose()
    node = tree.css_first(selector)
    if node is None:
        raise ValueError(f"selector {selector!r} not found")
    if not inline_join:
        return _clean_lines(node.text(separator="\n", strip=False))
    blocks = []
    # traverse() is document order; node.css("a, b") groups results by selector.
    for el in node.traverse(include_text=False):
        if el.tag not in BLOCK_TAGS:
            continue
        # leaf-ish blocks only: skip containers that hold other blocks (selectolax's
        # node.css can match the node itself, hence the count rather than css_first)
        if len(el.css("p, li, table")) > (1 if el.tag in ("p", "li") else 0):
            continue
        t = re.sub(r"\s+", " ", el.text(separator=" ", strip=False).replace(" ", " ")).strip()
        if t:
            blocks.append(t)
    return "\n".join(blocks)


def table_text(rows: list[list[str]]) -> str:
    """Serialise a table as `cell | cell | ...` lines; cells are kept verbatim."""
    return "\n".join(" | ".join(c for c in r) for r in rows)


_FRAC = re.compile(rf"^\s*(\d+)?(?:\s*{_HY}\s*|\s+)?(?:(\d+)\s*/\s*(\d+))?\s*$")


def parse_number(s: str) -> Decimal | None:
    """'3-3/4' -> 3.75, '1/4' -> 0.25, '4' -> 4, '3.90' -> 3.90, '$160' -> 160. None if unparseable."""
    t = s.strip().replace(",", "").lstrip("$").rstrip("%").strip()
    try:
        return Decimal(t)
    except InvalidOperation:
        pass
    m = _FRAC.match(t)
    if not m or not (m.group(1) or m.group(2)):
        return None
    whole = Decimal(m.group(1) or 0)
    if m.group(2):
        whole += Decimal(m.group(2)) / Decimal(m.group(3))
    return whole


def find(text: str, pattern: str | re.Pattern, field: str, *, group: int | str = 1, unit: str | None = None,
         period: str | None = None, doc: str = "", flags: int = 0, norm: bool = True,
         start: int = 0) -> Extraction | None:
    """First regex match -> Extraction spanning `group`. None if the pattern is absent."""
    rx = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, flags)
    m = rx.search(text, start)
    if not m or m.group(group) is None:
        return None
    return span(text, m.start(group), m.end(group), field, unit=unit, period=period, doc=doc, norm=norm)


def span(text: str, a: int, b: int, field: str, *, unit: str | None = None, period: str | None = None,
         doc: str = "", norm: bool = True) -> Extraction:
    v = text[a:b]
    line_a = text.rfind("\n", 0, a) + 1
    line_b = text.find("\n", b)
    snippet = text[line_a: line_b if line_b != -1 else len(text)]
    if len(snippet) > 300:
        snippet = text[max(line_a, a - 120): min(len(text), b + 120)]
    return Extraction(field=field, value_text=v, value_norm=parse_number(v) if norm else None, unit=unit,
                      period=period, char_start=a, char_end=b, snippet=snippet, doc=doc)
