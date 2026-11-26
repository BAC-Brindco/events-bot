"""Federal Reserve documents other than the FOMC decision itself (that is `us.fed:Fomc`, source `fomc`).

fed_press   https://www.federalreserve.gov/feeds/press_monetary.xml (ETag+LM, cheap 304s). 15 items back
            ~7 months. Carries the FOMC statement, the SEP press release, FOMC minutes, discount-rate
            minutes and the odd monetary announcement (Phase 0 us_policy.md).
              FOMC minutes                      -> realtime (desk policy); Stage 1 quotes the minutes'
                                                   policy-discussion paragraphs verbatim
              statement / SEP / implementation  -> drop (the `fomc` source already alerted them)
              discount-rate minutes, the rest   -> us_morning_wrap
            pubDate is the *scheduled* time (exactly 14:00 ET), not the posting time (FLAGS 5): it is kept
            as the source time; first-seen is the pipeline's own stamp.
fed_beige   The Beige Book is in no Fed RSS. Release dates come from the undocumented
            https://www.federalreserve.gov/json/calendar.json (UTF-8 BOM; events typed "Beige", time
            "2:00 p.m." ET); the PDF https://www.federalreserve.gov/monetarypolicy/files/BeigeBook_{YYYYMMDD}.pdf
            posts within ~15 s of 14:00 ET. Each poll: calendar (conditional GET; on 304 the archived copy is
            re-read), then for a release time in the last 2 days the PDF is probed; it becomes an item once it
            answers a real PDF. Older calendar dates are emitted unfetched so the first poll's silent
            baseline swallows history and the next release is new.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from html import escape as _e
from urllib.parse import urljoin

import feedparser
import structlog
from selectolax.parser import HTMLParser

from ...core.fetch import FetchError
from ...core.models import FetchResult, Message, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import ET, fmt_ist, parse_rfc822, utcnow
from ...digest import content

log = structlog.get_logger()
BASE = "https://www.federalreserve.gov"

# ---- shared RSS helper ---------------------------------------------------------------------------


def rss_entries(raw: bytes) -> list[dict]:
    """[{title, link, guid, published_raw, summary, category}] verbatim from an RSS 2.0 feed."""
    d = feedparser.parse(raw)
    out = []
    for e in d.entries:
        out.append({"title": re.sub(r"\s+", " ", (e.get("title") or "")).strip(),
                    "link": (e.get("link") or "").strip(), "guid": (e.get("id") or "").strip(),
                    "published_raw": (e.get("published") or "").strip() or None,
                    "summary": e.get("summary") or "", "category": (e.get("category") or "").strip()})
    return out


def sane_date(dt: datetime | None) -> datetime | None:
    """The Fed's testimony feed carries an item dated 1899 (Phase 0): treat pre-1990 stamps as absent."""
    return dt if dt is not None and dt.year >= 1990 else None


# ---- fed_press --------------------------------------------------------------------------------------

_MINUTES = re.compile(r"^Minutes of the Federal Open Market Committee\b", re.I)
_FOMC_DECISION_DOCS = re.compile(r"issues FOMC statement|economic projections|implementation note|"
                                 r"Summary of Economic Projections", re.I)
_DISCOUNT = re.compile(r"discount rate meeting", re.I)


def classify_press(title: str) -> tuple[str, str, str]:
    """(route_hint, scope_reason, kind) for a press_monetary.xml item."""
    if _MINUTES.search(title):
        return "realtime", "FOMC minutes (desk policy: real time)", "FOMC minutes"
    if _FOMC_DECISION_DOCS.search(title):
        return "drop", "FOMC decision document: already alerted by the fomc source", "FOMC decision"
    if _DISCOUNT.search(title):
        return "us_morning_wrap", "Board discount-rate minutes", "Discount-rate minutes"
    return "us_morning_wrap", "Fed monetary-policy press release", "Press release"


_MEETING = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)"
                      r"\s+(\d{1,2})\s*[-–]\s*(?:(January|February|March|April|May|June|July|August|September|"
                      r"October|November|December)\s+)?(\d{1,2}),\s*(\d{4})")
_MONTHS = {m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August",
                                       "September", "October", "November", "December"), 1)}


def meeting_date_from_title(title: str) -> date | None:
    """'Minutes of the Federal Open Market Committee, October 27-28, 2026' -> 2026-10-28 (final day)."""
    m = _MEETING.search(title)
    if not m:
        return None
    mon = _MONTHS[m.group(3) or m.group(1)]
    return date(int(m.group(5)), mon, int(m.group(4)))


