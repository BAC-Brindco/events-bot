"""Federal Reserve FOMC: calendar, in-window capture, Stage 1 and Stage 2.

Calendar: fomccalendars.htm (no ICS exists; FEED_MAP). Final meeting day, 14:00 ET.
Documents (FEED_MAP, verified 2026-10-01): statement monetary{d}a.htm, implementation note
monetary{d}a1.htm, SEP fomcprojtabl{d}.htm on SEP meetings. All post within ~35 s of 14:00 ET.
Link anomalies (fomcpressconf, fomcprojtable) are why links are read from the calendar
page when it has them and constructed only as a fallback.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from urllib.parse import urljoin

import structlog
from selectolax.parser import HTMLParser

from ...core.fetch import FetchError
from ...core.models import ScheduledEvent
from ...core.registry import SourceAdapter
from ...core.timeutil import ET, utcnow
from ...extract import fomc as fx
from ...extract.validator import validate_all
from ...stage1 import fomc as s1
from ...stage2 import fomc as s2

log = structlog.get_logger()
BASE = "https://www.federalreserve.gov"
MONTHS = {m: i for i, m in enumerate(fx.MONTHS, 1)}

WINDOW_BEFORE, WINDOW_AFTER = timedelta(minutes=10), timedelta(minutes=90)
STAGE1_WAIT = timedelta(minutes=5)       # wait this long for the note/SEP before sending without them
STAGE2_AFTER = timedelta(minutes=60)     # presser starts at 14:30 ET; send Stage 2 after it starts
TRANSCRIPT_PENDING = ("Official press conference transcript pending: the Fed usually posts it 8–13 days after "
                      "the meeting. A follow-up will be sent when it is available.")


def doc_urls(d: date, links: dict | None = None) -> dict[str, str]:
    k = d.strftime("%Y%m%d")
    u = {"statement": f"{BASE}/newsevents/pressreleases/monetary{k}a.htm",
         "impl": f"{BASE}/newsevents/pressreleases/monetary{k}a1.htm",
         "sep": f"{BASE}/monetarypolicy/fomcprojtabl{k}.htm"}
    for name, href in (links or {}).items():
        if name in u:
            u[name] = href
    return u


def parse_calendar(content: bytes) -> list[dict]:
    """Regular meetings from fomccalendars.htm: [{date, sep, links, label}] in date order."""
    tree = HTMLParser(content.decode("utf-8-sig", "replace"))
    out: list[dict] = []
    for panel in tree.css("div.panel.panel-default"):
        head = panel.css_first(".panel-heading")
        m = re.search(r"(\d{4}) FOMC Meetings", head.text() if head else "")
        if not m:
            continue
        year = int(m.group(1))
        for row in panel.css("div.fomc-meeting"):
            mon_el, day_el = row.css_first(".fomc-meeting__month"), row.css_first(".fomc-meeting__date")
            if mon_el is None or day_el is None:
                continue
            mon_txt, day_txt = mon_el.text(strip=True), day_el.text(strip=True)
            if "(" in day_txt or "unscheduled" in day_txt.lower() or "notation" in day_txt.lower():
                continue                                  # notation votes / unscheduled calls
            days = re.findall(r"\d+", day_txt)
            mons = [x for x in re.split(r"[/\-]", mon_txt) if x]
            if not days or not mons:
                continue
            month = MONTHS.get(mons[-1].strip()) or next(
                (i for name, i in MONTHS.items() if name.startswith(mons[-1].strip()[:3])), None)
            if month is None:
                continue
            final = date(year, month, int(days[-1]))
            links = {}
            for a in row.css("a"):
                href, txt = a.attributes.get("href") or "", a.text(strip=True)
                if re.search(r"monetary\d{8}a\.htm$", href):
                    links["statement"] = urljoin(BASE, href)
                elif re.search(r"monetary\d{8}a1\.htm$", href):
                    links["impl"] = urljoin(BASE, href)
                elif re.search(r"fomcprojtabl\w*\d{8}\.htm$", href):
                    links["sep"] = urljoin(BASE, href)
                elif "Press Conference" in txt:
                    links["presser"] = urljoin(BASE, href)
            out.append({"date": final, "sep": "*" in day_txt, "links": links,
                        "label": f"{mon_txt} {day_txt.rstrip('*')}, {year}"})
    out.sort(key=lambda r: r["date"])
    return out


def release_at(d: date) -> datetime:
    return datetime.combine(d, time(14, 0), tzinfo=ET)


def assemble(d: date, docs: dict[str, bytes]) -> tuple[list, dict[str, str]]:
    """Pure: archived bytes of one meeting's documents -> validated extractions + texts."""
    texts, ex = {}, []
    if "statement" in docs:
        t, e = fx.parse_statement(docs["statement"], d)
        texts["statement"], ex = t, ex + e
    if "impl" in docs:
        t, e = fx.parse_impl_note(docs["impl"], d)
        texts["impl"], ex = t, ex + e
    if "sep" in docs:
        t, e = fx.parse_sep(docs["sep"], d)
        texts["sep"], ex = t, ex + e
    return validate_all(ex, texts, expected_period=d.isoformat()), texts


