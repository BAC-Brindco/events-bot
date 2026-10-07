"""Office of the Economic Adviser (DPIIT): WPI and Index of Eight Core Industries (ICI).

Both releases open with a numbered "Key highlights" list whose sentences carry the headline
numbers; those sentences are the extraction source (pdfplumber text, flattened) and are also
quoted verbatim as the alert's key points. Archived ICI copies can be scanned images with no
text layer (Phase 0: IPR_2026_07); then nothing is extracted and the alert says so.
"""
from __future__ import annotations

import re

from .base import Extraction, span
from .mospi import pdf_pages

N = r"-?\d+(?:\.\d+)?"

_WPI = [
    ("oea.wpi.headline.current", rf"Wholesale Price Index \(WPI\) inflation for (\w+ \d{{4}}) is ({N}) per cent", 2, 1),
    ("oea.wpi.headline.prior", rf"inflation for \w+ \d{{4}} is {N} per cent on year-on-\s?year \(YoY\) basis, compared to "
                               rf"({N}) per cent in (\w+ \d{{4}})", 1, 2),
    ("oea.wpi.index.current", rf"The index for All Commodities for (\w+ \d{{4}}) stands at ({N})", 2, 1),
    ("oea.wpi.food.current", rf"WPI Food Index .*?observed a YoY inflation of ({N}) per cent in (\w+ \d{{4}})", 1, 2),
    ("oea.wpi.food.prior", rf"WPI Food Index .*?observed a YoY inflation of {N} per cent in \w+ \d{{4}}, compared\s+to "
                           rf"({N}) per cent in (\w+ \d{{4}})", 1, 2),
]
_WPI_GROUPS = re.compile(rf"Primary Articles, Fuel and Power, and Manufactured Products are ({N}) per cent, ({N}) per cent, "
                         rf"and ({N}) per cent, respectively in (\w+ \d{{4}}), compared to ({N}) per cent, ({N}) per cent, "
                         rf"and ({N}) per cent, respectively in (\w+ \d{{4}})")

_ICI = [
    ("oea.ici.growth.current", rf"Index of Core Industries \(ICI\) grew by ({N}) per cent in (\w+ \d{{4}})", 1, 2),
    ("oea.ici.growth.prior", rf"growth rate of ({N}) per cent recorded in (\w+ \d{{4}})", 1, 2),
    ("oea.ici.cumulative.current", rf"Cumulative growth rate of ICI during ([\w-]+ \d{{4}}) was ({N}) per cent", 2, 1),
    ("oea.ici.cumulative.year_ago", rf"Cumulative growth rate of ICI during [\w-]+ \d{{4}} was {N} per cent \([^)]*\) "
                                    rf"compared to ({N}) per cent in (the corresponding period of the previous year)", 1, 2),
]


def flat_text(pdf: bytes, pages: int = 2) -> str:
    return re.sub(r"\s*\n\s*", " ", "\n".join(pdf_pages(pdf)[:pages]))


def _find(flat: str, specs, doc: str = "text") -> list[Extraction]:
    ex = []
    for field, pat, vg, pg in specs:
        m = re.search(pat, flat)
        if m:
            ex.append(span(flat, m.start(vg), m.end(vg), field, unit="growth_pct", doc=doc, period=m.group(pg)))
    return ex


def parse_wpi(pdf: bytes) -> tuple[dict[str, str], list[Extraction]]:
    flat = flat_text(pdf)
    ex = _find(flat, _WPI)
    for e in ex:
        if e.field == "oea.wpi.index.current":
            e.unit = "index"
    m = _WPI_GROUPS.search(flat)
    if m:
        for g, (name, which, per) in enumerate((("primary", "current", 4), ("fuel", "current", 4),
                                                ("manufactured", "current", 4), ("primary", "prior", 8),
                                                ("fuel", "prior", 8), ("manufactured", "prior", 8)), start=0):
            grp = (1, 2, 3, 5, 6, 7)[g]
            ex.append(span(flat, m.start(grp), m.end(grp), f"oea.wpi.{name}.{which}", unit="growth_pct", doc="text",
                           period=m.group(per)))
    return {"text": flat}, ex


def parse_ici(pdf: bytes) -> tuple[dict[str, str], list[Extraction]]:
    flat = flat_text(pdf)
    return {"text": flat}, _find(flat, _ICI)


def highlights(texts: dict[str, str]) -> list[str]:
    """The numbered 'Key highlights' sentences, verbatim, without the chart pointer item."""
    t = texts.get("text", "")
    m = re.search(r"Key highlights of .+? for (?:the month of )?\w+ \d{4} (.+?)(?:Page \| \d|Page \d+ of|\s2\.\s[A-Z]|\sEMBARGO ADVISORY)", t)
    block = m.group(1) if m else ""
    items = [re.sub(r"^\((?:i|ii|iii|iv|v|vi|vii)\)\s*", "", s).strip()
             for s in re.split(r"\s(?=\((?:i|ii|iii|iv|v|vi|vii)\)\s)", " " + block)]
    return [s for s in items if len(s) > 40 and not re.search(r"charts? (?:\(\d|below)", s)][:6]
