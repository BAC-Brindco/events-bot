"""India Tier-2 sector regulators and ministries for the 18:30 IST Daily Macro Digest ("Regulators" section).

Phase 0 evidence: docs/phase0/india_t23.md (probed 2026-10-01, re-verified 2026-11-26). None of these
bodies publishes a usable feed, and PIB does not echo them (the statutory regulators never, the ministries
only for ministerial news), so each is polled at its own listing / JSON backend:

  trai        /notifications/press-release      Drupal grid, "Date of Publication dd/mm/yyyy", numbered PRs.
                                                TRAI announces every regulation, recommendation, consultation
                                                paper, direction and the monthly subscription data with a PR.
                                                (The site-wide rss.xml is 10 items of mixed noise; not used.)
  irdai       /press-releases, /circulars       Liferay tables, bilingual "Hindi / English" titles, dd-mm-yyyy.
  pfrda       /regulatory-framework/regulations, /circulars/active-circulars   Liferay cards, "Issue Date".
  cerc        Draft_reg.html, Current_reg.html  Static IIS, ETag + Last-Modified (conditional GET works);
                                                the first TLS handshake intermittently fails (retry fixes it).
  power, dot, meity   NIC headless-WordPress JSON /cms/wp-json/post-page/whats_new (post_date_gmt). The
                                                `documents` endpoints are mostly admin orders and timed out /
                                                504'd on 2026-11-26 (DoT >120 s, MeitY 504), so only whats_new.
  pngrb       eng-web/whatsnew/whats-new-json.js  hand-edited `var data = [...]` with // comments.
  ppac        homepage "Important News" + "Govt. Notifications"; the unix epoch in the file name is the
                                                upload time.
  cdsco       Notifications/Public-Notices (OpenCms table) and the NSQ DataTables JSON (cdscoonline).
  npci        Akamai/F5 blocks every non-browser client (F-05): registered disabled.

Scope (F-29, desk 2026-11): keep rules, regulations, directions, tariff orders, consultation papers and policy
price changes; drop hearings, extensions of comment deadlines, entity-specific orders and penalties,
recruitment, tenders, events, corrigenda. Every item carries meta route_hint 'india_eod' or 'drop'.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime, time, timezone
from urllib.parse import quote, urljoin

from selectolax.parser import HTMLParser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST
from ...digest import content

EOD = "india_eod"


def _ws(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _tree(b: bytes) -> HTMLParser:
    return HTMLParser(b.decode("utf-8", "replace"))


def _date(s: str, *fmts: str) -> date | None:
    s = _ws(s)
    for f in fmts:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def _midnight(d: date | None) -> datetime | None:
    return datetime.combine(d, time(0, 0), tzinfo=IST) if d else None


def _h(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _url(base: str, href: str) -> str:
    return quote(urljoin(base, href.strip()), safe=":/?&=%#;,+@!$'()*~")


def scope_of(title: str, keep: re.Pattern, drop: re.Pattern | None, what: str) -> tuple[str, str]:
    """(route_hint, scope_reason): drop rules win, then keep rules; anything else is out of scope."""
    if drop is not None and (m := drop.search(title)):
        return "drop", f"{what}: '{m.group(0)}' is procedural / entity-specific"
    if (m := keep.search(title)):
        return EOD, f"{what}: rule / policy ('{m.group(0)}')"
    return "drop", f"{what}: outside desk scope"


class _Regulator(SourceAdapter):
    """Shared poll (retrying: several of these hosts flap) and digest body (PDF when there is one)."""

    doc_type = "india2"
    tries = 2
    body_selector = "body"

    def poll(self) -> list[RawItem]:
        items: list[RawItem] = []
        for name, url in self.cfg.urls.items():
            res = self.ctx.fetcher.get_with_retry(url, tries=self.tries, base_delay=3, source_id=self.cfg.id,
                                                  doc_type=f"{self.cfg.id}:{name}",
                                                  expect=self.cfg.health_rules.expect)
            if not res.not_modified:
                items += self.parse(res, name)
        return items

    def _item(self, *, ext_id: str, url: str, title: str, when: datetime | None, raw: str | None,
              res: FetchResult, scope: tuple[str, str], kind: str, date_only: bool = True, **meta) -> RawItem:
        return RawItem(source_id=self.cfg.id, ext_id=ext_id, url=url, title=title, source_published_at=when,
                       published_raw=raw, document_id=res.document_id,
                       meta={"route_hint": scope[0], "scope_reason": scope[1], "kind": kind,
                             "date_only": date_only, **{k: v for k, v in meta.items() if v is not None}})

    def _get(self, url: str, expect: str, what: str, **kw) -> FetchResult:
        return self.ctx.fetcher.get_with_retry(url, tries=3, base_delay=4, source_id=self.cfg.id,
                                               doc_type=f"digest:{self.cfg.id}_{what}", expect=expect,
                                               conditional=False, **kw)

    def digest_body(self, it: RawItem) -> content.Body:
        pdf = it.meta.get("pdf_url") or (it.url if re.search(r"\.pdf$", it.url, re.I) else None)
        if pdf:
            r = self._get(pdf, "pdf", "pdf")
            text = content.pdf_body(r.content)
            notes = [] if len(text) > 200 else ["The PDF has no text layer (scanned image); see the PDF."]
            return content.Body(text, content._paragraphs(text), it.url, pdf, r.document_id, notes=notes)
        r = self._get(it.url, "html", "page")
        text = content.html_body(r.content, self.body_selector)
        return content.Body(text, content._paragraphs(text), it.url, None, r.document_id)


# ---- TRAI ------------------------------------------------------------------------------------------------
TRAI = "https://www.trai.gov.in"
_TRAI_PRNO = re.compile(r"Press Release No\.?\s*(\d+)", re.I)
_TRAI_DROP = re.compile(r"Mobile Network Quality|Extension of (?:the )?last date|last date .* extended|"
                        r"Open House|workshop|seminar|conference|awareness|hackathon|\blaunch(?:es|ed)?\b|"
                        r"Unsolicited Commercial|\bUCC\b|Corrigendum|recruitment|vacanc|felicitat|"
                        r"Quality of Service .* (?:district|city|areas)", re.I)
_TRAI_KEEP = re.compile(r"Regulations?\b|Recommendations?\b|Consultation Paper|\bTariff|\bDirections?\b|"
                        r"Spectrum|Telecom Subscription Data|Performance Indicators?|\bdraft\b|Guidelines|"
                        r"Amendment|\bOrder\b|Authori[sz]ation|Framework", re.I)


def trai_parse(content_: bytes) -> list[dict]:
    out = []
    for g in _tree(content_).css(".views-view-grid__item-inner"):
        t, d = g.css_first(".views-field-title"), g.css_first(".views-field-field-date .field-content")
        a, share = g.css_first(".views-field-field-file a[href]"), g.css_first("[data-a2a-url]")
        if t is None or d is None:
            continue
        no = _TRAI_PRNO.search(a.attributes.get("aria-label") or "") if a else None
        page = (share.attributes.get("data-a2a-url") or "") if share else ""
        out.append({"title": _ws(t.text()), "date": _date(d.text(), "%d/%m/%Y"), "raw": _ws(d.text()),
                    "no": int(no.group(1)) if no else None,
                    "pdf": _url(TRAI, a.attributes["href"]) if a else None,
                    "page": re.sub(r"^http://", "https://", page) or None})
    return [r for r in out if r["title"]]


class Trai(_Regulator):
    doc_type = "trai"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in trai_parse(res.content):
            year = r["date"].year if r["date"] else 0
            ext = f"pr:{year}:{r['no']}" if r["no"] else f"h:{_h(r['raw'], r['title'])}"
            items.append(self._item(
                ext_id=ext, url=r["page"] or r["pdf"] or res.url, title=r["title"], when=_midnight(r["date"]),
                raw=r["raw"], res=res, scope=scope_of(r["title"], _TRAI_KEEP, _TRAI_DROP, "TRAI"),
                kind="Press release", number=f"PR No. {r['no']}/{year}" if r["no"] else None, pdf_url=r["pdf"]))
        return items


# ---- IRDAI -----------------------------------------------------------------------------------------------
IRDAI = "https://irdai.gov.in"
_IRDAI_DROP = re.compile(r"Orders? in the matters?|Formats for|\bpenalt|Grievance|\bcamps?\b|awareness|Observance|"
                         r"recruitment|vacanc|appointment|workshop|seminar|conference|Foundation Day|"
                         r"Hindi|Swachh|Vigilance|Corrigendum|campaign|study groups?|constitutes|\bforms\b|"
                         r"Migration of|returns? (?:filing|submission)|\bUIN\b|portal|cyber|"
                         r"extension of (?:time|timeline|last date)|clarification", re.I)
_IRDAI_KEEP = re.compile(r"Regulations?\b|Master Circular|Consultation paper|Exposure Draft|\bdraft\b|Guidelines|Framework|"
                         r"Amendment|Notification|commission|expenses? of management|solvency|investment|"
                         r"capital|\bFDI\b|foreign|premium|tariff|Motor|Insurance Laws|Bima|"
                         r"Risk[- ]based|composite licen", re.I)
_LATIN = re.compile(r"[A-Za-z]")


def irdai_title(s: str) -> str:
    """'हिंदी शीर्षक / English title' -> the English half (verbatim); English-only titles pass through."""
    s = _ws(s)
    parts = [p.strip() for p in s.split(" / ")]
    eng = [p for p in parts if _LATIN.search(p) and len(_LATIN.findall(p)) > len(p) // 3]
    return " / ".join(eng) if eng else s


def irdai_parse(content_: bytes) -> list[dict]:
    out = []
    for tb in _tree(content_).css("table"):
        rows = tb.css("tr")
        if not rows:
            continue
        head = [_ws(c.text()) for c in rows[0].css("th, td")]
        if "Short Description" not in head or "Last Updated" not in head:
            continue
        ci = {h: i for i, h in enumerate(head)}
        for tr in rows[1:]:
            tds = tr.css("td")
            det = tr.css_first('a[href*="document-detail"]')
            if len(tds) < len(head) or det is None:
                continue
            m = re.search(r"documentId=(\d+)", det.attributes.get("href") or "")
            pdf = tr.css_first('a[href*="/documents/"]')
            raw_d = _ws(tds[ci["Last Updated"]].text())
            out.append({"id": int(m.group(1)) if m else None,
                        "title": irdai_title(tds[ci["Short Description"]].text()),
                        "title_full": _ws(tds[ci["Short Description"]].text()),
                        "subtitle": irdai_title(tds[ci["Sub Title"]].text()) if "Sub Title" in ci else "",
                        "ref": _ws(tds[ci["Reference No"]].text()) if "Reference No" in ci else "",
                        "date": _date(raw_d, "%d-%m-%Y"), "raw": raw_d,
                        "pdf": pdf.attributes["href"] if pdf is not None else None})
        break
    return [r for r in out if r["id"] and r["title"]]


class Irdai(_Regulator):
    doc_type = "irdai"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        kind = {"press": "Press release", "circulars": "Circular"}.get(url_name, url_name)
        return [self._item(
            ext_id=f"doc:{r['id']}", url=f"{IRDAI}/document-detail?documentId={r['id']}", title=r["title"],
            when=_midnight(r["date"]), raw=r["raw"], res=res,
            scope=scope_of(f"{r['title']} {r['subtitle'] if url_name == 'circulars' else ''}", _IRDAI_KEEP,
                           _IRDAI_DROP, f"IRDAI {kind.lower()}"),
            kind=r["subtitle"] or kind, pdf_url=r["pdf"], feed=url_name,
            number=(r["ref"] if r["ref"] and r["ref"] != r["title_full"] else None))
            for r in irdai_parse(res.content)]


# ---- PFRDA -----------------------------------------------------------------------------------------------
_PFRDA_REG_DROP = re.compile(r"Adjudicating Officer|Staff|Employees|Salary|Allowances|Conduct of Business|"
                             r"Meetings of the Authority|Right to Information", re.I)
# Circulars are mostly NPS operations (PoP charges, KYC, DLC); only scheme design / investment rules for the
# pension-fund managers can move flows.
_PFRDA_CIRC_KEEP_TAGS = {"Pension Fund"}
_PFRDA_CIRC_KEEP = re.compile(r"investment|asset allocation|equity|framework|scheme|fee|charges", re.I)
_PFRDA_CIRC_DROP = re.compile(r"KYC|Points? of Presence|\bPoPs?\b|grievance|awareness|Exception Reports?|"
                              r"Audit reports?|Advisory on mandatory|Digital Life Certificate|clarification|"
                              r"corrigendum|extension", re.I)


def pfrda_parse(content_: bytes) -> list[dict]:
    out = []
    for card in _tree(content_).css(".basic-card"):
        a, t = card.css_first("a.basic-link[href]") or card.css_first("a[href]"), card.css_first(".basic-title")
        if a is None or t is None:
            continue
        meta = {}
        for mi in card.css(".meta-item"):
            k = mi.css_first("strong")
            if k is not None:
                meta[_ws(k.text()).rstrip(":")] = _ws(mi.text()).split(":", 1)[-1].strip()
        tag = card.css_first(".basic-tag")
        href = (a.attributes.get("href") or "").split("?", 1)[0]
        out.append({"title": _ws(t.text()), "url": href, "slug": href.rsplit("/", 1)[-1],
                    "tag": _ws(tag.text()) if tag else "", "ref": meta.get("Ref", ""),
                    "raw": meta.get("Issue Date", ""), "date": _date(meta.get("Issue Date", ""), "%d-%m-%Y")})
    return [r for r in out if r["slug"]]


class Pfrda(_Regulator):
    doc_type = "pfrda"
    body_selector = ".journal-content-article, .portlet-body, main, body"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in pfrda_parse(res.content):
            if url_name == "regulations":
                sc = scope_of(r["title"], re.compile(r"Regulations?"), _PFRDA_REG_DROP, "PFRDA regulation")
                kind = "Regulation"
            else:
                kind = "Circular"
                sc = scope_of(r["title"], _PFRDA_CIRC_KEEP, _PFRDA_CIRC_DROP, "PFRDA circular")
                if sc[0] == EOD and r["tag"] not in _PFRDA_CIRC_KEEP_TAGS:
                    sc = ("drop", f"PFRDA circular for '{r['tag']}' (NPS operations)")
            items.append(self._item(ext_id=f"{url_name}:{r['slug'][:150]}", url=r["url"], title=r["title"],
                                    when=_midnight(r["date"]), raw=r["raw"], res=res, scope=sc, kind=kind,
                                    number=r["ref"] or None, category=r["tag"] or None))
        return items

    def digest_body(self, it: RawItem) -> content.Body:
        """The /w/<slug> page links the PDF; quote the PDF, else the page."""
        r = self._get(it.url, "html", "page")
        a = next((x for x in _tree(r.content).css("a[href]")
                  if re.search(r"/documents/.*\.pdf(?:$|[?/])", x.attributes.get("href") or "", re.I)), None)
        if a is not None:
            pdf = _url(it.url, a.attributes["href"])
            p = self._get(pdf, "pdf", "pdf", parent_id=r.document_id)
            text = content.pdf_body(p.content)
            if len(text) > 200:
                return content.Body(text, content._paragraphs(text), it.url, pdf, p.document_id)
        text = ""
        for sel in self.body_selector.split(", "):
            try:
                text = content.html_body(r.content, sel)
            except ValueError:
                continue
            if len(text) > 200:
                break
        return content.Body(text, content._paragraphs(text), it.url, None, r.document_id)


# ---- CERC ------------------------------------------------------------------------------------------------
CERC = "https://cercind.gov.in/"
_CERC_DROP = re.compile(r"Appointment of Consultants|Conduct of Business|Payment of Fees|\bStaff\b|Salary|"
                        r"Allowances|Right to Information|Hindi|Recruitment", re.I)
_CERC_KEEP = re.compile(r"Regulations?|Guidelines|Mechanism|Procedure|Framework|Tariff|Market|charges|"
                        r"\bOrder\b|Discussion Paper|Staff Paper|Approach Paper", re.I)
_CERC_SL = re.compile(r"^(\d+)\.?$")
_CERC_DOC = re.compile(r"Draft (?:Amendment|Notification|Order|Regulations?)|Supplementary Draft|"
                       r"Discussion Paper|Staff Paper|Approach Paper", re.I)


def cerc_parse(content_: bytes, final: bool) -> list[dict]:
    out = []
    t = _tree(content_).css_first("table")
    for tr in (t.css("tr") if t else []):
        tds = tr.css("td")
        if len(tds) < 2 or not (m := _CERC_SL.match(_ws(tds[0].text()))):
            continue
        links = [(_ws(a.text()), a.attributes.get("href") or "") for a in tds[1].css("a[href]")]
        full = _ws(tds[1].text())
        title = full.split(links[0][0], 1)[0].strip() if links and links[0][0] else full
        pick = None
        if final:
            pick = next((h for x, h in links if re.search(r"Notification|Guidelines", x, re.I)), None) \
                or next((h for x, h in links if re.search(r"Gazette", x, re.I)), None)
        else:
            pick = next((h for x, h in links if _CERC_DOC.search(x)), None)
        pick = pick or next((h for _, h in links if h.lower().endswith(".pdf")), None)
        raw = _ws(tds[3].text()) if final and len(tds) > 3 else ""
        title = re.sub(r"\s+New$", "", title).rstrip(" -–")          # "New" badge after fresh rows
        out.append({"sl": int(m.group(1)), "title": title, "url": _url(CERC, pick) if pick else None,
                    "gazette": _ws(tds[2].text()) if final and len(tds) > 2 else "",
                    "raw": raw or None, "date": _date(raw, "%d.%m.%Y") if raw else None})
    return out


class Cerc(_Regulator):
    doc_type = "cerc"
    tries = 3            # intermittent TLS UNEXPECTED_EOF on the first handshake (Phase 0, again 2026-11-26)

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        final = url_name == "regulations"
        what = "CERC regulation" if final else "CERC draft regulation"
        return [self._item(
            ext_id=f"{url_name}:{r['sl']}", url=r["url"] or f"{res.url}?sl={r['sl']}", title=r["title"], when=_midnight(r["date"]),
            raw=r["raw"], res=res, scope=scope_of(r["title"], _CERC_KEEP, _CERC_DROP, what),
            kind="Regulation (notified)" if final else "Draft regulation / consultation",
            number=f"Gazette No. {r['gazette']}" if r["gazette"] else None,
            date_only=r["date"] is not None)
            for r in cerc_parse(res.content, final) if r["title"]]


# ---- NIC WordPress JSON: Ministry of Power, DoT, MeitY -------------------------------------------------
_NIC_DROP = re.compile(r"recruitment|Selection for the post|appointment|vacanc|Managing Director|"
                       r"\bDirector \(|\bMember \(|Salary|Allowances|Conditions of Service|"
                       r"Extension of (?:the )?timeline|Extension of timeline|last date|extended|"
                       r"Invitation for selection|\btender|invites? bids|^Extension of|Hindi|Swachh|Internship|"
                       r"Applications|Testing Agency|Grant\b|Hackathon|Challenge|Award|\bLive!|Address Note|"
                       r"HMoC|deputation|posting|promotion|Jan Vishwas|Corrigendum", re.I)
_NIC_KEEP = {
    "power": re.compile(r"\bRules\b|\bBill\b|Policy|Directions?\b|Section 11|Guidelines|Tariff|Framework|"
                        r"Amendment|Notification|Advisory to States|Late Payment Surcharge|Bidding|"
                        r"Renewable|Purchase Preference|Bank Guarantee|Surety", re.I),
    "dot": re.compile(r"Policy|Scheme|Spectrum|Licen[cs]|Frequency Allocation|\bRules\b|Notification|"
                      r"Guidelines|Tariff|Telecommunications Act|Satellite|Auction|\bPLI\b|Framework", re.I),
    "meity": re.compile(r"\bRules\b|Policy|Notification|\bAct\b|Guidelines|\bScheme\b|\bPLI\b|Semiconductor|"
                        r"Framework|Advisory|Data Protection|DPDP|Amendment", re.I),
}
NIC_PAGE = {"power": "https://www.powermin.gov.in", "dot": "https://www.dot.gov.in",
            "meity": "https://www.meity.gov.in"}


def nic_parse(content_: bytes) -> list[dict]:
    data = json.loads(content_)
    out = []
    for p in (data.get("posts") if isinstance(data, dict) else data) or []:
        acf = p.get("acf_data") or {}
        files = acf.get("file") or []
        pdf = (acf.get("pdf") or {}).get("url") if isinstance(acf.get("pdf"), dict) else None
        link = acf.get("external_link") or acf.get("internal_link") or None
        try:
            gmt = datetime.strptime(p.get("post_date_gmt") or "", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            gmt = None
        out.append({"id": p.get("ID"), "title": _ws(p.get("post_title")), "gmt": gmt,
                    "raw": p.get("post_date"), "file_ids": [f for f in files if isinstance(f, int)],
                    "pdf": pdf, "link": link, "type": acf.get("type") or ""})
    return [r for r in out if r["id"] and r["title"]]


def nic_file_pdf(content_: bytes) -> tuple[str | None, str | None]:
    """post-page/post?id=<file id> -> (pdf url, file_date) of the central_documents record."""
    p = (json.loads(content_) or {}).get("posts") or {}
    acf = p.get("acf_data") or {}
    pdf = acf.get("pdf") if isinstance(acf.get("pdf"), dict) else {}
    return (pdf or {}).get("url"), acf.get("file_date")


class NicWhatsNew(_Regulator):
    """`whats_new` of the NIC next-gen ministry sites. File ids resolve via /cms/wp-json/post-page/post?id=N."""
    doc_type = "nic"

    @property
    def family(self) -> str:
        return self.cfg.id.split("_")[0]

    def _host(self) -> str:
        return NIC_PAGE[self.family]

    def file_api(self, file_id: int) -> str:
        return f"{self._host()}/cms/wp-json/post-page/post?id={file_id}"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        label = {"power": "Ministry of Power", "dot": "DoT", "meity": "MeitY"}[self.family]
        for r in nic_parse(res.content):
            if r["pdf"]:
                url = r["pdf"]
            elif r["link"]:
                url = r["link"] if r["link"].startswith("http") else urljoin(self._host(), r["link"])
            elif r["file_ids"]:
                url = self.file_api(r["file_ids"][0])       # resolved to the PDF by poll() / digest_body
            else:
                url = f"{self._host()}/cms/wp-json/post-page/post?id={r['id']}"
            items.append(self._item(
                ext_id=f"post:{r['id']}", url=url, title=r["title"], when=r["gmt"], raw=r["raw"], res=res,
                scope=scope_of(r["title"], _NIC_KEEP[self.family], _NIC_DROP, label), kind="What's new",
                date_only=False, file_ids=r["file_ids"] or None, pdf_url=r["pdf"]))
        return items

    def poll(self) -> list[RawItem]:
        """Parse, then resolve the PDF of kept items the bot has not stored yet (one request each), so the
        stored URL is the document itself. The baseline poll (no stored items) skips resolution."""
        items = super().poll()
        db = getattr(self.ctx, "db", None)
        if db is None or not items:
            return items
        known = {r["ext_id"] for r in db.q("select ext_id from items where source_id = %s", (self.cfg.id,))}
        if not known:
            return items
        for it in items:
            if it.ext_id in known or it.meta.get("route_hint") == "drop" or not it.meta.get("file_ids") \
                    or it.meta.get("pdf_url"):
                continue
            try:
                r = self.ctx.fetcher.get_with_retry(self.file_api(it.meta["file_ids"][0]), tries=2, base_delay=3,
                                                    source_id=self.cfg.id, doc_type=f"{self.cfg.id}:file",
                                                    expect="json", conditional=False)
                pdf, fdate = nic_file_pdf(r.content)
            except Exception:  # noqa: BLE001  the listing item stands without its PDF link
                continue
            if pdf:
                it.url, it.meta["pdf_url"], it.meta["file_date"] = pdf, pdf, fdate
        return items

    def digest_body(self, it: RawItem) -> content.Body:
        if not it.meta.get("pdf_url") and it.meta.get("file_ids"):
            r = self._get(self.file_api(it.meta["file_ids"][0]), "json", "file")
            pdf, _ = nic_file_pdf(r.content)
            if pdf:
                it = it.model_copy(update={"meta": {**it.meta, "pdf_url": pdf}})
        return super().digest_body(it)


# ---- PNGRB -----------------------------------------------------------------------------------------------
PNGRB_BASE = "https://www.pngrb.gov.in/eng-web/whatsnew/"     # relative urls in the JS resolve against eng-web/
_PNGRB_PAGE = "https://www.pngrb.gov.in/eng-web/"
_PNGRB_KEEP_CAT = re.compile(r"^(?:Tariff Order|Gazette Notification|PPPL Tariff Regulations|"
                             r"Public Consultation Document|PCD|.*DPNG & CNG Prices|Guidelines.*|Amendment|"
                             r"Regulations?)\b", re.I)
_PNGRB_PN_KEEP = re.compile(r"Regulations?|Tariff|Guidelines|Unified|\bdraft\b|Consultation Document|"
                            r"\bTrading\b|introduction of", re.I)
_PNGRB_DROP = re.compile(r"^Additional Comments|Public Consultation Process|M/s\.?|extended|extension|last date|Corrigendum|hearing|Open House|"
                         r"Minutes|\bEOI\b|\bbids?\b|Invitation of Bids|Clarification|Addendum", re.I)


def _strip_js_comments(s: str) -> str:
    """Remove // line comments and /* */ blocks that sit outside string literals."""
    out, i, n, q = [], 0, len(s), None
    while i < n:
        c = s[i]
        if q:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
            out.append(c)
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def pngrb_parse(content_: bytes) -> list[dict]:
    s = _strip_js_comments(content_.decode("utf-8-sig", "replace"))
    a, b = s.find("["), s.rfind("]")
    if a < 0 or b < 0:
        raise ValueError("PNGRB whats-new: no array")
    body = re.sub(r",\s*([\]}])", r"\1", s[a:b + 1])          # trailing commas
    rows = json.loads(body)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cat, desc, url = _ws(r.get("title")), _ws(r.get("Title_Desc")), (r.get("url") or "").strip()
        d = _date(r.get("created_date") or "", "%Y-%m-%d")
        full = _url(_PNGRB_PAGE, url) if url else _PNGRB_PAGE
        if not re.search(r"\.pdf$", full, re.I):
            # Many rows point at the same section page (public_notice.html); the bot dedupes on URL across
            # all items, so the row key goes in the query string (static page, the server ignores it).
            full = f"{full}?item={_h(cat, desc, url)[:10]}"
        out.append({"cat": cat, "desc": desc, "date": d, "raw": r.get("created_date") or
                    f"{r.get('day', '')} {r.get('month', '')}".strip(), "url": full})
    # The hand-edited file repeats rows verbatim (~10% on 2026-11-26); keep the first (newest) copy.
    seen, uniq = set(), []
    for r in out:
        k = (r["cat"], r["desc"], r["url"])
        if r["desc"] and k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def pngrb_scope(cat: str, desc: str) -> tuple[str, str]:
    if (m := _PNGRB_DROP.search(desc)):
        return "drop", f"PNGRB: '{m.group(0)}' is procedural"
    if _PNGRB_KEEP_CAT.search(cat):
        return EOD, f"PNGRB {cat}"
    if re.match(r"Public Notice|Press Release", cat, re.I) and (m := _PNGRB_PN_KEEP.search(desc)):
        return EOD, f"PNGRB {cat.lower()}: rule / tariff ('{m.group(0)}')"
    return "drop", f"PNGRB {cat or 'item'}: outside desk scope"


