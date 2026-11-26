"""Census real-time print alerts: advance retail sales (MARTS) and advance durable goods orders (M3).

Calendar: `economic-indicators/calendar-listview.html` (no ICS/JSON exists; phase0/us_data.md). Each row holds
the indicator, the visible release date and time, the period covered, and a hidden anchor `AYYYYMMDDHHMM`.
The visible date is used: on the live 2026 page the anchor of the Feb-2026 durables row says 27 Apr while
the date column says 7 Apr (a page typo), so the anchor is only a cross-check (meta `anchor_mismatch`).
In late November the main page already shows next year, so the page's own links to
`calendar-listview-YYYY.html` for the current year are followed too. Health: the main page must parse to
>= 80 events.

Release detection and figures: the release PDFs (`marts_current.pdf`, `durgd.pdf`, overwritten in place at
08:30; durgd LM 08:30:08). A PDF is this release only when its "FOR RELEASE AT ..." line names the release
date and its title names the calendar's period; until then capture() returns None. `indicator.xml` (one item
per indicator; pubDate is a stamp and the channel is rebuilt about hourly) is the fallback after
FALLBACK_AFTER when the PDF is unavailable or stale: headline and prior only, detected by period change.
The Census data API needs a key (redirects to missing_key.html with HTTP 200), so it is not used.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import structlog
from selectolax.parser import HTMLParser

from ...core.fetch import FetchError
from ...core.timeutil import ET as NY
from ...core.timeutil import utcnow
from ...digest.content import pdf_body
from ...extract.base import Extraction, span
from ...extract.us_release_text import cell, line_from, row_cells
from ...stage1.render import Doc
from ...stage1.usprint import Capture, Card, PrintSpec
from .bea import MONTHS, rss_items
from .prints_base import ScheduledPrint, ScheduledRelease, at_et

log = structlog.get_logger()

FALLBACK_AFTER = timedelta(minutes=10)
MIN_EVENTS = 80
TITLES = {"retail": "Advance Monthly Sales for Retail and Food Services",
          "durables": "Advance Report on Durable Goods"}
GUIDS = {"retail": "retail_sales", "durables": "durable_goods"}

PRINTS = {
    "retail": PrintSpec(
        kind="retail", name="Retail sales", tag="CENSUS",
        headline=(("Retail sales m/m", "census.retail.total_mm"), ("ex autos", "census.retail.exauto_mm")),
        cards=(Card("Retail and food services, m/m", "census.retail.total_mm", prior="census.retail.total_mm_prior"),
               Card("Ex motor vehicles and parts, m/m", "census.retail.exauto_mm",
                    prior="census.retail.exauto_mm_prior"),
               Card("Ex motor vehicles, parts and gasoline, m/m", "census.retail.exautogas_mm",
                    prior="census.retail.exautogas_mm_prior"),
               Card("Retail and food services, y/y", "census.retail.total_yy")),
        required=("census.retail.total_mm", "census.retail.total_mm_prior"),
        table_title="Percent change from preceding month (prior month revised)"),
    "durables": PrintSpec(
        kind="durables", name="Durable goods orders", tag="CENSUS",
        headline=(("Durable goods orders m/m", "census.durables.total_mm"),
                  ("ex transportation", "census.durables.extrans_mm")),
        cards=(Card("New orders, durable goods, m/m", "census.durables.total_mm",
                    prior="census.durables.total_mm_prior"),
               Card("Ex transportation, m/m", "census.durables.extrans_mm", prior="census.durables.extrans_mm_prior"),
               Card("Ex defense, m/m", "census.durables.exdef_mm", prior="census.durables.exdef_mm_prior"),
               Card("Nondefense capital goods ex aircraft, m/m", "census.durables.core_capex_mm",
                    prior="census.durables.core_capex_mm_prior")),
        required=("census.durables.total_mm", "census.durables.total_mm_prior"),
        table_title="New orders, percent change from preceding month (prior month revised)"),
}


# ---- calendar -----------------------------------------------------------------------------------------
def calendar_kind(title: str) -> str | None:
    for k, t in TITLES.items():
        if title.startswith(t):
            return k
    return None


def parse_calendar(content: bytes) -> list[dict]:
    """Every row of a list-view page: {title, kind, release_at (ET), period, anchor, anchor_mismatch}."""
    tree = HTMLParser(content.decode("utf-8", "replace"))
    out = []
    for tr in tree.css("table#calendar tr"):
        tds = tr.css("td")
        if len(tds) < 5 or tds[0].css_first("a") is None:
            continue
        title = re.sub(r"\s+", " ", tds[0].text(strip=True))
        d_txt, t_txt, period = (re.sub(r"\s+", " ", tds[i].text(strip=True)) for i in (1, 2, 3))
        try:
            d = datetime.strptime(d_txt, "%B %d, %Y").date()
            t = datetime.strptime(t_txt.replace(".", "").upper(), "%I:%M %p").time()
        except ValueError:                           # "Suspended" and similar
            if calendar_kind(title):
                log.warning("census_calendar_row_unparsed", title=title, date=d_txt, time=t_txt)
            continue
        anchor = next((m.group(1) for td in tds[4:] if (m := re.fullmatch(r"A(\d{12})", td.text(strip=True)))), None)
        rel = at_et(d, t.hour, t.minute)
        out.append({"title": title, "kind": calendar_kind(title), "release_at": rel, "period": period,
                    "anchor": anchor, "anchor_mismatch": bool(anchor and anchor != rel.strftime("%Y%m%d%H%M"))})
    return out


def year_pages(content: bytes, base: str) -> dict[int, str]:
    """The page's own links to other years' list views: {year: absolute url}."""
    out = {}
    for m in re.finditer(rb'href="([^"]*calendar-listview-(\d{4})\.html)"', content):
        out[int(m.group(2))] = urljoin(base, m.group(1).decode())
    return out


# ---- release PDFs -----------------------------------------------------------------------------------------
def pdf_text(pdf: bytes) -> str:
    """One line per PyMuPDF text line, pages joined: table cells land one per line (row_cells reads them)."""
    import pymupdf
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(p.get_text() for p in doc)


_REL = re.compile(r"FOR RELEASE AT (\d{1,2}:\d{2} [AP]M) E[SD]T, [A-Z]+, ([A-Z]+ \d{1,2}, \d{4})")


def release_line(text: str) -> tuple[date, str] | None:
    m = _REL.search(text)
    if not m:
        return None
    return datetime.strptime(m.group(2).title(), "%B %d, %Y").date(), m.group(0)


def pdf_is(kind: str, text: str, day: date, period: str | None) -> bool:
    """True when the PDF is the release of `day` (and, when known, for `period`)."""
    rl = release_line(text)
    if rl is None or rl[0] != day:
        return False
    if not period:
        return True
    head = text[: text.find("Release Number") if "Release Number" in text else 3000]
    return re.sub(r"\s+", " ", period.upper()) in re.sub(r"\s+", " ", head)


def prev_month(period: str) -> str:
    m = re.fullmatch(rf"({'|'.join(MONTHS)}) (\d{{4}})", period)
    if not m:
        return "prior month"
    i, y = MONTHS.index(m.group(1)), int(m.group(2))
    return f"{MONTHS[i - 1]} {y - 1 if i == 0 else y}"


def _pair(text: str, spans, cur_i: int, prior_i: int, field: str, period: str, doc: str) -> list[Extraction]:
    out = []
    c = cell(text, spans, cur_i, field, period=period, doc=doc)
    p = cell(text, spans, prior_i, f"{field}_prior", period=f"{prev_month(period)} (r)", doc=doc)
    return [x for x in (c, p) if x is not None]


def parse_retail(text: str, period: str) -> list[Extraction]:
    """Table 2 (percent change): per row the cells are [cur m/m, cur y/y, prior m/m (r), prior y/y, 3m, 3m y/y]."""
    doc, ex = "pdf", []
    t2 = text.find("Table 2.")
    if t2 < 0:
        return []
    rows = (("census.retail.total_mm", re.compile(r"Retail & food services,\s*\n\s*total")),
            ("census.retail.exauto_mm", re.compile(r"Total \(excl\. motor vehicle & parts\)(?! &)")),
            ("census.retail.exautogas_mm",
             re.compile(r"Total \(excl\. motor vehicle & parts &\s*\n(?:\s*\n)*\s*gasoline stations\)")))
    for field, rx in rows:
        s = row_cells(text, rx, start=t2, limit=6)
        ex += _pair(text, s, 0, 2, field, period, doc)
        if field == "census.retail.total_mm":
            yy = cell(text, s, 1, "census.retail.total_yy", period=period, doc=doc)
            if yy is not None:
                ex.append(yy)
    return ex


def parse_durables(text: str, period: str) -> list[Extraction]:
    """Table 1: "New Orders" rows; cells [cur level, prior level, 2-back level, cur m/m, prior m/m (r), ...]."""
    doc, ex = "pdf", []
    t1 = text.find("Table 1.")
    if t1 < 0:
        return []
    orders = re.compile(r"New Orders\d*[^\n]*")
    for field, anchors in (("census.durables.total_mm", ("Total:",)),
                           ("census.durables.extrans_mm", ("Excluding transportation:",)),
                           ("census.durables.exdef_mm", ("Excluding defense:",)),
                           ("census.durables.core_capex_mm", ("Nondefense capital goods:", "Excluding aircraft:"))):
        pos = t1
        for a in anchors:
            pos = text.find(a, pos)
            if pos < 0:
                break
        if pos < 0:
            continue
        s = row_cells(text, orders, start=pos, limit=5)
        ex += _pair(text, s, 3, 4, field, period, doc)
    return ex


PARSERS = {"retail": parse_retail, "durables": parse_durables}
LEADS = {"retail": "Advance estimates of U.S. retail and food services sales for",
         "durables": "New orders for manufactured durable goods in"}


def bullets(kind: str, pdf: bytes) -> list[str]:
    body = pdf_body(pdf)
    lead = line_from(body, LEADS[kind])
    out = [lead] if lead else []
    if kind == "durables":
        ship = line_from(body, "Shipments of manufactured durable goods in")
        if ship:
            out.append(ship)
    return out


# ---- indicator.xml fallback ---------------------------------------------------------------------------------
def indicator_doc(item: dict) -> str:
    """Deterministic text of one indicator.xml item: title, then the description with <br>/<p> as lines."""
    desc = re.sub(r"<br\s*/?>|</?p>", "\n", item["description"], flags=re.I)
    desc = re.sub(r"<[^>]+>", "", desc)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in desc.split("\n")]
    return "\n".join([item["title"]] + [ln for ln in lines if ln])


_IND = re.compile(rf"^((?:{'|'.join(MONTHS)}) \d{{4}})( \(r\))?: ([+-]?\d+(?:\.\d+)?)", re.M)


def parse_indicator(kind: str, text: str) -> tuple[str | None, list[Extraction]]:
    rows = list(_IND.finditer(text))
    if not rows:
        return None, []
    field = "census.retail.total_mm" if kind == "retail" else "census.durables.total_mm"
    cur = rows[0]
    ex = [span(text, cur.start(3), cur.end(3), field, unit="growth_pct", period=cur.group(1), doc="indicator")]
    prior = next((r for r in rows[1:] if r.group(2)), rows[1] if len(rows) > 1 else None)
    if prior is not None:
        ex.append(span(text, prior.start(3), prior.end(3), f"{field}_prior", unit="growth_pct",
                       period=f"{prior.group(1)}{prior.group(2) or ''}", doc="indicator"))
    return cur.group(1), ex


class CensusPrints(ScheduledPrint):
    doc_type = "census"
    PRINTS = PRINTS

    def _get(self, name: str, url: str, expect: str):
        return self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"census:{name}", expect=expect,
                                    conditional=False)

    def schedule(self) -> list[ScheduledRelease]:
        url = self.cfg.urls["calendar"]
        main = self._get("calendar", url, "html")
        rows = parse_calendar(main.content)
        if len(rows) < MIN_EVENTS:
            raise FetchError(url, "shape", f"only {len(rows)} calendar rows parsed (< {MIN_EVENTS})")
        this_year = utcnow().astimezone(NY).year
        for year, link in sorted(year_pages(main.content, url).items()):
            if year >= this_year and link != url:
                rows += parse_calendar(self._get(f"calendar_{year}", link, "html").content)
        out = []
        for r in rows:
            if r["kind"] is None:
                continue
            if r["anchor_mismatch"] and r["release_at"] > utcnow():
                log.warning("census_calendar_anchor_mismatch", title=r["title"], anchor=r["anchor"],
                            shown=r["release_at"].isoformat())
            out.append(ScheduledRelease(r["kind"], r["release_at"], url, r["title"],
                                        {"period": r["period"], "anchor": r["anchor"],
                                         "anchor_mismatch": r["anchor_mismatch"]}))
        now = utcnow()
        for kind in PRINTS:
            if not any(r.kind == kind and r.release_at > now for r in out):
                raise FetchError(url, "shape", f"no future {kind} release on the calendar")
        return out

    def capture(self, rel: ScheduledRelease, meta: dict) -> Capture | None:
        period = meta.get("period") or rel.meta.get("period")
        day = rel.release_at.astimezone(NY).date()
        pdf_url = self.cfg.urls[f"{rel.kind}_pdf"]
        try:
            res = self._get(f"{rel.kind}_pdf", pdf_url, "pdf")
            cap = self.build_capture(rel.kind, res.content, pdf_url, day, period, release_at=rel.release_at)
            if cap is not None:
                return cap
        except FetchError as e:
            log.warning("census_pdf_unavailable", kind=rel.kind, error=str(e)[:200])
        if utcnow() - rel.release_at < FALLBACK_AFTER:
            return None
        ind = self._get("indicator", self.cfg.urls["indicator"], "xml")
        return self.build_indicator_capture(rel.kind, ind.content, period, release_at=rel.release_at)

    @staticmethod
    def build_capture(kind: str, pdf: bytes, pdf_url: str, day: date, period: str | None, *,
                      release_at: datetime | None = None) -> Capture | None:
        """Pure: release PDF bytes -> Capture, or None while the PDF is still the previous release."""
        text = pdf_text(pdf)
        if not pdf_is(kind, text, day, period):
            return None
        if not period:
            m = re.search(rf"({'|'.join(m.upper() for m in MONTHS)}) (\d{{4}})", text[:3000])
            period = f"{m.group(1).title()} {m.group(2)}" if m else day.isoformat()
        ex = PARSERS[kind](text, period)
        return Capture(period=period, texts={"pdf": text}, extractions=ex,
                       docs=[Doc("Release (PDF)", pdf_url)], bullets=bullets(kind, pdf), release_at=release_at)

    def build_indicator_capture(self, kind: str, content: bytes, period: str | None, *,
                                release_at: datetime | None = None) -> Capture | None:
        item = next((i for i in rss_items(content) if i["guid"] == GUIDS[kind]), None)
        if item is None:
            return None
        text = indicator_doc(item)
        got, ex = parse_indicator(kind, text)
        if got is None or (period and got != period):
            return None                                  # still the previous period
        lead = next((ln for ln in text.split("\n")[1:] if len(ln) > 60), None)
        return Capture(period=got, texts={"indicator": text}, extractions=ex,
                       docs=[Doc("Economic Briefing Room (indicator.xml)", self.cfg.urls["indicator"]),
                             Doc("Release (PDF, not yet updated when this alert was built)",
                                 self.cfg.urls[f"{kind}_pdf"])],
                       bullets=[lead] if lead else [], release_at=release_at)
