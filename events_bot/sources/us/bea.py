"""BEA real-time print alerts: GDP (advance / second / third estimate) and Personal Income and Outlays.

Calendar (phase0/us_data.md): the keyless `release_dates.json` ({release name: {release_dates: [ISO UTC]}})
is the primary schedule; the ICS (`online-calendar-subscription.ics`, SUMMARY names the vintage and period,
e.g. "GDP (Second Estimate) and Corporate Profits, 3rd Quarter 2026") is the cross-check and supplies the
expected period. When the JSON is down the ICS alone drives the calendar.

Release detection: the news RSS (`apps.bea.gov/rss/rss.xml`, LM within ~2 min of 08:30). capture() returns
None until the RSS carries an item of this kind stamped on the release date (and, when the calendar named
one, for the expected period). The release page URL is the RSS item's link, never constructed; one RSS link
lacks the scheme ("www.bea.gov/news/..."), so it is normalised. The page heading must name the same period
(and, for GDP, the same estimate vintage) as the RSS item, or the page is treated as a stale copy. Note the
RSS writes "GDP, (Second Estimate) ..." with a comma the page heading does not have.

Figures are spans of the release page text (`article`): GDP from the headline sentence and the "Real GDP
and Related Measures" table; PIO from the "Personal Income and Related Measures" table (signed, prior and
current month) and the 12-month PCE sentences. The BEA data API needs a key and answers HTTP 200 with an
error JSON when it is missing, so it is not used.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from xml.etree import ElementTree as ET

import structlog

from ...core.fetch import FetchError
from ...core.timeutil import ET as NY
from ...core.timeutil import UTC, parse_rfc822, utcnow
from ...extract.base import Extraction, html_text
from ...extract.us_release_text import cell, row_cells, signed, text_field
from ...stage1.render import Doc
from ...stage1.usprint import Capture, Card, PrintSpec
from .prints_base import ScheduledPrint, ScheduledRelease, at_et

log = structlog.get_logger()

JSON_NAMES = {"Gross Domestic Product": "gdp", "Personal Income and Outlays": "pce"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
_Q = r"(\d)(?:st|nd|rd|th) Quarter(?: and Year)? (\d{4})"
_M = rf"({'|'.join(MONTHS)}) (\d{{4}})"

PRINTS = {
    "gdp": PrintSpec(
        kind="gdp", name="GDP", tag="BEA",
        headline=(("Real GDP q/q saar", "bea.gdp.real"), ("prior quarter", "bea.gdp.real_prior_q")),
        cards=(Card("Real GDP, annualized q/q", "bea.gdp.real", prior="bea.gdp.real_prior_q",
                    unit="per cent, SAAR (prior = previous quarter)"),
               Card("Previous estimate of this quarter", "bea.gdp.real_prev_est", unit="per cent, SAAR"),
               Card("PCE price index", "bea.gdp.pce_price", unit="per cent, SAAR"),
               Card("PCE price index ex food and energy", "bea.gdp.core_pce_price", unit="per cent, SAAR")),
        required=("bea.gdp.real", "bea.gdp.real_prior_q")),
    "pce": PrintSpec(
        kind="pce", name="PCE inflation and personal income", tag="BEA",
        headline=(("PCE prices m/m", "bea.pce.price_mm"), ("y/y", "bea.pce.price_yy"),
                  ("core m/m", "bea.pce.core_mm"), ("core y/y", "bea.pce.core_yy")),
        cards=(Card("PCE price index, m/m", "bea.pce.price_mm", prior="bea.pce.price_mm_prior"),
               Card("PCE price index, 12-month", "bea.pce.price_yy"),
               Card("Core PCE price index (ex food and energy), m/m", "bea.pce.core_mm", prior="bea.pce.core_mm_prior"),
               Card("Core PCE price index, 12-month", "bea.pce.core_yy"),
               Card("Personal income, m/m", "bea.pce.income_mm", prior="bea.pce.income_mm_prior"),
               Card("Personal spending (current-dollar PCE), m/m", "bea.pce.spending_mm",
                    prior="bea.pce.spending_mm_prior"),
               Card("Real PCE, m/m", "bea.pce.real_pce_mm", prior="bea.pce.real_pce_mm_prior")),
        required=("bea.pce.price_mm", "bea.pce.price_yy", "bea.pce.core_mm", "bea.pce.core_yy"),
        table_title="Percent change from preceding month"),
}


# ---- calendar -----------------------------------------------------------------------------------
def _et_release(dt: datetime) -> datetime:
    local = dt.astimezone(NY)
    return at_et(local.date(), local.hour, local.minute)


def parse_release_dates(content: bytes) -> list[tuple[str, datetime, str]]:
    """[(kind, release_at ET, json release name)] from release_dates.json."""
    data = json.loads(content)
    out = []
    for name, kind in JSON_NAMES.items():
        rec = data.get(name)
        if not isinstance(rec, dict):
            continue
        for s in rec.get("release_dates") or []:
            out.append((kind, _et_release(datetime.fromisoformat(s)), name))
    return out


def ics_kind(summary: str) -> str | None:
    if summary.startswith("GDP ("):
        return "gdp"
    if summary.startswith("Personal Income and Outlays"):
        return "pce"
    return None


def parse_ics(content: bytes) -> list[tuple[str, datetime, str]]:
    """[(kind, release_at ET, SUMMARY verbatim)] for GDP and PIO events of the BEA ICS (times are UTC)."""
    raw = content.decode("utf-8-sig", "replace").replace("\r\n", "\n")
    raw = re.sub(r"\n[ \t]", "", raw)                      # RFC 5545 line unfolding
    out = []
    for block in raw.split("BEGIN:VEVENT")[1:]:
        sm = re.search(r"^SUMMARY[^:\n]*:(.*)$", block, re.M)
        dm = re.search(r"^DTSTART[^:\n]*:(\d{8}T\d{6})(Z?)$", block, re.M)
        if not sm or not dm:
            continue
        summary = re.sub(r"\\([,;\\])", r"\1", sm.group(1)).strip()
        kind = ics_kind(summary)
        if kind is None:
            continue
        dt = datetime.strptime(dm.group(1), "%Y%m%dT%H%M%S")
        dt = dt.replace(tzinfo=UTC) if dm.group(2) else dt.replace(tzinfo=NY)
        out.append((kind, _et_release(dt), summary))
    return out


def period_key(kind: str, s: str) -> tuple | None:
    """("q", 3, 2026) / ("m", 10, 2026) from a calendar / RSS / page title; None if it names no period."""
    if kind == "gdp":
        m = re.search(_Q, s)
        return ("q", int(m.group(1)), int(m.group(2))) if m else None
    m = re.search(_M, s)
    return ("m", MONTHS.index(m.group(1)) + 1, int(m.group(2))) if m else None


# ---- RSS ------------------------------------------------------------------------------------------
def rss_items(content: bytes) -> list[dict]:
    """Plain RSS 2.0 items: {title, link, published (raw), pubdate (UTC), attrs, description}. ElementTree,
    not feedparser: feedparser resolves BEA's scheme-less links against the channel link."""
    if b"<!DOCTYPE" in content[:2048] or b"<!ENTITY" in content:
        raise ValueError("RSS with a DTD is refused")
    root = ET.fromstring(content)
    out = []
    for it in root.iter("item"):
        def txt(tag: str) -> str:
            return (it.findtext(tag) or "").strip()
        out.append({"title": re.sub(r"\s+", " ", txt("title")), "link": txt("link"), "guid": txt("guid"),
                    "published_raw": txt("pubDate"), "published": parse_rfc822(txt("pubDate")),
                    "description": it.findtext("description") or "", "attrs": dict(it.attrib)})
    return out


