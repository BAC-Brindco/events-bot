"""NSE Indices press releases: index inclusions / exclusions / replacements, as verbatim rows.

Layout (semi-annual "Replacements in indices", ad hoc inclusions/exclusions alike):
  "1) Nifty 50"                                     -> index heading
  "The following company is being excluded:"       -> action
  "Sr. No. Company Name Symbol" / "1 Wipro Ltd. WIPRO" rows, continuing across pages
The text layer is read in order, so lists split across pages stay with their index. Each row's
company name and symbol are kept exactly as printed.
"""
from __future__ import annotations

import re

from .mospi import pdf_pages

_INDEX = re.compile(r"^(?:[A-Z]\.\s+.*|\d+\)\s+(?P<name>.+?))\s*$")
_ACTION = re.compile(r"being (excluded|included|replaced)", re.I)
_ROW = re.compile(r"^(\d+)\s+(.+?)\s+([A-Z0-9&\-]{2,20})$")
_EFFECTIVE = re.compile(r"(?:become effective|effective) from ([A-Z][a-z]+ \d{1,2}, \d{4}(?: \(close of [A-Z][a-z]+ \d{1,2}, "
                        r"\d{4}\))?)")


def parse_changes(pdf: bytes) -> dict:
    lines = [l.strip() for page in pdf_pages(pdf) for l in page.split("\n")]
    text = "\n".join(lines)
    title = next((l for l in lines[:8] if re.search(r"Replacement|Inclusion|Exclusion|Changes in", l)), "")
    # single-index notices name the index only in the title: "Exclusion from Nifty SME Emerge index"
    t = re.search(r"(?:from|in|into|to)\s+(Nifty.+?)(?:\s+index)?(?:\s+w\.e\.f\..*)?$", title, re.I)
    index = t.group(1).strip() if t else None
    action = None
    rows: list[dict] = []
    for l in lines:
        if l.startswith("About NSE Indices"):
            break
        m = re.match(r"^\d+\)\s+(.+)$", l)
        if m and len(l) < 90 and not _ROW.match(l):
            index, action = m.group(1).strip(), None
            continue
        a = _ACTION.search(l)
        if a and l.lower().startswith("the following"):
            action = a.group(1).lower()
            continue
        if l.startswith(("Note", "Sr. No.")):
            if l.startswith("Note"):
                action = None
            continue
        r = _ROW.match(l)
        if r and index and action:
            rows.append({"index": index, "action": action, "company": r.group(2), "symbol": r.group(3)})
    eff = _EFFECTIVE.search(re.sub(r"\s+", " ", text))
    return {"rows": rows, "effective": eff.group(1) if eff else None, "title": title, "text": text}