class Pngrb(_Regulator):
    doc_type = "pngrb"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        return [self._item(
            ext_id=f"h:{_h(r['cat'], r['desc'], r['url'])}", url=r["url"], title=f"{r['cat']}: {r['desc']}",
            when=_midnight(r["date"]), raw=r["raw"], res=res, scope=pngrb_scope(r["cat"], r["desc"]),
            kind=r["cat"] or "What's new", date_only=r["date"] is not None)
            for r in pngrb_parse(res.content)]


# ---- PPAC ------------------------------------------------------------------------------------------------
_PPAC_SECTIONS = ("importantnews", "govtnotlaws")       # "whatsnew" repeats importantnews under new epochs
_PPAC_EPOCH = re.compile(r"file=([a-z_]+)/(\d{10})_")
_PPAC_KEEP = re.compile(r"Domestic Natural Gas Price|Gas Price Ceiling|APM|\bNotification\b|\bOrder\b|"
                        r"Guidelines|\bRules\b|Policy|subsidy|pricing", re.I)
_PPAC_DROP = re.compile(r"\bRSP of\b|Daily Price|Indian Crude Basket|\bICB\b|tender|Advertisement|Internship|"
                        r"POSH|Sexual Harassment|Complaints committee|Report|Snapshot|Reckoner|Flash|"
                        r"Nodal agency", re.I)