def parse_rss(content: bytes) -> list[dict]:
    out = []
    for it in rss_items(content):
        it["link"] = norm_link(it["link"] or it["guid"])
        out.append(it)
    return out


def norm_link(link: str) -> str:
    link = link.strip()
    if link and not re.match(r"^https?://", link):
        link = "https://" + link.lstrip("/")
    return link


def rss_kind(title: str) -> str | None:
    if re.match(r"^GDP,? \((?:Advance|Second|Third) Estimate\)", title):   # the RSS writes "GDP, (Second ..."
        return "gdp"
    if title.startswith("Personal Income and Outlays,"):
        return "pce"
    return None


def vintage(s: str) -> str | None:
    m = re.search(r"\((Advance|Second|Third) Estimate\)", s)
    return m.group(1) if m else None


def match_item(items: list[dict], kind: str, release_at: datetime, expected: tuple | None) -> dict | None:
    day = release_at.astimezone(NY).date()
    for it in items:
        if rss_kind(it["title"]) != kind or it["published"] is None:
            continue
        if it["published"].astimezone(NY).date() != day:
            continue                                     # an older release of the same kind
        if expected is not None and period_key(kind, it["title"]) != expected:
            log.warning("bea_rss_period_mismatch", kind=kind, title=it["title"], expected=str(expected))
            continue
        return it
    return None


