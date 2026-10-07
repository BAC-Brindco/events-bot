"""RBI Monetary Policy Committee: calendar, in-window capture, Stage 1 and Stage 2.

Calendar: the "Meeting Schedule of the Monetary Policy Committee" block on Annualpolicy.aspx
(FY schedule posted each March). Decision at 10:00 IST on the final day.

Discovery (Phase 0): the resolution is a press release page BS_PressReleaseDisplay.aspx?prid=N
whose HTML carries the full text (the PDF lags 25-80 min). Two channels, whichever is first:
- pressreleases_rss.xml (10 items; first seen <= 2 min after pubDate in Phase 0);
- probing the next unpublished prids (sequential). An unpublished prid returns HTTP 200 with an
  empty shell, so a page counts only when `.tablebg` holds a resolution title.
The 2026-10-07 capture (harness/mpc_capture.py) measures which channel wins; both stay on.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

import feedparser
import structlog
from selectolax.parser import HTMLParser

from ...core.fetch import FetchError
from ...core.models import ScheduledEvent
from ...core.registry import SourceAdapter
from ...core.timeutil import IST, utcnow
from ...extract import mpc as mx
from ...extract.validator import validate_all
from ...stage1 import mpc as sm
from ...stage1.render import Doc

log = structlog.get_logger()
PR_PAGE = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={}"
MONTHS = {m: i for i, m in enumerate("January February March April May June July August September October "
                                     "November December".split(), 1)}
WINDOW_BEFORE, WINDOW_AFTER = timedelta(minutes=10), timedelta(minutes=150)
STAGE2_AFTER = timedelta(minutes=60)
PROBE_AHEAD = 6
TRANSCRIPT_PENDING = ("RBI posts an edited transcript of the post-policy press conference about two days later "
                      "(Annualpolicy.aspx). A follow-up will be sent when it is available.")
_RES_TITLE = re.compile(r"Resolution of the Monetary Policy Committee", re.I)
_GOV_TITLE = re.compile(r"Governor.s Statement", re.I)
_SDRP_TITLE = re.compile(r"Statement on Developmental and Regulatory Policies", re.I)


def release_at(d: date) -> datetime:
    return datetime.combine(d, time(10, 0), tzinfo=IST)


def parse_schedule(content: bytes) -> list[date]:
    """Final days from every 'Meeting Schedule of the Monetary Policy Committee for YYYY-YYYY' block."""
    html = content.decode("utf-8", "replace")
    out: list[date] = []
    for m in re.finditer(r"Meeting Schedule of the Monetary Policy Committee for \d{4}-\d{4}", html):
        chunk = re.sub(r"<[^>]+>", " ", html[m.end(): m.end() + 5000])
        for d in re.finditer(r"([A-Z][a-z]+) ((?:\d{1,2}, )*\d{1,2})(?:,)? and (\d{1,2}), (\d{4})", chunk):
            if d.group(1) in MONTHS:
                out.append(date(int(d.group(4)), MONTHS[d.group(1)], int(d.group(3))))
        # cross-month meetings: "September 29, 30 and October 1, 2025"
        for d in re.finditer(r"([A-Z][a-z]+) \d{1,2}(?:, \d{1,2})* and ([A-Z][a-z]+) (\d{1,2}), (\d{4})", chunk):
            if d.group(2) in MONTHS:
                out.append(date(int(d.group(4)), MONTHS[d.group(2)], int(d.group(3))))
    return sorted(set(out))


def parse_index(content: bytes) -> dict[date, dict[str, int]]:
    """Published meetings on Annualpolicy.aspx: final day -> {'resolution': prid, 'governor': prid}."""
    tree = HTMLParser(content.decode("utf-8", "replace"))
    out: dict[date, dict[str, int]] = {}
    for td in tree.css("td"):
        t = td.text(separator=" ", strip=True)
        m = re.match(r"Resolution of the Monetary Policy Committee \(MPC\) (.+?, \d{4})", t)
        if m and (a := td.css_first("a")) and (p := re.search(r"prid=(\d+)", a.attributes.get("href") or "")):
            d = _final_day(m.group(1))
            if d:
                out.setdefault(d, {})["resolution"] = int(p.group(1))
    for a in tree.css("a"):
        t, href = a.text(strip=True), a.attributes.get("href") or ""
        m = re.match(r"Governor.s Statement: (\w+ \d{1,2}, \d{4})", t)
        p = re.search(r"prid=(\d+)", href)
        if m and p:
            try:
                d = datetime.strptime(m.group(1), "%B %d, %Y").date()
            except ValueError:
                continue
            out.setdefault(d, {})["governor"] = int(p.group(1))
    return out


def _final_day(s: str) -> date | None:
    """'August 3 to 5, 2026' / 'September 29 to October 1, 2025' -> last day."""
    m = re.search(r"(?:([A-Z][a-z]+) )?(\d{1,2}), (\d{4})$", s)
    first_month = re.match(r"([A-Z][a-z]+)", s)
    if not m:
        return None
    month = m.group(1) or (first_month.group(1) if first_month else None)
    return date(int(m.group(3)), MONTHS[month], int(m.group(2))) if month in MONTHS else None


def assemble(d: date, content: bytes):
    text, ex = mx.parse_resolution(content, d)
    return validate_all(ex, {"resolution": text}, expected_period=d.isoformat()), text


def build_messages(d: date, content: bytes, prior_d: date | None, prior_content: bytes | None, urls: dict[str, str],
                   *, first_seen: datetime, mode: str, stage2: bool = True, sdrp: bytes | None = None,
                   governor_pdf: bytes | None = None):
    """Pure: Stage 1 + the detailed Stage 2 review from archived bytes. Used live, by fire and by replay."""
    from ...stage2 import mpc as review

    ex, text = assemble(d, content)
    prior_ex, prior_text = assemble(prior_d, prior_content) if prior_d and prior_content else (None, None)
    mx.consistency(ex, prior_ex)
    ref = f"rbi_mpc:{d.isoformat()}"
    docs = [Doc("MPC resolution", urls["resolution"])]
    if urls.get("governor_pdf") or urls.get("governor"):
        docs.append(Doc("Governor's statement", urls.get("governor_pdf") or urls["governor"]))
    if urls.get("sdrp"):
        docs.append(Doc("Developmental and regulatory policies", urls["sdrp"]))
    m1 = sm.build_stage1(ref, ex, docs, release_at=release_at(d), first_seen=first_seen, mode=mode)
    m2 = None
    if stage2:
        m2 = review.build(ref, ex, text, prior_ex, prior_text, prior_d.strftime("%d %b %Y") if prior_d else None,
                          docs, transcript_status=TRANSCRIPT_PENDING, mode=mode,
                          sdrp_text=mx.resolution_text(sdrp) if sdrp else None, governor_pdf=governor_pdf)
    return m1, m2, ex, text


def governor_pdf_url(page: bytes) -> str | None:
    m = re.search(rb'https://rbidocs\.rbi\.org\.in/rdocs/PressRelease/PDFs/[A-Z0-9]+\.PDF', page, re.I)
    return m.group(0).decode() if m else None


class RbiMpc(SourceAdapter):
    doc_type = "rbi_mpc"

    def _index(self) -> bytes:
        return self.ctx.fetcher.get(self.cfg.urls["index"], source_id=self.cfg.id, doc_type="rbi_mpc:index",
                                    expect="html", conditional=False).content

    def calendar(self) -> list[ScheduledEvent]:
        content = self._index()
        days = parse_schedule(content)
        if len(days) < 4:
            raise FetchError(self.cfg.urls["index"], "shape", f"only {len(days)} MPC dates parsed")
        published = parse_index(content)
        known = sorted(set(days) | set(published))
        today = utcnow().astimezone(IST).date()
        evs = []
        for d in days:
            if d < today - timedelta(days=7):
                continue
            prior = max((k for k in known if k < d), default=None)
            at = release_at(d)
            evs.append(ScheduledEvent(
                ref=f"rbi_mpc:{d.isoformat()}", source_id=self.cfg.id, event_type="rbi_mpc_decision",
                title=f"RBI MPC decision ({d:%d %b %Y})", scheduled_at=at, window_start=at - WINDOW_BEFORE,
                window_end=at + WINDOW_AFTER, calendar_url=self.cfg.urls["index"],
                meta={"prior": prior.isoformat() if prior else None,
                      "prior_resolution_prid": published.get(prior, {}).get("resolution") if prior else None}))
        return evs

    # ---- discovery -----------------------------------------------------------
    def _page(self, prid: int) -> bytes | None:
        try:
            b = self.ctx.fetcher.get(PR_PAGE.format(prid), source_id=self.cfg.id, doc_type="rbi_mpc:page",
                                     expect="html", conditional=False).content
        except FetchError as e:
            if e.kind == "http" and e.status == 404:
                return None
            raise
        return b if HTMLParser(b.decode("utf-8", "replace")).css_first(".tablebg") is not None else None

    def _discover(self, d: date, meta: dict) -> dict[str, int]:
        found = {k: meta[k] for k in ("resolution_prid", "governor_prid", "sdrp_prid") if meta.get(k)}
        if all(k in found for k in ("resolution_prid", "governor_prid", "sdrp_prid")):
            return found
        fp = feedparser.parse(self.ctx.fetcher.get(self.cfg.urls["press_releases"], source_id=self.cfg.id,
                                                   doc_type="rbi_mpc:rss", expect="xml", conditional=False).content)
        max_prid = 0
        for e in fp.entries:
            m = re.search(r"prid=(\d+)", e.get("link", ""), re.I)
            if not m:
                continue
            prid = int(m.group(1))
            max_prid = max(max_prid, prid)
            title = e.get("title", "")
            if _RES_TITLE.search(title):
                found["resolution_prid"] = prid
            elif _GOV_TITLE.search(title):
                found["governor_prid"] = prid
            elif _SDRP_TITLE.search(title):
                found["sdrp_prid"] = prid
        if "resolution_prid" not in found and max_prid:
            for prid in range(max_prid + 1, max_prid + 1 + PROBE_AHEAD):
                b = self._page(prid)
                if b is None:
                    continue
                t = mx.resolution_text(b)
                if _RES_TITLE.search(t[:600]) and mx.page_date(d) in t[:300]:
                    found["resolution_prid"] = prid
                elif _GOV_TITLE.search(t[:300]):
                    found["governor_prid"] = prid
                elif _SDRP_TITLE.search(t[:300]):
                    found["sdrp_prid"] = prid
        return found

    def poll_event(self, ev: dict) -> None:
        meta, now = ev["meta"] or {}, utcnow()
        if meta.get("stage2_at"):
            return
        d = ev["scheduled_at"].astimezone(IST).date()
        found = self._discover(d, meta)
        if "resolution_prid" not in found:
            return
        if found != {k: meta.get(k) for k in found}:
            self.ctx.db.mark_event(ev["ref"], "in_window", **found)
        content = self._page(found["resolution_prid"])
        if content is None or mx.page_date(d) not in mx.resolution_text(content)[:300]:
            return
        if not meta.get("first_seen"):
            self.ctx.db.mark_event(ev["ref"], None, first_seen=now)
        first_seen = datetime.fromisoformat(meta["first_seen"]) if meta.get("first_seen") else now
        do1 = not meta.get("stage1_at")
        do2 = bool(meta.get("stage1_at")) and now - ev["scheduled_at"] >= STAGE2_AFTER
        if not (do1 or do2):
            return
        prior_d = date.fromisoformat(meta["prior"]) if meta.get("prior") else None
        prior_content = self._page(meta["prior_resolution_prid"]) if do2 and meta.get("prior_resolution_prid") else None
        urls = {"resolution": PR_PAGE.format(found["resolution_prid"])}
        sdrp = governor_pdf = None
        if found.get("governor_prid"):
            urls["governor"] = PR_PAGE.format(found["governor_prid"])
        if found.get("sdrp_prid"):
            urls["sdrp"] = PR_PAGE.format(found["sdrp_prid"])
        if do2:
            # Stage 2 is the full review: the measures statement (HTML) and the Governor's Statement
            # (PDF only). Either may be missing or late; the review says so instead of waiting.
            if found.get("sdrp_prid"):
                sdrp = self._page(found["sdrp_prid"])
            if found.get("governor_prid"):
                gpage = self._page(found["governor_prid"])
                pdf_url = governor_pdf_url(gpage) if gpage else None
                if pdf_url:
                    urls["governor_pdf"] = pdf_url
                    try:
                        governor_pdf = self.ctx.fetcher.get_with_retry(
                            pdf_url, tries=3, base_delay=3, source_id=self.cfg.id, doc_type="rbi_mpc:governor_pdf",
                            expect="pdf", conditional=False, headers={"Referer": "https://www.rbi.org.in/"}).content
                    except FetchError as e:
                        log.warning("governor_pdf_failed", error=str(e)[:200])
        app = self.ctx.app
        m1, m2, ex, text = build_messages(d, content, prior_d, prior_content, urls, first_seen=first_seen,
                                          mode=app.mode, stage2=do2, sdrp=sdrp, governor_pdf=governor_pdf)
        row = self.ctx.db.latest_document(urls["resolution"])
        if row is not None:
            self.ctx.db.set_document_text(row["id"], text)
            self.ctx.db.replace_extractions(row["id"], ex)
        if do1:
            app.dispatcher.dispatch(m1, app.settings.recipients)
            self.ctx.db.mark_event(ev["ref"], "captured", stage1_at=now)
        elif m2 is not None:
            app.dispatcher.dispatch(m2, app.settings.recipients)
            self.ctx.db.mark_event(ev["ref"], "captured", stage2_at=now)