_PPAC_LEAD = re.compile(r"^\d{1,2} [A-Z][a-z]{2} \d{4}\s*(?:Notification|Circular|Act|Rules)?\s*")


def ppac_parse(content_: bytes) -> list[dict]:
    out, seen = [], set()
    for a in _tree(content_).css('a[href*="download.php?file="]'):
        href = a.attributes.get("href") or ""
        m = _PPAC_EPOCH.search(href)
        if not m or m.group(1) not in _PPAC_SECTIONS or href in seen:
            continue
        seen.add(href)
        title = _ws(a.attributes.get("title")) or _ws(a.text())
        title = re.sub(r"\s*\(Pdf, [^)]*\)$", "", _PPAC_LEAD.sub("", title))
        epoch = int(m.group(2))
        when = datetime.fromtimestamp(epoch, tz=timezone.utc) if 1_400_000_000 < epoch < 2_200_000_000 else None
        out.append({"section": m.group(1), "title": title, "url": _url("https://ppac.gov.in/", href),
                    "key": href.split("file=", 1)[1], "when": when, "epoch": epoch})
    return [r for r in out if r["title"]]


class Ppac(_Regulator):
    doc_type = "ppac"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        return [self._item(
            ext_id=f"file:{r['key'][:200]}", url=r["url"], title=r["title"], when=r["when"], raw=str(r["epoch"]),
            res=res, scope=scope_of(r["title"], _PPAC_KEEP, _PPAC_DROP, "PPAC"),
            kind="Government notification" if r["section"] == "govtnotlaws" else "Important news",
            date_only=False, section=r["section"])
            for r in ppac_parse(res.content)]


