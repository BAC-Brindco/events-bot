"""MoSPI data releases (CPI, IIP, GDP) -> validated extractions + verbatim highlights.

Document text is pdfplumber's text layer (deterministic for the same bytes). The CPI body text is
fragmented by its Word export ("4." / "82"), so CPI numbers come from the headline table, serialised
as `cell | cell` lines and validated against that serialisation, exactly like the FOMC SEP table.
IIP and GDP numbers come from their "Key Highlights" sentences, which are clean.
"""
from __future__ import annotations

import io
import re

from .base import Extraction, span, table_text

PCT = r"\(?-?\)?\s?\d+(?:\.\d+)?"
MONTHS = ("January February March April May June July August September October November December").split()


def pdf_pages(pdf: bytes) -> list[str]:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as p:
        return [pg.extract_text() or "" for pg in p.pages]


def kind_of(title: str) -> str | None:
    t = title.lower()
    if re.search(r"\bcpi\b|consumer price index", t) and "industrial workers" not in t:
        return "cpi"
    if "index of industrial production" in t or re.search(r"\biip\b", t):
        return "iip"
    if "gross domestic product" in t and "quarter" in t:
        return "gdp"
    return None


# ---- CPI -----------------------------------------------------------------------------------

def cpi_table(pdf: bytes) -> list[list[str]] | None:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as p:
        for pg in p.pages[:2]:
            for t in pg.extract_tables():
                flat = [[(c or "").replace("\n", " ").strip() for c in r] for r in t]
                # the 8-column headline table, not the page-box "table" that wraps the whole page
                if max((len(r) for r in flat), default=0) >= 8 and \
                        any(c == "CPI (General)" for r in flat for c in r) and any(c == "Combined" for r in flat for c in r):
                    return flat
    return None


def parse_cpi(pdf: bytes) -> tuple[dict[str, str], list[Extraction]]:
    """Headline table, whatever its layout: column groups are identified by their own header text
    ('(Provisional)' = current month, '(Final)' = prior month), spacer rows are dropped, and a label
    row with its numbers on the next row (July 2026 layout) is handled."""
    raw = cpi_table(pdf)
    if not raw:
        return {}, []
    rows = [r for r in raw if any(c for c in r)]
    text = table_text(rows)
    offs, pos = [], 0
    for r in rows:
        offs.append(pos)
        pos += len(" | ".join(r)) + 1
    groups: list[tuple[str, str]] = []           # [(which, period label)] for value columns 2-4 and 5-7
    for r in rows:
        labels = [c for c in r if re.search(r"\((?:Provisional|Final)\)", c)]
        if len(labels) == 2:
            groups = [("current" if "Provisional" in l else "prior", l) for l in labels]
            break
    if len(groups) != 2:
        return {"table": text}, []
    ex: list[Extraction] = []
    section = series = None
    num = re.compile(r"^-?\d+(?:\.\d+)?$")
    for i, r in enumerate(rows):
        if r[0]:
            section = "infl" if r[0].startswith("Inflation") else ("index" if r[0].startswith("Index") else section)
        if len(r) > 1 and r[1]:
            series = {"CPI (General)": "cpi", "CFPI": "cfpi"}.get(r[1], series if not r[1] else None)
        vals = r[2:8] if len(r) >= 8 else []
        if section != "infl" or series is None or not any(num.match(c) for c in vals):
            continue
        col = offs[i] + sum(len(c) + 3 for c in r[:2])
        for k, c in enumerate(vals):
            which, label = groups[0 if k < 3 else 1]
            area = ("rural", "urban", "combined")[k % 3]
            if num.match(c):
                ex.append(span(text, col, col + len(c), f"mospi.{series}.{area}.{which}", unit="growth_pct",
                               doc="table", period=label))
            col += len(c) + 3
    return {"table": text}, ex


# ---- IIP -----------------------------------------------------------------------------------

_IIP_HEAD = re.compile(r"The IIP growth rate for the month of (\w+ \d{4}) is (-?\d+(?:\.\d+)?) percent which was\s+"
                       r"(-?\d+(?:\.\d+)?) percent \(([^)]+)\) in the month of (\w+ \d{4})")
_IIP_SECT = re.compile(r"The growth rates of the sectors, Mining & Quarrying, Manufacturing, Electricity & Gas Supply"
                       r"(?: and\s+Water Supply, Sewerage & Waste Management)? for the month of (\w+ \d{4}) are\s+"
                       r"((?:\(-\)\s?)?-?\d+(?:\.\d+)?) percent, ((?:\(-\)\s?)?-?\d+(?:\.\d+)?)\s+percent, "
                       r"((?:\(-\)\s?)?-?\d+(?:\.\d+)?) percent(?: and ((?:\(-\)\s?)?-?\d+(?:\.\d+)?) percent)?")


