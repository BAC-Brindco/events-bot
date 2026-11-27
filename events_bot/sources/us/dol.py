"""DOL weekly jobless claims: a stream item per weekly release, for the US Morning Wrap (section Data).

Sources (phase0/us_data.md):
  pdf  https://www.dol.gov/ui/data.pdf  always the latest release; ETag + LM (304 works), LM = embargo time.
       Page 1 carries "EMBARGOED UNTIL 8:30 A.M. (Eastern) Wednesday, November 25, 2026"; that date is the
       item's ext_id (claims:YYYY-MM-DD) and its time the published stamp.
  rss  https://www.dol.gov/rss/releases.xml  backstop; 10 items across all DOL agencies, date-only stamps.
       Claims items ("Unemployment Insurance Weekly Claims Report", link .../eta/etaYYYYMMDD) map to the same
       ext_id; every other DOL release is dropped (not macro).
The canonical item URL is the dated release `https://www.dol.gov/newsroom/releases/eta/etaYYYYMMDD` (it
redirects to the numbered PDF). dol.gov sits behind Akamai: the RSS and the dated URLs answer 403 to a bare
UA, so every request sends prints_base.BROWSER_HEADERS over curl_cffi (impersonate=True; httpx is 403 from
GitHub runners whatever the headers, reachability run 45953080704).

Polling is limited to Wednesday-Friday ET: Thursday is release day, Wednesday covers holiday shifts
(Thanksgiving, Christmas, New Year, July 4 on a Thursday), Friday is the catch-up.
"""
from __future__ import annotations

import re
from datetime import datetime

import structlog

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import ET as NY
from ...core.timeutil import utcnow
from ...digest import content
from ...digest.content import Body, pdf_body
from .bea import rss_items
from .prints_base import BROWSER_HEADERS, at_et

log = structlog.get_logger()

TITLE = "Unemployment Insurance Weekly Claims Report"
RELEASE_URL = "https://www.dol.gov/newsroom/releases/eta/eta{ymd}"
POLL_WEEKDAYS = (2, 3, 4)            # Wed, Thu, Fri (ET)
_EMBARGO = re.compile(r"EMBARGOED UNTIL (\d{1,2}):(\d{2}) ([AP])\.M\. \(Eastern\) \w+day, (\w+ \d{1,2}, \d{4})")
# Line breaks so each headline paragraph is its own line of the text (pdf_body joins the page-1 blocks).
_BREAK = re.compile(r" (?=UNEMPLOYMENT INSURANCE WEEKLY CLAIMS\b|SEASONALLY ADJUSTED DATA\b|In the week ending\b)")
HEADLINE_STARTS = ("In the week ending", "The advance seasonally adjusted insured unemployment rate")


def claims_text(pdf: bytes) -> str:
    """Deterministic text of a claims PDF: pdf_body plus breaks before the page-1 headings and the first
    headline paragraph (only whitespace changes)."""
    return _BREAK.sub("\n", pdf_body(pdf))


def embargo(text: str) -> datetime | None:
    m = _EMBARGO.search(text)
    if not m:
        return None
    hh = int(m.group(1)) % 12 + (12 if m.group(3) == "P" else 0)
    d = datetime.strptime(m.group(4), "%B %d, %Y").date()
    return at_et(d, hh, int(m.group(2)))


def headline_paragraphs(text: str) -> list[str]:
    """The seasonally adjusted headline paragraphs, verbatim lines: initial claims + 4-week average, then
    insured unemployment."""
    out = []
    for start in HEADLINE_STARTS:
        line = next((ln for ln in text.split("\n") if ln.startswith(start)), None)
        if line:
            out.append(line)
    return out


def parse_pdf(pdf: bytes) -> dict | None:
    text = claims_text(pdf)
    at = embargo(text)
    if at is None or "WEEKLY CLAIMS" not in text:
        return None
    week = re.search(r"In the week ending (\w+ \d{1,2}), the advance figure", text)
    return {"text": text, "release_at": at, "week_ending": week.group(1) if week else None,
            "paragraphs": headline_paragraphs(text)}


