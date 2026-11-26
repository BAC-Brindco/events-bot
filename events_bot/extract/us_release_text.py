"""Text helpers for the BEA / Census / DOL release parsers (sources/us/bea.py, census.py, dol.py).

Every value is a span of the document text (extract/base.py contract). Two shapes recur:

  * a sentence with the sign in a word: "increased 0.3 percent" / "decreased 0.3 percent". An increase
    is the number alone; a decrease keeps the verb in the span ("decreased 0.3"), so the alert never
    shows a bare "0.3" for a fall, and value_norm carries the sign for the computed change.
  * a table row: a label followed by its cells, one per line in PyMuPDF / BEA HTML text, with dot
    leaders and footnote digits glued to the label ("New Orders4.........."). `row_cells` returns the
    numeric cells after a label, in order, as spans.
"""
from __future__ import annotations

import re

from .base import Extraction, parse_number, span

DOWN = re.compile(r"^(?:decreas|declin|fell|down|lower)", re.I)
# A cell: a signed decimal or integer with thousands commas, standing alone (not glued to a word, so the
# "4" of "New Orders4" is not a cell), or a placeholder such as (*), (S), (NA) or BEA's missing-value mark.
CELL = re.compile(r"(?<![\w.,])([+-]?\d[\d,]*(?:\.\d+)?)(?![\w,]|\.\d)|\((?:\*|S|NA|D|X|Z)\)")
EMPTY_CELL = {"…", "�", "—", "-", "(NA)"}    # BEA prints "…" alone on a line for "no estimate"


def signed(text: str, m: re.Match, verb: int | str, num: int | str, field: str, *, unit: str | None = "growth_pct",
           period: str | None = None, doc: str = "") -> Extraction:
    """Extraction for "<verb> ... <num>" where the verb carries the sign."""
    if DOWN.match(m.group(verb)):
        ex = span(text, m.start(verb), m.end(num), field, unit=unit, period=period, doc=doc, norm=False)
        n = parse_number(m.group(num))
        ex.value_norm = -n if n is not None else None
        ex.meta["sign_from_verb"] = m.group(verb)
        return ex
    return span(text, m.start(num), m.end(num), field, unit=unit, period=period, doc=doc)


def row_cells(text: str, label: str | re.Pattern, *, start: int = 0, end: int | None = None, limit: int = 12,
              stop: re.Pattern | None = None) -> list[tuple[int, int]]:
    """Spans of the numeric cells that follow `label` (first match at/after `start`), up to `limit`.

    Cells are read until a line holds a word (the next row's label), so a short row never borrows
    the next row's numbers. Placeholders ((*), (S), (NA), a lone "…") occupy a position but come back as
    (a, a) empty spans so column indexes stay aligned."""
    rx = label if isinstance(label, re.Pattern) else re.compile(re.escape(label))
    m = rx.search(text, start, end if end is not None else len(text))
    if not m:
        return []
    pos, out = m.end(), []
    # the rest of the label's own line may hold cells (dot leaders then the first value)
    while len(out) < limit:
        nl = text.find("\n", pos)
        line_end = nl if nl != -1 else len(text)
        line = text[pos:line_end]
        if line.strip() in EMPTY_CELL:
            out.append((pos, pos))
            pos = nl + 1 if nl != -1 else len(text)
            if nl == -1:
                break
            continue
        cells = list(CELL.finditer(line))
        residue = CELL.sub(" ", line)
        if out and re.search(r"[A-Za-z]{2,}", residue):
            break                                    # next row's label
        if stop is not None and stop.search(line):
            break
        for c in cells:
            if c.group(1) is not None:
                out.append((pos + c.start(1), pos + c.end(1)))
            else:
                out.append((pos + c.start(), pos + c.start()))
            if len(out) >= limit:
                break
        if nl == -1:
            break
        pos = nl + 1
    return out


def cell(text: str, spans: list[tuple[int, int]], i: int, field: str, *, unit: str | None = "growth_pct",
         period: str | None = None, doc: str = "") -> Extraction | None:
    if i >= len(spans) or spans[i][0] == spans[i][1]:
        return None
    a, b = spans[i]
    return span(text, a, b, field, unit=unit, period=period, doc=doc)


def text_field(text: str, m: re.Match, group: int | str, field: str, *, doc: str = "",
               period: str | None = None) -> Extraction:
    return span(text, m.start(group), m.end(group), field, unit="text", period=period, doc=doc, norm=False)


def line_from(text: str, phrase: str) -> str | None:
    """The rest of the line from `phrase` on (a verbatim substring), or None."""
    i = text.find(phrase)
    if i < 0:
        return None
    j = text.find("\n", i)
    return text[i: j if j != -1 else len(text)].strip()