# ---- CDSCO -----------------------------------------------------------------------------------------------
CDSCO = "https://cdsco.gov.in"
CDSCO_NSQ_PAGE = "https://cdsco.gov.in/opencms/opencms/en/Notifications/nsq-drugs/"
_CDSCO_DROP = re.compile(r"Cancellation|Surrender|Misleading|Appointment|Reminder|onboarding|WHO Alert|"
                         r"falsified|Clarification|list of laboratories|updated list|Corrigendum|"
                         r"Registration Certificate|Ethics Committee|SUGAM|extension|\bcosmetics?\b|theft|"
                         r"spurious|M/s\.?", re.I)
_CDSCO_KEEP = re.compile(r"\bban(?:ned)?\b|prohibit|Fixed Dose Combination|\bFDCs?\b|un-?approved drug|"
                         r"Draft .*Rules|Amendment Rules|\bG\.?S\.?R\.?\b|Gazette|Schedule M\b|"
                         r"Drugs Rules|Medical Devices? Rules|price|Enforcement against", re.I)


def cdsco_parse(content_: bytes) -> list[dict]:
    out = []
    t = _tree(content_).css_first("table")
    for tr in (t.css("tr")[1:] if t else []):
        tds = tr.css("td")
        a = tr.css_first('a[href*="num_id="]')
        if len(tds) < 3 or a is None:
            continue
        href = a.attributes["href"]
        nid = href.split("num_id=", 1)[1]
        try:
            num = int(base64.b64decode(nid + "=" * (-len(nid) % 4)).decode())
        except ValueError:
            num = None
        raw = _ws(tds[2].text())
        out.append({"num": num, "nid": nid, "title": _ws(tds[1].text()), "raw": raw,
                    "date": _date(raw, "%Y-%b-%d"), "url": _url(CDSCO, href)})
    return [r for r in out if r["title"] and (r["num"] or r["nid"])]