# ---- release page -----------------------------------------------------------------------------------
def page_text(content: bytes) -> str:
    return html_text(content, "article")


def _lead_paragraphs(text: str, title: str, table_head: str, n: int = 5) -> list[str]:
    a = text.find(title)
    b = text.find(table_head, a + 1 if a >= 0 else 0)
    if a < 0:
        return []
    body = text[a + len(title): b if b > a else len(text)]
    return [ln for ln in body.split("\n") if len(ln) >= 80][:n]


def parse_gdp(text: str) -> tuple[str, list[Extraction], list[str]]:
    """(period label, extractions, verbatim lead paragraphs) of a GDP release page text."""
    doc = "release"
    ex: list[Extraction] = []
    tm = re.search(rf"^GDP \((Advance|Second|Third) Estimate\)[^\n]*?{_Q}[^\n]*$", text, re.M)
    if not tm:
        return "", [], []
    title = tm.group(0)
    period = f"{tm.group(2)}{_ord(tm.group(2))} Quarter {tm.group(3)}"
    ex.append(text_field(text, tm, 1, "bea.gdp.vintage", doc=doc))
    # headline sentence (sign in the verb); the table's last column is the same number, signed
    hm = re.search(r"Real gross domestic product \(GDP ?\) (increased|decreased) at an annual rate of "
                   r"(\d+(?:\.\d+)?) percent in the (\w+) quarter of (\d{4})", text)
    tab = text.find("Real GDP and Related Measures")
    cells = row_cells(text, re.compile(r"^Real GDP$", re.M), start=max(tab, 0)) if tab >= 0 else []
    cur = cell(text, cells, len(cells) - 1, "bea.gdp.real", period=period, doc=doc) if cells else None
    if cur is None and hm:
        cur = signed(text, hm, 1, 2, "bea.gdp.real", period=period, doc=doc)
    if cur is not None and hm and cur.value_norm is not None and \
            abs(cur.value_norm) != abs(signed(text, hm, 1, 2, "x", doc=doc).value_norm or 0):
        log.warning("bea_gdp_table_sentence_disagree", table=cur.value_text, sentence=hm.group(2))
        cur = signed(text, hm, 1, 2, "bea.gdp.real", period=period, doc=doc)     # the sentence is anchored
    if cur is not None:
        ex.append(cur)
    if len(cells) >= 2:
        prev = cell(text, cells, len(cells) - 2, "bea.gdp.real_prev_est", period=f"{period}, previous estimate",
                    doc=doc)
        if prev is not None:
            ex.append(prev)
    pm = re.search(r"In the (\w+) quarter(?: of \d{4})?, real GDP (increased|decreased) (\d+(?:\.\d+)?) percent", text)
    if pm:
        ex.append(signed(text, pm, 2, 3, "bea.gdp.real_prior_q", period=f"{pm.group(1)} quarter", doc=doc))
    for field, label in (("bea.gdp.pce_price", r"^PCE price index$"),
                         ("bea.gdp.core_pce_price", r"^PCE price index,? excluding food and energy$")):
        c = row_cells(text, re.compile(label, re.M), start=max(tab, 0)) if tab >= 0 else []
        e = cell(text, c, len(c) - 1, field, period=period, doc=doc) if c else None
        if e is not None:
            ex.append(e)
    return f"{period} ({tm.group(1)} Estimate)", ex, _lead_paragraphs(text, title, "Real GDP and Related Measures")


def _ord(d: str) -> str:
    return {"1": "st", "2": "nd", "3": "rd"}.get(d, "th")


PIO_ROWS = (("income_mm", "Current-dollar personal income"), ("spending_mm", "Current-dollar PCE"),
            ("real_pce_mm", "Real PCE"), ("price_mm", "PCE price index"),
            ("core_mm", "PCE price index excluding food and energy"))