def build_messages(d: date, docs: dict[str, bytes], prior_d: date | None, prior_docs: dict[str, bytes] | None,
                   urls: dict[str, str], *, first_seen: datetime, mode: str, stage2: bool = True):
    """Pure: both messages for a meeting from archived bytes. Used live and by replay."""
    ex, texts = assemble(d, docs)
    prior_ex, prior_texts = assemble(prior_d, prior_docs) if prior_d and prior_docs else (None, None)
    fx.consistency(ex, prior_ex)
    ref = f"fomc:{d.isoformat()}"
    names = {"statement": "Statement", "impl": "Implementation note", "sep": "Projections (SEP)"}
    links = [s1.Doc(names[k], urls[k]) for k in ("statement", "impl", "sep") if k in docs]
    m1 = s1.build(ref, ex, links, release_at=release_at(d), first_seen=first_seen, mode=mode)
    m2 = None
    if stage2:
        m2 = s2.build(ref, ex, texts, prior_ex, prior_texts,
                      prior_d.strftime("%d %b %Y") if prior_d else None, links,
                      transcript_status=TRANSCRIPT_PENDING, mode=mode)
    return m1, m2, ex, texts


class Fomc(SourceAdapter):
    doc_type = "fomc"

    def _calendar_rows(self) -> list[dict]:
        res = self.ctx.fetcher.get(self.cfg.urls["calendar"], source_id=self.cfg.id, doc_type="fomc:calendar",
                                   expect="html", conditional=False)
        rows = parse_calendar(res.content)
        if len(rows) < 8:
            raise FetchError(self.cfg.urls["calendar"], "shape", f"only {len(rows)} meetings parsed")
        return rows

    def calendar(self) -> list[ScheduledEvent]:
        rows = self._calendar_rows()
        today = utcnow().date()
        evs = []
        for i, r in enumerate(rows):
            if r["date"] < today - timedelta(days=7):
                continue
            at = release_at(r["date"])
            prior = rows[i - 1]["date"] if i else None
            evs.append(ScheduledEvent(
                ref=f"fomc:{r['date'].isoformat()}", source_id=self.cfg.id, event_type="fomc_decision",
                title=f"FOMC decision ({r['label']}){' with SEP' if r['sep'] else ''}",
                scheduled_at=at, window_start=at - WINDOW_BEFORE, window_end=at + WINDOW_AFTER,
                calendar_url=self.cfg.urls["calendar"],
                meta={"sep": r["sep"], "links": r["links"], "prior": prior.isoformat() if prior else None}))
        return evs

    # ---- in-window -----------------------------------------------------------
    def _get(self, url: str, kind: str) -> bytes | None:
        try:
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"fomc:{kind}", expect="html",
                                       conditional=False)
        except FetchError as e:
            if e.kind == "http" and e.status == 404:
                return None                           # not posted yet
            raise
        return res.content

    def _fetch_meeting(self, d: date, urls: dict[str, str], want_sep: bool) -> dict[str, bytes]:
        docs: dict[str, bytes] = {}
        st = self._get(urls["statement"], "statement")
        if st is None:
            return docs
        if fx.long_date(d) not in fx.statement_text(st):
            log.warning("fomc_stale_statement", date=d.isoformat())   # a cached/old page
            return docs
        docs["statement"] = st
        for k in ("impl",) + (("sep",) if want_sep else ()):
            b = self._get(urls[k], k)
            if b is not None:
                docs[k] = b
        return docs

    def poll_event(self, ev: dict) -> None:
        meta, now = ev["meta"] or {}, utcnow()
        d = ev["scheduled_at"].astimezone(ET).date()
        if meta.get("stage2_at"):
            return
        urls = doc_urls(d, meta.get("links"))
        docs = self._fetch_meeting(d, urls, bool(meta.get("sep")))
        if "statement" not in docs:
            return
        complete = "impl" in docs and ("sep" in docs or not meta.get("sep"))
        first_seen = datetime.fromisoformat(meta["first_seen"]) if meta.get("first_seen") else now
        if not meta.get("first_seen"):
            self.ctx.db.mark_event(ev["ref"], "in_window", first_seen=now)
        prior_d = date.fromisoformat(meta["prior"]) if meta.get("prior") else None
        do_stage1 = not meta.get("stage1_at") and (complete or now - ev["scheduled_at"] > STAGE1_WAIT)
        do_stage2 = meta.get("stage1_at") and now - ev["scheduled_at"] >= STAGE2_AFTER
        if not (do_stage1 or do_stage2):
            return
        prior_docs = None
        if do_stage2 and prior_d:
            pu = doc_urls(prior_d)
            prior_docs = {k: b for k in ("statement", "impl") if (b := self._get(pu[k], f"prior_{k}"))}
        app = self.ctx.app
        m1, m2, ex, texts = build_messages(d, docs, prior_d, prior_docs, urls, first_seen=first_seen,
                                           mode=app.mode, stage2=bool(do_stage2))
        self._store(urls, docs, ex, texts)
        if do_stage1:
            app.dispatcher.dispatch(m1, app.settings.recipients)
            self.ctx.db.mark_event(ev["ref"], "captured" if complete else "in_window", stage1_at=now,
                                   stage1_complete=complete)
        elif do_stage2 and m2 is not None:
            app.dispatcher.dispatch(m2, app.settings.recipients)
            self.ctx.db.mark_event(ev["ref"], "captured", stage2_at=now)

    def _store(self, urls: dict[str, str], docs: dict[str, bytes], ex: list, texts: dict[str, str]) -> None:
        for k in docs:
            row = self.ctx.db.latest_document(urls[k])
            if row is None:
                continue
            self.ctx.db.set_document_text(row["id"], texts.get(k, ""))
            self.ctx.db.replace_extractions(row["id"], [e for e in ex if e.doc == k])