def nsq_parse(content_: bytes) -> dict[str, list[dict]]:
    """DataTables JSON (served as text/html) -> rows grouped by reporting month ('OCT-2026')."""
    try:
        txt = content_.decode("utf-8")
    except UnicodeDecodeError:          # the table carries cp1252 bytes (e.g. 0xAE for the registered sign)
        txt = content_.decode("cp1252", "replace")
    rows = (json.loads(txt) or {}).get("aaData") or []
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(_ws(r.get("dt_reporting_month_year")).upper(), []).append(r)
    return {k: v for k, v in by.items() if re.match(r"^[A-Z]{3}-\d{4}$", k)}


def nsq_text(month: str, rows: list[dict]) -> str:
    src = Counter(_ws(r.get("str_reporting_source")) or "unstated" for r in rows)
    lines = [f"Drug samples declared Not of Standard Quality (NSQ) for the reporting month {month}: "
             f"{len(rows)} samples ({', '.join(f'{k}: {v}' for k, v in sorted(src.items()))})."]
    for r in rows:
        lines.append(" | ".join(_ws(r.get(k)) for k in ("str_product_name", "str_batch_no", "str_manufactured_by",
                                                       "str_nsq_result", "str_reported_by_lab_or_state")))
    return "\n".join(lines)


class Cdsco(_Regulator):
    doc_type = "cdsco"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        return [self._item(
            ext_id=f"notice:{r['num'] or r['nid']}", url=r["url"], title=r["title"], when=_midnight(r["date"]),
            raw=r["raw"], res=res, scope=scope_of(r["title"], _CDSCO_KEEP, _CDSCO_DROP, "CDSCO notice"),
            kind="Public notice")
            for r in cdsco_parse(res.content)]

    def digest_body(self, it: RawItem) -> content.Body:
        """download_file_division.jsp answers a one-line `<iframe src='/opencms/resources/...pdf'>` wrapper."""
        r = self._get(it.url, "html", "wrapper")
        m = re.search(r"""<iframe[^>]+src=['"]([^'"]+)['"]""", r.content.decode("utf-8", "replace"), re.I)
        if not m:
            raise ValueError("CDSCO download wrapper has no iframe src")
        pdf = _url(CDSCO, m.group(1))
        p = self._get(pdf, "pdf", "pdf", parent_id=r.document_id)
        text = content.pdf_body(p.content)
        notes = [] if len(text) > 200 else ["The PDF has no text layer (scanned image); see the PDF."]
        return content.Body(text, content._paragraphs(text), it.url, pdf, p.document_id, notes=notes)


