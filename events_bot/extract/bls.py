"""BLS news releases (`news.release/<code>.nr0.htm`): CPI, Employment Situation, PPI, JOLTS.

The release body is `div.normalnews`: hard-wrapped `<pre>` text plus (CPI, PPI) HTML tables. `release_text`
turns it into one deterministic text, a pure function of the archived bytes:
  - each `<pre>` is split into blank-line-separated paragraphs; a prose paragraph is re-flowed onto one line
    (line breaks -> single spaces), so every sentence of the release is a substring of one line; a
    tabular / indented paragraph keeps its lines (whitespace-collapsed);
  - each `<table>` becomes `cell | cell | ...` rows (base.table_text), cells verbatim.
Every figure is a `base.span` into that text, so the validator re-checks it in place.

Signs. Prose numbers carry the verb, not the sign ("rose by 165,000", "fell by 32,000"); the payroll and
earnings patterns therefore emit a different field for a decline (`*_down`), so a figure is never shown
without the direction the release gives it. CPI/PPI figures come from Table A, where cells are signed.
"""
from __future__ import annotations

import re
from datetime import date

from selectolax.parser import HTMLParser

from .base import Extraction, span, table_text

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
_MON = "|".join(MONTHS)
_DOW = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"

# "embargoed until\n8:30 a.m. (ET) Tuesday, November 10, 2026" / JOLTS "For release 10:00 a.m. (ET) Tuesday, ..."
EMBARGO = re.compile(
    rf"(?:embargoed\s+until|For\s+release)(?:\s+USDL[\s-]?\d{{2}}-\d+)?\s+(\d{{1,2}}):(\d{{2}})\s*([ap])\.m\.\s*\(ET\)\s*{_DOW},\s+"
    rf"({_MON})\s+(\d{{1,2}}),\s+(\d{{4}})", re.I)
# "CONSUMER PRICE INDEX - OCTOBER 2026", "THE EMPLOYMENT SITUATION - OCTOBER 2026",
# "JOB OPENINGS AND LABOR TURNOVER <?> SEPTEMBER 2026" (JOLTS uses a non-ASCII dash, mis-decoded as U+FFFD)
PERIOD = re.compile(rf"^(?:THE\s+)?[A-Z][A-Z ,&-]+?\s+\S\s+((?:{_MON.upper()})\s+\d{{4}})\s*$", re.M)

N = r"-?\d+(?:\.\d+)?"


def _decode(content: bytes) -> str:
    # BLS pages are served as UTF-8 but JOLTS carries stray cp1252 bytes (bullets, dashes); keep it deterministic.
    return content.decode("utf-8", "replace")


def _ws(s: str) -> str:
    return re.sub(r"[ \t\r\f\v ]+", " ", s).strip()


def _pre_lines(raw: str) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n[ \t\r ]*\n", raw.replace("\r\n", "\n")):
        lines = [ln for ln in para.split("\n") if ln.strip()]
        if not lines:
            continue
        tabular = any(re.search(r"\S(?: {3,}|\t)\S", ln.strip()) or re.search(r"\.{3,}", ln) for ln in lines)
        if tabular or len(lines) == 1:
            out += [_ws(ln) for ln in lines]
        else:
            out.append(_ws(" ".join(lines)))
    return [ln for ln in out if ln]


def _table_rows(node) -> list[list[str]]:
    rows = []
    for tr in node.css("tr"):
        cells = [_ws(c.text(separator=" ", strip=False)) for c in tr.css("th, td")]
        if any(cells):
            rows.append(cells)
    return rows


def release_text(content: bytes) -> str:
    tree = HTMLParser(_decode(content))
    root = tree.css_first("div.normalnews") or tree.css_first("#bodytext")
    if root is None:
        raise ValueError("BLS release body (div.normalnews) not found")
    out: list[str] = []
    for el in root.traverse(include_text=False):
        tag = el.tag.lower()
        if tag == "pre":
            out += _pre_lines(el.text(separator="", strip=False))
        elif tag == "table":
            cap = el.css_first("caption")
            if cap is not None:
                out.append(_ws(cap.text(separator=" ")))
            out.append(table_text(_table_rows(el)))
    return "\n".join(x for x in out if x)