def _signed(e: Extraction) -> Extraction:
    """MoSPI writes negatives as '(-) 5.6'; the verbatim string is kept, the value is negated."""
    from decimal import Decimal
    if e.value_text.startswith("(-)"):
        e.value_norm = -Decimal(e.value_text.replace("(-)", "").strip())
    return e


def parse_iip(pdf: bytes) -> tuple[dict[str, str], list[Extraction]]:
    text = "\n".join(pdf_pages(pdf))
    flat = re.sub(r"\s*\n\s*", " ", text)
    ex: list[Extraction] = []
    m = _IIP_HEAD.search(flat)
    if m:
        ex.append(span(flat, m.start(2), m.end(2), "mospi.iip.growth.current", unit="growth_pct", doc="text",
                       period=m.group(1)))
        p = span(flat, m.start(3), m.end(3), "mospi.iip.growth.prior", unit="growth_pct", doc="text", period=m.group(5))
        p.meta["estimate"] = m.group(4)
        ex.append(p)
    s = _IIP_SECT.search(flat)
    if s:
        for g, name in zip(range(2, 6), ("mining", "manufacturing", "electricity", "water")):
            if s.group(g):
                ex.append(_signed(span(flat, s.start(g), s.end(g), f"mospi.iip.{name}.current", unit="growth_pct",
                                       doc="text", period=s.group(1))))
    return {"text": flat}, ex


# ---- GDP -----------------------------------------------------------------------------------

_GDP = [
    ("mospi.gdp.real.current", r"Real GDP has been estimated to grow by (\d+(?:\.\d+)?)% in (Q\d of FY \d{4}-\d{2})"),
    ("mospi.gdp.real.year_ago", r"Real GDP has been estimated to grow by [\d.]+% in Q\d of FY \d{4}-\d{2},\s*against the "
                                r"growth of (\d+(?:\.\d+)?)% experienced during (Q\d of FY \d{4}-\d{2})"),
    ("mospi.gdp.nominal.current", r"Nominal GDP has witnessed a growth of (\d+(?:\.\d+)?)% in (Q\d of FY \d{4}-\d{2})"),
    ("mospi.gdp.nominal.year_ago", r"Nominal GDP has witnessed a growth of [\d.]+% in Q\d of FY \d{4}-\d{2},\s*against "
                                   r"the growth of (\d+(?:\.\d+)?)% during (Q\d of FY \d{4}-\d{2})"),
    ("mospi.gva.real.current", r"Real and Nominal GVA has been assessed to grow by (\d+(?:\.\d+)?)% and [\d.]+%\s*"
                               r"respectively in (Q\d of FY \d{4}-\d{2})"),
    ("mospi.gva.nominal.current", r"Real and Nominal GVA has been assessed to grow by [\d.]+% and (\d+(?:\.\d+)?)%\s*"
                                  r"respectively in (Q\d of FY \d{4}-\d{2})"),
    ("mospi.gfcf.real.current", r"Gross Fixed Capital Formation \(GFCF\)\s*recorded [^(]*\((\d+(?:\.\d+)?)%\) growth rate at "
                                r"Constant prices during (Q\d\s*of FY \d{4}-\d{2})"),
    ("mospi.pfce.real.current", r"Private Final Consumption Expenditure \(PFCE\) registered a growth of\s*"
                                r"(\d+(?:\.\d+)?)% at Constant prices during the quarter()"),
]


def parse_gdp(pdf: bytes) -> tuple[dict[str, str], list[Extraction]]:
    flat = re.sub(r"\s*\n\s*", " ", "\n".join(pdf_pages(pdf)))
    ex: list[Extraction] = []
    for field, pat in _GDP:
        m = re.search(pat, flat)
        if m:
            ex.append(span(flat, m.start(1), m.end(1), field, unit="growth_pct", doc="text",
                           period=re.sub(r"\s+", " ", m.group(2)) or None))
    return {"text": flat}, ex


def highlights(kind: str, texts: dict[str, str]) -> list[str]:
    """Verbatim headline sentences for the alert's 'Key points' (never rewritten)."""
    t = texts.get("text", "")
    if kind == "gdp":
        m = re.search(r"KEY HIGHLIGHTS (.+?) (?:\d+ )?This Press Release is embargoed", t)
        block = m.group(1) if m else ""
        return [s.strip() for s in re.split(r"(?<=\.)\s+(?=[A-Z])", block) if len(s.strip()) > 30][:8]
    if kind == "iip":
        m = re.search(r"Key Highlights:\s*(.+?)(?:\s+\d+ \| Page|\s+3\.)", t)
        block = m.group(1) if m else ""
        items = [re.sub(r"^[ivx]+\.\s*", "", s).strip() for s in re.split(r"\s(?=[ivx]+\.\s)", block)]
        lead = re.search(r"(In \w+ \d{4}, Index of Industrial Production recorded .+?\.)\s", t)
        return ([lead.group(1)] if lead else []) + [s for s in items if len(s) > 30][:6]
    return []
