"""BLS real-time print alerts: CPI, Employment Situation (payrolls), PPI, JOLTS.

Calendar: the BLS release ICS (`schedule/news_release/bls.ics`, TZID US-Eastern; Akamai-gated, so it is fetched
with the full browser header set, prints_base.BROWSER_HEADERS). SUMMARY carries the release name only; the four
names below map to kinds by exact match (so "State Job Openings and Labor Turnover" and "Employment Situation of
Veterans" are not picked up).

Release: `news.release/<code>.nr0.htm`, the canonical verbatim text (phase0/us_data.md). It has no ETag /
Last-Modified, so a new release is detected by content: the page's embargo line ("embargoed until 8:30 a.m. (ET)
Tuesday, November 10, 2026"; JOLTS "For release 10:00 a.m. (ET) ...") must name the scheduled release date.
While the previous release is still up, capture() returns None and nothing is sent.

Never used as a release time (phase0 flag 2): RSS pubDate (~07:50 ET) and PDF Last-Modified (~07:07 ET) are
pre-staged. The alert's official time is the embargo line; first-seen is our own clock.

The keyless BLS API (25 queries/day) is not used as a trigger; nothing here calls it.
"""
from __future__ import annotations

import re
from datetime import date

import structlog

from ...core.timeutil import ET
from ...extract import bls as bx
from ...stage1.render import Doc
from ...stage1.usprint import Capture, Card, PrintSpec
from .prints_base import BROWSER_HEADERS, ScheduledPrint, ScheduledRelease, at_et

log = structlog.get_logger()

BASE = "https://www.bls.gov"
ICS_URL = f"{BASE}/schedule/news_release/bls.ics"
CODES = {"cpi": "cpi", "nfp": "empsit", "ppi": "ppi", "jolts": "jolts"}
SUMMARY_KIND = {
    "Consumer Price Index": "cpi",
    "Employment Situation": "nfp",
    "Producer Price Index": "ppi",
    "Job Openings and Labor Turnover Survey": "jolts",
}
DOC_NAMES = {"cpi": "Consumer Price Index news release", "nfp": "The Employment Situation news release",
             "ppi": "Producer Price Index news release", "jolts": "Job Openings and Labor Turnover news release"}
BULLETS = {"cpi": 3, "nfp": 2, "ppi": 3, "jolts": 3}


def nr0_url(kind: str) -> str:
    return f"{BASE}/news.release/{CODES[kind]}.nr0.htm"


def pdf_url(kind: str) -> str:
    return f"{BASE}/news.release/pdf/{CODES[kind]}.pdf"


PRINTS: dict[str, PrintSpec] = {
    "cpi": PrintSpec(
        kind="cpi", name="US CPI", tag="BLS",
        headline=(("m/m", "cpi.headline.mom"), ("y/y", "cpi.headline.yoy"),
                  ("core m/m", "cpi.core.mom"), ("core y/y", "cpi.core.yoy")),
        cards=(Card("CPI-U all items, m/m (SA)", "cpi.headline.mom", "cpi.headline.mom.prior", "per cent"),
               Card("CPI-U all items, 12-month (NSA)", "cpi.headline.yoy", None, "per cent"),
               Card("Core (less food and energy), m/m (SA)", "cpi.core.mom", "cpi.core.mom.prior", "per cent"),
               Card("Core (less food and energy), 12-month (NSA)", "cpi.core.yoy", None, "per cent")),
        required=("cpi.headline.mom", "cpi.core.mom"),
        table_title="CPI-U, seasonally adjusted change from preceding month (per cent)"),
    "nfp": PrintSpec(
        kind="nfp", name="US Employment Situation", tag="BLS",
        headline=(("payrolls", "nfp.change"), ("payrolls fell by", "nfp.change_down"), ("UR", "ur"),
                  ("AHE m/m", "ahe.mom"), ("AHE m/m down", "ahe.mom_down")),
        cards=(Card("Nonfarm payrolls, change", "nfp.change", "nfp.change.prior", "jobs (prior = previous month, as revised)"),
               Card("Nonfarm payrolls, decline", "nfp.change_down", None, "jobs, fell by"),
               Card("Unemployment rate", "ur", None, "per cent"),
               Card("Average hourly earnings, m/m", "ahe.mom", None, "per cent"),
               Card("Average hourly earnings, m/m (decrease)", "ahe.mom_down", None, "per cent, decreased by"),
               Card("Average hourly earnings, 12-month", "ahe.yoy", None, "per cent"),
               Card("Average hourly earnings, 12-month (decrease)", "ahe.yoy_down", None, "per cent, decreased by"),
               Card("Average hourly earnings, level", "ahe.level", None, "US$ per hour")),
        required=("ur",)),
    "ppi": PrintSpec(
        kind="ppi", name="US PPI", tag="BLS",
        headline=(("final demand m/m", "ppi.fd.mom"), ("y/y", "ppi.fd.yoy"), ("core m/m", "ppi.core.mom")),
        cards=(Card("Final demand, m/m (SA)", "ppi.fd.mom", "ppi.fd.mom.prior", "per cent"),
               Card("Final demand, 12-month (NSA)", "ppi.fd.yoy", "ppi.fd.yoy.prior", "per cent"),
               Card("Final demand less foods, energy and trade, m/m (SA)", "ppi.core.mom", "ppi.core.mom.prior",
                    "per cent"),
               Card("Final demand less foods, energy and trade, 12-month (NSA)", "ppi.core.yoy",
                    "ppi.core.yoy.prior", "per cent")),
        required=("ppi.fd.mom", "ppi.fd.yoy"),
        table_title="PPI final demand (per cent; prior month as now published)"),
    "jolts": PrintSpec(
        kind="jolts", name="US JOLTS", tag="BLS",
        headline=(("job openings", "jolts.openings"), ("rate", "jolts.rate")),
        cards=(Card("Job openings", "jolts.openings", None, "million"),
               Card("Job openings rate", "jolts.rate", None, "per cent")),
        required=("jolts.openings", "jolts.rate")),
}