def embargo(text: str) -> tuple[date, int, int] | None:
    """(release date, hour, minute) in ET from the embargo line; None if the page carries none."""
    m = EMBARGO.search(text)
    if not m:
        return None
    hh, mm = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0), int(m.group(2))
    return date(int(m.group(6)), MONTHS.index(m.group(4).capitalize()) + 1, int(m.group(5))), hh, mm


def period(text: str) -> str | None:
    m = PERIOD.search(text)
    if not m:
        return None
    mon, yr = m.group(1).split()
    return f"{mon.capitalize()} {yr}"


def _prev_month(per: str) -> str:
    mon, yr = per.split()
    i = MONTHS.index(mon)
    return f"{MONTHS[i - 1]} {int(yr) - (1 if i == 0 else 0)}"


# Sentence ends: a period after a lower-case word, a digit or a closing parenthesis ("U.S. Bureau" is not an end).
_SENT = re.compile(r"(?:(?<=[a-z]{2}\.)|(?<=\d\.)|(?<=\.\)))\s+(?=[A-Z(])")


def sentences(line: str) -> list[str]:
    return [x for x in _SENT.split(line) if x]


def lead_sentences(text: str, n: int = 3) -> list[str]:
    """The release's own opening sentences (after the 'TITLE - MONTH YYYY' heading), verbatim: each is a
    substring of one line of `text`. Sentences about the 2025 appropriations lapse are appended when present,
    since they explain a missing 12-month figure."""
    m = PERIOD.search(text)
    lines = (text[m.end():] if m else text).split("\n")
    out: list[str] = []
    for ln in lines:
        if len(ln) < 60 or " | " in ln:
            continue
        for x in sentences(ln):
            if len(x) >= 40 and not x.startswith("(") and len(out) < n:
                out.append(x)
        if len(out) >= n:
            break
    for ln in lines[:12]:
        for x in sentences(ln):
            if "lapse in appropriations" in x and x not in out:
                out.append(x)
    return out


# ---- CPI / PPI: Table A ------------------------------------------------------------------------------

def _row_cells(text: str, row_start: int, line: str) -> list[tuple[int, int, str]]:
    """Cell spans (absolute offsets) of one `a | b | c` row line."""
    out, pos = [], 0
    for cell in line.split(" | "):
        out.append((row_start + pos, row_start + pos + len(cell), cell))
        pos += len(cell) + 3
    return out


def _find_row(text: str, start: int, label_rx: str, stop: int | None = None) -> tuple[int, str] | None:
    rx = re.compile(rf"^(?:{label_rx}) \| .*$", re.M)
    m = rx.search(text, start, stop if stop is not None else len(text))
    return (m.start(), m.group(0)) if m else None


def _cell(text: str, row: tuple[int, str] | None, idx: int, field: str, *, period: str | None,
          unit: str = "growth_pct", doc: str = "release") -> Extraction | None:
    if row is None:
        return None
    cells = _row_cells(text, row[0], row[1])
    if not (-len(cells) <= idx < len(cells)):
        return None
    a, b, v = cells[idx]
    if not re.fullmatch(N, v):
        return None                    # "-" / "(4)": not published (e.g. 12-month change across the 2025 lapse)
    return span(text, a, b, field, unit=unit, period=period, doc=doc)


def _month_label(mon: str) -> str:
    """Row label of a month in BLS tables: 'Oct.', 'May', 'June ( 1 )', 'Sept.' (+ revision footnote)."""
    return rf"{mon[:3]}[a-z]*\.?(?: \( ?\d ?\))?"