def minutes_urls(press_html: bytes | None, title: str) -> dict[str, str]:
    """Minutes HTML / PDF URLs: read from the press-release page, else built from the meeting date."""
    out: dict[str, str] = {}
    if press_html:
        tree = HTMLParser(press_html.decode("utf-8-sig", "replace"))
        for a in tree.css("a[href]"):
            h = a.attributes.get("href") or ""
            if re.search(r"fomcminutes\d{8}\.htm$", h):
                out.setdefault("html", urljoin(BASE, h))
            elif re.search(r"fomcminutes\d{8}\.pdf$", h):
                out.setdefault("pdf", urljoin(BASE, h))
    d = meeting_date_from_title(title)
    if d is not None:
        k = d.strftime("%Y%m%d")
        out.setdefault("html", f"{BASE}/monetarypolicy/fomcminutes{k}.htm")
        out.setdefault("pdf", f"{BASE}/monetarypolicy/files/fomcminutes{k}.pdf")
    return out


# The policy section of the minutes: "Participants' Views on Current Conditions ... and Monetary Policy
# Considerations" / "Policymakers' Discussion ..." then "Committee Policy Actions". These openings have been
# stable for years; each picks one whole paragraph (a line of the document text).
POLICY_LEADS = (re.compile(r"^In their consideration of monetary policy", re.I),
                re.compile(r"^With regard to the outlook for monetary policy", re.I),
                re.compile(r"^In discussing risk[- ]management considerations", re.I))
_CPA_HEAD = re.compile(r"Committee Policy Actions?\b")


def minutes_policy_paragraphs(text: str, n: int = 4) -> list[str]:
    """Verbatim paragraphs (whole lines of `text`) from the minutes' policy discussion, in document order."""
    lines = text.split("\n")
    picked: list[int] = []
    for pat in POLICY_LEADS:
        i = next((i for i, ln in enumerate(lines) if pat.search(ln) and len(ln) >= content.MIN_PARA), None)
        if i is not None:
            picked.append(i)
    head = next((i for i, ln in enumerate(lines) if _CPA_HEAD.match(ln)), None)
    if head is not None:
        j = next((j for j in range(head, min(head + 4, len(lines)))
                  if re.search(r"members agreed", lines[j]) and len(lines[j]) >= content.MIN_PARA), None)
        if j is not None:
            picked.append(j)
    return [lines[i] for i in sorted(set(picked))][:n]


def strip_cpa_head(p: str) -> str:
    """Display only: 'Committee Policy Actions In support of ...' -> 'In support of ...' (still verbatim)."""
    return _CPA_HEAD.sub("", p, count=1).lstrip() if _CPA_HEAD.match(p) else p


def minutes_text(res_html: bytes | None, res_pdf: bytes | None) -> str:
    if res_html is not None:
        return content.html_body(res_html, "#article")
    return content.pdf_body(res_pdf) if res_pdf is not None else ""