# ---- calendar (pure) -------------------------------------------------------------------------------------

def _unfold(ics: str) -> list[str]:
    """RFC 5545 line unfolding: a line starting with a space or tab continues the previous one."""
    out: list[str] = []
    for ln in ics.replace("\r\n", "\n").split("\n"):
        if ln[:1] in (" ", "\t") and out:
            out[-1] += ln[1:]
        else:
            out.append(ln)
    return out


_DT = re.compile(r"^DTSTART(?:;TZID=([^:;]+))?(?:;VALUE=DATE-TIME)?:(\d{8})T(\d{2})(\d{2})(\d{2})(Z?)$")


def parse_ics(content: bytes, calendar_url: str = ICS_URL) -> list[ScheduledRelease]:
    """BLS ICS -> releases of the four tracked kinds. Times are US-Eastern wall clock (DST via zoneinfo)."""
    out: list[ScheduledRelease] = []
    ev: dict | None = None
    for ln in _unfold(content.decode("utf-8-sig", "replace")):
        if ln == "BEGIN:VEVENT":
            ev = {}
        elif ln == "END:VEVENT" and ev is not None:
            kind = SUMMARY_KIND.get((ev.get("summary") or "").strip())
            if kind and ev.get("dt"):
                d, hh, mm, tzid, z = ev["dt"]
                if z or (tzid and tzid not in ("US-Eastern", "America/New_York")):
                    log.warning("bls_ics_unexpected_tz", tzid=tzid, z=z, summary=ev.get("summary"))
                else:
                    out.append(ScheduledRelease(kind=kind, release_at=at_et(d, hh, mm), calendar_url=calendar_url,
                                                title=ev["summary"].strip(), meta={"uid": ev.get("uid")}))
            ev = None
        elif ev is not None:
            if ln.startswith("SUMMARY:"):
                ev["summary"] = ln[len("SUMMARY:"):].replace("\\,", ",")
            elif ln.startswith("UID:"):
                ev["uid"] = ln[4:]
            else:
                m = _DT.match(ln)
                if m:
                    y = m.group(2)
                    ev["dt"] = (date(int(y[:4]), int(y[4:6]), int(y[6:])), int(m.group(3)), int(m.group(4)),
                                m.group(1), m.group(6))
    out.sort(key=lambda r: r.release_at)
    return out


# ---- release (pure) --------------------------------------------------------------------------------------

def build_capture(kind: str, content: bytes, release_date: date) -> Capture | None:
    """Pure: archived nr0 bytes -> Capture, or None when the page is not the release scheduled for
    `release_date` (previous release still up, or no embargo line)."""
    text, ex = bx.PARSERS[kind](content)
    emb = bx.embargo(text)
    if emb is None or emb[0] != release_date:
        return None
    per = bx.period(text)
    if per is None:
        return None
    d, hh, mm = emb
    return Capture(period=per, texts={"release": text}, extractions=ex,
                   docs=[Doc(DOC_NAMES[kind], nr0_url(kind)), Doc("PDF version", pdf_url(kind))],
                   bullets=bx.lead_sentences(text, BULLETS[kind]), release_at=at_et(d, hh, mm))


class BlsPrints(ScheduledPrint):
    PRINTS = PRINTS
    doc_type = "bls"

    def schedule(self) -> list[ScheduledRelease]:
        url = self.cfg.urls.get("calendar", ICS_URL)
        res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type="bls:calendar", headers=BROWSER_HEADERS,
                                   conditional=False)
        rels = parse_ics(res.content, url)
        if not rels:
            raise ValueError("BLS ICS yielded no CPI/Employment Situation/PPI/JOLTS events")
        return rels

    def capture(self, rel: ScheduledRelease, meta: dict) -> Capture | None:
        url = nr0_url(rel.kind)
        res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"bls:{rel.kind}", expect="html",
                                   headers=BROWSER_HEADERS, conditional=False)
        cap = build_capture(rel.kind, res.content, rel.release_at.astimezone(ET).date())
        if cap is None:
            log.info("bls_not_yet_posted", kind=rel.kind, release=rel.release_at.isoformat())
        return cap