def parse_cpi(content: bytes) -> tuple[str, list[Extraction]]:
    """Table A: '<label> | m-6 | ... | m-1 | m | <12-mo NSA>'; the month header row names the SA columns."""
    text = release_text(content)
    per = period(text)
    ex: list[Extraction] = []
    blk = re.search(r"Table A\. Percent changes in CPI for All Urban Consumers", text)
    if blk is None or per is None:
        return text, ex
    s = blk.end()
    end = text.find("\nFootnotes", s)
    stop = end if end != -1 else None
    mon, yr = per.split()
    hdr = re.compile(rf"^(?:[A-Z][a-z]{{2,3}}\.? \d{{4}} \| )+{mon[:3]}[a-z]*\.? {yr}$", re.M).search(text, s, stop or len(text))
    if hdr is None:
        return text, ex            # newest SA column is not the release's own month: refuse to guess
    months = hdr.group(0).split(" | ")
    prior_per = _prev_month(per)
    for label, f in ((r"All items", "cpi.headline"), (r"All items less food and energy", "cpi.core")):
        row = _find_row(text, s, label, stop)
        if row is None or len(row[1].split(" | ")) != len(months) + 2:
            continue
        for idx, field, p in ((-2, f + ".mom", per), (-3, f + ".mom.prior", prior_per), (-1, f + ".yoy", per)):
            e = _cell(text, row, idx, field, period=p)
            if e:
                ex.append(e)
    # 12-month changes in prose, when the table cell is not published.
    for f, rx in (("cpi.headline.yoy", r"The all items index (?:rose|increased|fell|decreased|declined) "
                                       rf"({N}) percent for the 12 months ending {mon}"),
                  ("cpi.core.yoy", r"The all items less food and energy index (?:rose|increased|fell|decreased|declined) "
                                   rf"({N}) percent over the last 12 months")):
        if not any(e.field == f for e in ex):
            m = re.search(rx, text)
            if m:
                ex.append(span(text, m.start(1), m.end(1), f, unit="growth_pct", period=per, doc="release"))
    return text, ex


def parse_ppi(content: bytes) -> tuple[str, list[Extraction]]:
    """Table A: year rows ('2026 | ') then month rows 'Oct. | FD | FD less f,e,t | goods x4 | services x4 | 12m FD
    | 12m FD less f,e,t'."""
    text = release_text(content)
    per = period(text)
    ex: list[Extraction] = []
    blk = re.search(r"Table A\. Monthly and 12-month percent changes in selected final demand price indexes", text)
    if blk is None or per is None:
        return text, ex
    s = blk.end()
    end = text.find("\nFootnotes", s)
    stop = end if end != -1 else len(text)
    h0 = text.find("\n", s) + 1
    hdr = text[h0:text.find("\n", h0)].split(" | ")
    if not (len(hdr) >= 4 and "less foods, energy, and trade" in hdr[2] and "12 months ago" in hdr[-2]):
        return text, ex            # layout changed: refuse to guess columns

    def month_row(p: str):
        mon, yr = p.split()
        y = re.compile(rf"^{yr} \|.*$", re.M).search(text, s, stop)
        if y is None:
            return None
        nxt = re.compile(r"^\d{4} \|.*$", re.M).search(text, y.end(), stop)
        return _find_row(text, y.end(), _month_label(mon), nxt.start() if nxt else stop)

    prior_per = _prev_month(per)
    row, prow = month_row(per), month_row(prior_per)
    for r, p, suf in ((row, per, ""), (prow, prior_per, ".prior")):
        for idx, f in ((1, "ppi.fd.mom"), (-2, "ppi.fd.yoy"), (2, "ppi.core.mom"), (-1, "ppi.core.yoy")):
            e = _cell(text, r, idx, f + suf, period=p)
            if e:
                ex.append(e)
    return text, ex


# ---- Employment Situation ------------------------------------------------------------------------------

_UP = r"rose|increased|edged up|was up|grew|added"
_DOWN = r"fell|declined|decreased|edged down|was down"