def _ext_id(d) -> str:
    return f"claims:{d.isoformat()}"


def _https(link: str) -> str:
    return re.sub(r"^http://", "https://", link.strip())


class DolClaims(SourceAdapter):
    doc_type = "dol"

    def poll(self) -> list[RawItem]:
        if utcnow().astimezone(NY).weekday() not in POLL_WEEKDAYS:
            return []
        items: list[RawItem] = []
        for name, url in self.cfg.urls.items():
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"dol:{name}",
                                       expect="pdf" if name == "pdf" else "xml", headers=BROWSER_HEADERS, impersonate=True)
            if res.not_modified:
                continue
            items += self.parse(res, name)
        seen, out = set(), []
        for it in items:                                # the PDF item wins over the RSS item of the same week
            if it.ext_id not in seen:
                seen.add(it.ext_id)
                out.append(it)
        return out

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        if url_name == "pdf":
            p = parse_pdf(res.content)
            if p is None:
                log.warning("dol_claims_pdf_unrecognised", url=res.url)
                return []
            d = p["release_at"].date()
            return [RawItem(
                source_id=self.cfg.id, ext_id=_ext_id(d), url=RELEASE_URL.format(ymd=d.strftime("%Y%m%d")),
                title=TITLE, source_published_at=p["release_at"], document_id=res.document_id,
                summary=p["paragraphs"][0] if p["paragraphs"] else None,
                meta={"route_hint": "us_morning_wrap", "scope_reason": "US weekly jobless claims",
                      "kind": "claims", "week_ending": p["week_ending"], "paragraphs": p["paragraphs"],
                      "pdf_url": res.url})]
        out = []
        for it in rss_items(res.content):
            link = _https(it["link"] or it["guid"])
            m = re.search(r"/eta/eta(\d{8})$", link)
            title = it["title"]
            if m and title.startswith(TITLE):
                d = datetime.strptime(m.group(1), "%Y%m%d").date()
                out.append(RawItem(
                    source_id=self.cfg.id, ext_id=_ext_id(d), url=link, title=title,
                    source_published_at=at_et(d, 8, 30), published_raw=it["published_raw"],
                    meta={"route_hint": "us_morning_wrap", "scope_reason": "US weekly jobless claims",
                          "kind": "claims", "from": "rss"}))
            else:
                slug = link.rstrip("/").rsplit("/", 1)[-1]
                out.append(RawItem(
                    source_id=self.cfg.id, ext_id=f"rss:{slug}", url=link, title=title,
                    source_published_at=it["published"], published_raw=it["published_raw"],
                    meta={"route_hint": "drop", "scope_reason": "DOL release other than weekly claims",
                          "date_only": True}))
        return out

    def digest_body(self, it: RawItem) -> Body:
        """The release PDF (dated URL, else data.pdf when it is still the same week), headline paragraphs."""
        f = self.ctx.fetcher
        d = datetime.strptime(it.ext_id.split(":", 1)[1], "%Y-%m-%d").date()
        res, notes = None, []
        try:
            res = f.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id, doc_type="digest:dol_pdf",
                                   expect="pdf", conditional=False, headers=BROWSER_HEADERS, impersonate=True)
        except Exception as e:  # noqa: BLE001  fall back to the always-latest file
            log.warning("dol_dated_pdf_failed", url=it.url, error=str(e)[:200])
        if res is None or (parse_pdf(res.content) or {}).get("release_at", None) is None:
            res = f.get(self.cfg.urls["pdf"], source_id=self.cfg.id, doc_type="digest:dol_pdf", expect="pdf",
                        conditional=False, headers=BROWSER_HEADERS, impersonate=True)
            notes.append("Quoted from ui/data.pdf (the dated release URL did not answer).")
        p = parse_pdf(res.content)
        if p is None or p["release_at"].date() != d:
            raise ValueError(f"claims PDF is not the release of {d.isoformat()}")
        text = p["text"]
        paras = [x for x in p["paragraphs"] if x in text] or content._paragraphs(text)[:2]
        return Body(text, paras, it.url, res.final_url or res.url, res.document_id, notes=notes,
                    posted=p["release_at"])