class CdscoNsq(_Regulator):
    """One item per reporting month of the NSQ table (the drug-class-level monthly alert)."""
    doc_type = "cdsco_nsq"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for month, rows in nsq_parse(res.content).items():
            items.append(self._item(
                # one URL per month: the bot dedupes on URL across items (OpenCms ignores the query)
                ext_id=f"nsq:{month}", url=f"{CDSCO_NSQ_PAGE}?month={month}",
                title=f"CDSCO Not of Standard Quality (NSQ) drug alert for {month}", when=None, raw=month, res=res,
                scope=(EOD, "CDSCO monthly NSQ drug alert"), kind="NSQ drug alert", date_only=False,
                month=month, samples=len(rows), listing_url=res.url))
        return items

    def digest_body(self, it: RawItem) -> content.Body:
        # expect "any": the JSON carries cp1252 bytes, so the fetcher's strict UTF-8 JSON check rejects it
        r = self._get(it.meta.get("listing_url") or self.cfg.urls["nsq"], "any", "nsq")
        month = it.meta.get("month") or it.ext_id.split(":", 1)[1]
        rows = nsq_parse(r.content).get(month)
        if not rows:
            text = f"The NSQ table no longer shows {month}; it shows the latest reporting month only."
            return content.Body(text, [], CDSCO_NSQ_PAGE, None, r.document_id, notes=[text])
        text = nsq_text(month, rows)
        return content.Body(text, content._paragraphs(text), CDSCO_NSQ_PAGE, None, r.document_id)


# ---- NPCI (disabled) -------------------------------------------------------------------------------------
class Npci(_Regulator):
    """NPCI UPI circulars / monthly statistics. Every path 403s non-browser clients (Akamai + F5 TSPD; Phase 0
    and again 2026-11-26), so the source is registered `enabled: false` (F-05) and this adapter refuses to run."""
    doc_type = "npci"

    def poll(self) -> list[RawItem]:
        raise RuntimeError("NPCI blocks non-browser clients (F-05); source is manual / disabled")

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        return []