class FedPress(SourceAdapter):
    doc_type = "fed_press"

    def poll(self) -> list[RawItem]:
        """A decision document is dropped only when the `fomc` source has a scheduled meeting within a day of it.
        An intermeeting statement (an unscheduled cut, an emergency facility) is not on the FOMC calendar, so
        `fomc` never sees it: it goes out in real time from here."""
        items = super().poll()
        for it in items:
            m = it.meta or {}
            if m.get("kind") != "FOMC decision" or it.source_published_at is None or self.ctx.db is None:
                continue
            hit = self.ctx.db.one("select 1 as x from events where source_id = 'fomc' and scheduled_at "
                                  "between %s and %s limit 1",
                                  (it.source_published_at - timedelta(days=1), it.source_published_at + timedelta(days=1)))
            if hit is None:
                it.meta = {**m, "route_hint": "realtime",
                           "scope_reason": "FOMC action outside the scheduled meeting calendar (intermeeting)"}
        return items

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in rss_entries(res.content):
            if not r["link"]:
                continue
            hint, reason, kind = classify_press(r["title"])
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=r["link"].rsplit("/", 1)[-1], url=r["link"], title=r["title"],
                source_published_at=sane_date(parse_rfc822(r["published_raw"])), published_raw=r["published_raw"],
                document_id=res.document_id,
                meta={"route_hint": hint, "scope_reason": reason, "kind": kind,
                      "time_note": "RSS pubDate is the scheduled release time"}))
        return items

    # -- fetching the minutes ------------------------------------------------------------------------
    def _get(self, url: str, expect: str, doc_type: str, parent_id: int | None = None):
        return self.ctx.fetcher.get_with_retry(url, tries=3, base_delay=4, source_id=self.cfg.id,
                                               doc_type=f"fed_press:{doc_type}", expect=expect, conditional=False,
                                               parent_id=parent_id)

    def _minutes(self, it: RawItem) -> tuple[str, dict[str, str], int | None]:
        """(text, urls, document_id). HTML first (posts +13..+81 s), the PDF (+11 s) if the HTML is not up."""
        press = None
        try:
            press = self._get(it.url, "html", "press_page", it.document_id)
        except FetchError as e:
            log.warning("fed_minutes_press_page_failed", url=it.url, error=str(e)[:200])
        urls = minutes_urls(press.content if press else None, it.title)
        if not urls:
            raise ValueError("no minutes URL on the press page or in the title")
        parent = press.document_id if press else it.document_id
        try:
            r = self._get(urls["html"], "html", "minutes_html", parent)
            return minutes_text(r.content, None), urls, r.document_id
        except (FetchError, ValueError) as e:
            log.warning("fed_minutes_html_failed", url=urls.get("html"), error=str(e)[:200])
        r = self._get(urls["pdf"], "pdf", "minutes_pdf", parent)
        return minutes_text(None, r.content), urls, r.document_id

    def render_stage1(self, it: RawItem, *, first_seen: datetime, mode: str) -> Message | None:
        """FOMC minutes: headline + the policy-discussion paragraphs verbatim + links. Anything else, or any
        failure, returns None and the pipeline sends the generic Stage 1 item alert (never silent)."""
        if (it.meta or {}).get("kind") != "FOMC minutes":
            return None
        try:
            text, urls, doc_id = self._minutes(it)
            paras = minutes_policy_paragraphs(text)
            if doc_id is not None:
                self.ctx.db.set_document_text(doc_id, text)
            if not paras:
                return None
            return build_minutes_alert(it, paras, urls, first_seen=first_seen, mode=mode)
        except Exception as e:  # noqa: BLE001  fall back to the generic alert
            log.warning("fed_minutes_render_failed", ref=it.ref, error=str(e)[:200])
            return None

    def digest_body(self, it: RawItem) -> content.Body:
        if (it.meta or {}).get("kind") == "FOMC minutes":
            text, urls, doc_id = self._minutes(it)
            paras = minutes_policy_paragraphs(text) or content._paragraphs(text)
            return content.Body(text, paras, urls.get("html", it.url), urls.get("pdf"), doc_id)
        r = self._get(it.url, "html", "press_page", it.document_id)
        text = content.html_body(r.content, "#article")
        pdf = next((urljoin(BASE, a.attributes.get("href") or "")
                    for a in HTMLParser(r.content.decode("utf-8-sig", "replace")).css("#article a[href]")
                    if (a.attributes.get("href") or "").lower().endswith(".pdf")), None)
        return content.Body(text, content._paragraphs(text), it.url, pdf, r.document_id)


def build_minutes_alert(it: RawItem, paras: list[str], urls: dict[str, str], *, first_seen: datetime,
                        mode: str) -> Message:
    from ...deliver import design as dz
    from ...deliver import house as hs
    from ...stage1.render import Doc, _provenance

    shown = [strip_cpa_head(p) for p in paras]
    lead = f"The Federal Reserve released the {it.title[0].lower() + it.title[1:]}."
    subject = f"[FED] FOMC minutes | {it.title}"
    body = dz.masthead(kicker=hs.KICKER, title="FOMC Minutes",
                       dateline=f"{_e(it.title)} &middot; Federal Reserve",
                       subline=f"Scheduled {_e(fmt_ist(it.source_published_at))} &middot; "
                               f"first seen {_e(fmt_ist(first_seen))}")
    body += dz.row(dz.callout(_e(lead), accent="gold", title="Released"), pad=dz.BLOCK_PAD)
    body += dz.row(hs.section_caption("i", "Policy discussion", "verbatim paragraphs from the minutes")
                   + hs.prose("<br><br>".join(_e(p) for p in shown)), pad=dz.SECTION_PAD)
    docs = [Doc("Press release", it.url)]
    if urls.get("html"):
        docs.append(Doc("Minutes (HTML)", urls["html"]))
    if urls.get("pdf"):
        docs.append(Doc("Minutes (PDF)", urls["pdf"]))
    body += dz.row(hs.section_caption("", "Source documents") + hs.links(docs), pad=dz.SECTION_PAD)
    body += dz.row(dz.colophon(_provenance("Federal Reserve Board", it.ref, mode), hs.DISCLAIMER),
                   pad="26px 24px 26px 24px")
    text = [lead, ""] + [f"> {p}" for p in shown] + ["", *[f"{d.name}: {d.url}" for d in docs],
                                                     "", f"First seen: {fmt_ist(first_seen)}", it.ref]
    return Message(ref=it.ref, stage="stage1", kind="realtime", subject=subject,
                   body_html=dz.doc_open(_e(subject), _e(lead)) + body + dz.DOC_CLOSE, body_text="\n".join(text))