def parse_empsit(content: bytes) -> tuple[str, list[Extraction]]:
    text = release_text(content)
    per = period(text)
    ex: list[Extraction] = []
    if per is None:
        return text, ex
    mon = per.split()[0]
    d = "release"

    def add(field, m, g=1, unit="jobs", p=per):
        if m:
            ex.append(span(text, m.start(g), m.end(g), field, unit=unit, period=p, doc=d))

    # Payrolls. Lead sentence: "Total nonfarm payroll employment rose by 165,000 in October" /
    # "... changed little (+22,000) in September" / "... fell by 32,000".
    lead = re.search(r"Total nonfarm payroll employment[^.]*?\.", text)
    if lead:
        sub = lead.group(0)
        off = lead.start()
        m = re.search(rf"(?:{_UP}) by ([\d,]+)", sub)
        if m:
            ex.append(span(text, off + m.start(1), off + m.end(1), "nfp.change", unit="jobs", period=per, doc=d))
        else:
            m = re.search(rf"(?:{_DOWN}) by ([\d,]+)", sub)
            if m:
                ex.append(span(text, off + m.start(1), off + m.end(1), "nfp.change_down", unit="jobs", period=per,
                               doc=d))
            else:
                m = re.search(r"\(([+-]?[\d,]+)\)", sub)
                if m:
                    ex.append(span(text, off + m.start(1), off + m.end(1), "nfp.change", unit="jobs", period=per,
                                   doc=d))
    # Unemployment rate, from the same lead sentence or the household paragraph.
    add("ur", re.search(r"the unemployment rate [^.]*?(?:at|to|of) (\d+\.\d) percent", text), unit="percent")
    # Revised prior month: "the change for September was revised up by 31,000, from +29,000 to +60,000"
    prior = _prev_month(per).split()[0]
    add("nfp.change.prior", re.search(
        rf"change for {prior} was revised (?:up|down) by [\d,]+, from [+-]?[\d,]+ to ([+-]?[\d,]+)", text),
        p=_prev_month(per))
    if not any(e.field == "nfp.change.prior" for e in ex):
        add("nfp.change.prior", re.search(rf"change for {prior} was (?:unrevised|unchanged) at ([+-]?[\d,]+)", text),
            p=_prev_month(per))
    # Average hourly earnings: "average hourly earnings for all employees on private nonfarm payrolls increased
    # by 14 cents, or 0.4 percent, to $38.00. Over the past 12 months, average hourly earnings have increased by
    # 3.1 percent."
    m = re.search(rf"average hourly earnings for all employees on private nonfarm payrolls ({_UP}|{_DOWN}) by "
                  rf"\d+ cents?, or (\d+\.\d) percent, to (\$\d[\d,]*\.\d{{2}})", text)
    if m:
        down = re.fullmatch(_DOWN, m.group(1)) is not None
        add("ahe.mom_down" if down else "ahe.mom", m, 2, unit="growth_pct")
        add("ahe.level", m, 3, unit="usd")
    else:
        add("ahe.level", re.search(r"average hourly earnings for all employees on private nonfarm payrolls "
                                   r"(?:were|was|remained) (?:unchanged|little changed) at (\$\d[\d,]*\.\d{2})", text),
            unit="usd")
    m = re.search(rf"Over the (?:past|last) 12 months, average hourly earnings have ({_UP}|{_DOWN}) by (\d+\.\d) "
                  r"percent", text)
    if m:
        add("ahe.yoy_down" if re.fullmatch(_DOWN, m.group(1)) else "ahe.yoy", m, 2, unit="growth_pct")
    return text, ex


# ---- JOLTS ----------------------------------------------------------------------------------------------

def parse_jolts(content: bytes) -> tuple[str, list[Extraction]]:
    text = release_text(content)
    per = period(text)
    ex: list[Extraction] = []
    if per is None:
        return text, ex
    d = "release"
    # Job Openings section: "The number and rate of job openings were little changed at 7.5 million and 4.5
    # percent, respectively, in September." or "The number of job openings decreased to 7.1 million (-325,000),
    # and the rate decreased to 4.3 percent."
    sec = re.search(r"^Job Openings$", text, re.M)
    s = sec.end() if sec else 0
    m = re.compile(r"The number and rate of job openings [^.]*?(?:at|to) (\d+\.\d) million and (\d+\.\d) percent"
                   ).search(text, s)
    if m:
        ex.append(span(text, m.start(1), m.end(1), "jolts.openings", unit="millions", period=per, doc=d))
        ex.append(span(text, m.start(2), m.end(2), "jolts.rate", unit="percent", period=per, doc=d))
        return text, ex
    m = re.compile(r"(?:number of )?job openings [^.]*?(?:at|to) (\d+\.\d) million").search(text, s)
    if m is None:
        m = re.search(r"job openings [^.]*?(?:at|to) (\d+\.\d) million", text)
    if m:
        ex.append(span(text, m.start(1), m.end(1), "jolts.openings", unit="millions", period=per, doc=d))
        r = re.compile(r"(?:the )?(?:job openings )?rate [^.]*?(?:at|to) (\d+\.\d) percent").search(text, m.end())
        if r and r.start() - m.end() < 200:
            ex.append(span(text, r.start(1), r.end(1), "jolts.rate", unit="percent", period=per, doc=d))
    return text, ex


PARSERS = {"cpi": parse_cpi, "nfp": parse_empsit, "ppi": parse_ppi, "jolts": parse_jolts}