def parse_pio(text: str) -> tuple[str, list[Extraction], list[str]]:
    doc = "release"
    tm = re.search(rf"^Personal Income and Outlays, {_M}$", text, re.M)
    if not tm:
        return "", [], []
    period = f"{tm.group(1)} {tm.group(2)}"
    ex: list[Extraction] = []
    head = "Personal Income and Related Measures"
    tab = text.find(head)
    if tab >= 0:
        hm = re.compile(rf"^{re.escape(head)}[^\n]*\n({'|'.join(MONTHS)})\n({'|'.join(MONTHS)})$", re.M).search(text, tab)
        p_prior, p_cur = (hm.group(1), hm.group(2)) if hm else ("prior month", tm.group(1))
        for key, label in PIO_ROWS:
            c = row_cells(text, re.compile(rf"^{re.escape(label)}$", re.M), start=tab, limit=2)
            if len(c) == 2:
                e = cell(text, c, 1, f"bea.pce.{key}", period=p_cur, doc=doc)
                p = cell(text, c, 0, f"bea.pce.{key}_prior", period=p_prior, doc=doc)
                ex += [x for x in (e, p) if x is not None]
    ym = re.search(r"From the same month one year ago, the PCE price index for \w+ (increased|decreased) "
                   r"(\d+(?:\.\d+)?) percent", text)
    if ym:
        ex.append(signed(text, ym, 1, 2, "bea.pce.price_yy", period=period, doc=doc))
    cm = re.search(r"Excluding food and energy, the PCE price index (increased|decreased) (\d+(?:\.\d+)?) percent "
                   r"from one year ago", text)
    if cm:
        ex.append(signed(text, cm, 1, 2, "bea.pce.core_yy", period=period, doc=doc))
    return period, ex, _lead_paragraphs(text, tm.group(0), head)


PARSERS = {"gdp": parse_gdp, "pce": parse_pio}


class BeaPrints(ScheduledPrint):
    doc_type = "bea"
    PRINTS = PRINTS

    def _get(self, name: str, url: str, expect: str):
        return self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"bea:{name}", expect=expect,
                                    conditional=False)

    def schedule(self) -> list[ScheduledRelease]:
        json_rows, ics_rows, errors = None, None, []
        try:
            json_rows = parse_release_dates(self._get("release_dates", self.cfg.urls["release_dates"], "json").content)
        except (FetchError, ValueError) as e:
            errors.append(f"release_dates.json: {e}")
        try:
            res = self._get("ics", self.cfg.urls["ics"], "any")
            if not res.content.lstrip(b"\xef\xbb\xbf \r\n").startswith(b"BEGIN:VCALENDAR"):
                raise FetchError(self.cfg.urls["ics"], "bad_content", "not an iCalendar file", 200)
            ics_rows = parse_ics(res.content)
        except FetchError as e:
            errors.append(f"ics: {e}")
        if json_rows is None and ics_rows is None:
            raise FetchError(self.cfg.urls["release_dates"], "calendar", "; ".join(errors))
        ics_at = {(k, at): s for k, at, s in (ics_rows or [])}
        rels: list[ScheduledRelease] = []
        if json_rows is not None:
            for kind, at, name in json_rows:
                summary = ics_at.get((kind, at))
                meta = {"calendar_source": "release_dates.json", "ics_confirmed": summary is not None}
                if ics_rows is not None and summary is None and at > utcnow():
                    log.warning("bea_calendar_ics_disagrees", kind=kind, at=at.isoformat())
                rels.append(ScheduledRelease(kind, at, self.cfg.urls["release_dates"], summary or name, meta))
        else:
            rels = [ScheduledRelease(k, at, self.cfg.urls["ics"], s, {"calendar_source": "ics"})
                    for k, at, s in ics_rows or []]
        now = utcnow()
        for kind in PRINTS:
            if not any(r.kind == kind and r.release_at > now for r in rels):
                raise FetchError(self.cfg.urls["release_dates"], "shape", f"no future {kind} release date")
        return rels

    def capture(self, rel: ScheduledRelease, meta: dict) -> Capture | None:
        items = parse_rss(self._get("rss", self.cfg.urls["rss"], "xml").content)
        expected = period_key(rel.kind, rel.title) if rel.title else None
        it = match_item(items, rel.kind, rel.release_at, expected)
        if it is None:
            return None                                  # not in the feed yet (or still the previous period)
        try:
            res = self._get(f"{rel.kind}_page", it["link"], "html")
        except FetchError as e:
            if e.kind == "http" and e.status == 404:
                return None
            raise
        return self.build_capture(rel, it, res.content)

    @staticmethod
    def build_capture(rel: ScheduledRelease, it: dict, page: bytes) -> Capture | None:
        """Pure: RSS item + release page bytes -> Capture (None if the page is not this release yet)."""
        text = page_text(page)
        period, ex, bullets = PARSERS[rel.kind](text)
        if not period or period_key(rel.kind, period) != period_key(rel.kind, it["title"]) or \
                (rel.kind == "gdp" and vintage(period) != vintage(it["title"])):
            log.warning("bea_stale_page", url=it["link"], title=it["title"], page_period=period)
            return None                                  # a cached copy of the previous release
        return Capture(period=period, texts={"release": text}, extractions=ex,
                       docs=[Doc("News release", it["link"])], bullets=bullets,
                       release_at=it["published"] or rel.release_at)