# ---- fed_beige ---------------------------------------------------------------------------------------

BEIGE_PDF = BASE + "/monetarypolicy/files/BeigeBook_{d}.pdf"
FRESH = timedelta(days=2)


def _et_time(s: str | None) -> time:
    """'2:00 p.m.' -> 14:00; anything unparseable -> 14:00 (every Beige Book since 1996 posts at 2 p.m.)."""
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*([ap])\.?m\.?", s or "", re.I)
    if not m:
        return time(14, 0)
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return time(h, int(m.group(2)))


def beige_dates(raw: bytes) -> list[datetime]:
    """Beige Book release times (tz-aware ET) from calendar.json, newest first."""
    data = json.loads(raw.decode("utf-8-sig"))
    out = set()
    for ev in data.get("events", []):
        if ev.get("type") != "Beige":
            continue
        try:
            y, mo = (int(x) for x in str(ev.get("month", "")).split("-"))
            for day in re.findall(r"\d+", str(ev.get("days", ""))):
                out.add(datetime.combine(date(y, mo, int(day)), _et_time(ev.get("time")), tzinfo=ET))
        except ValueError:
            continue
    return sorted(out, reverse=True)


_NS_START = re.compile(r"^National Summary$")
_NS_END = re.compile(r"^Highlights by Federal Reserve District")


def beige_national_summary(text: str) -> list[str]:
    """Paragraph lines of the National Summary (after the table of contents) up to the district highlights.
    Report notes and page furniture are left out."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if _NS_START.match(ln.strip())), None)
    if start is None:
        return []
    out = []
    for ln in lines[start + 1:]:
        if _NS_END.match(ln):
            break
        if len(ln) >= content.MIN_PARA and not ln.startswith("Note:"):
            out.append(ln)
    return out


class FedBeige(SourceAdapter):
    doc_type = "fed_beige"

    def _item(self, at: datetime, document_id: int | None = None) -> RawItem:
        k = at.strftime("%Y%m%d")
        return RawItem(source_id=self.cfg.id, ext_id=f"beige:{k}", url=BEIGE_PDF.format(d=k),
                       title=f"Beige Book, {at.strftime('%B')} {at.day}, {at.year}",
                       source_published_at=at, published_raw=at.strftime("%Y-%m-%d %H:%M ET"),
                       document_id=document_id, meta={"route_hint": "us_morning_wrap", "kind": "Beige Book",
                                                      "scope_reason": "Beige Book (Fed district survey)"})

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        """Pure part (replay): the calendar's past Beige dates older than FRESH, unfetched (history for the
        baseline). poll() adds the fresh ones once their PDF is live."""
        now = res.fetched_at
        return [self._item(at) for at in beige_dates(res.content) if now - at > FRESH]

    def _calendar(self) -> FetchResult:
        url = self.cfg.urls["calendar"]
        res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type="fed_beige:calendar", expect="json")
        if not res.not_modified:
            return res
        row = self.ctx.db.latest_document(url)
        if row is not None:
            return res.model_copy(update={"content": self.ctx.archive.get(row["storage_key"]),
                                          "document_id": row["id"], "fetched_at": utcnow()})
        return self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type="fed_beige:calendar", expect="json",
                                    conditional=False)

    def poll(self) -> list[RawItem]:
        cal = self._calendar()
        now = utcnow()
        items = self.parse(cal.model_copy(update={"fetched_at": now}), "calendar")
        for at in beige_dates(cal.content):
            if not (timedelta(0) <= now - at <= FRESH):
                continue
            url = BEIGE_PDF.format(d=at.strftime("%Y%m%d"))
            try:
                r = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type="fed_beige:pdf", expect="pdf",
                                         parent_id=cal.document_id)
            except FetchError as e:
                if e.kind == "http" and e.status == 404:
                    log.info("beige_not_yet_posted", url=url)
                    continue
                raise
            items.append(self._item(at, r.document_id))
        return items

    def digest_body(self, it: RawItem) -> content.Body:
        r = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:fed_beige_pdf", expect="pdf", conditional=False)
        text = content.pdf_body(r.content)
        paras = beige_national_summary(text) or content._paragraphs(text)
        return content.Body(text, paras, it.url, it.url, r.document_id)
