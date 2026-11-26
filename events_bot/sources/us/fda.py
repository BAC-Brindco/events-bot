"""FDA: press releases, drug recalls by Indian firms (openFDA) and CDER warning letters to Indian facilities.

www.fda.gov sits behind Akamai bot detection: httpx gets a 302 to /apology_objects/abuse-detection-apology.html
(26 Nov 2026), so every www.fda.gov request goes through the Chrome-fingerprint client (impersonate=True).
api.fda.gov (openFDA) is a plain JSON API and takes httpx.

fda_press      press-release RSS (20 items, ETag). Kept: approvals (novel / first generic), generic-drug,
               drug-pricing, tariff / onshoring / import / foreign-inspection policy, anything naming India.
               -> us_morning_wrap; the rest dropped.
fda_recalls    openFDA drug enforcement, country:"India", newest 25 recall records. One item per recall event
               (event_id groups the per-product recall numbers) -> us_morning_wrap, tag fda:india. openFDA is
               refreshed weekly (meta.last_updated), so this is polled every 6 h.
fda_warning_letters
               the Warning Letters listing (server-rendered, newest 10 by posted date, ETag) plus ?page=1.
               The xlsx export named in docs/phase0/us_data.md (datatables-data?page&_format=xlsx) is a stale
               cached file: on 26 Nov 2026 its newest row was posted 04/28/2026, whatever the parameters, so it
               cannot show new letters. The listing has the same 7 columns (+ an empty Excerpt column) and links
               each letter. Health: the header must be exactly those columns (parse raises otherwise).
               CDER rows are kept; `enrich` reads the letter page and keeps only recipients in India
               (tag fda:india) -> us_morning_wrap; other offices and non-Indian recipients are dropped.
"""
from __future__ import annotations

import base64
import json
import re
from collections import OrderedDict
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import feedparser
from selectolax.parser import HTMLParser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...digest import content
from . import uspol_common as uc

FDA = "https://www.fda.gov"
PAGE_SELECTORS = ("[role=main]", "article", "main")


def _https(url: str) -> str:
    return re.sub(r"^http://", "https://", (url or "").strip())


def _openfda_headers(settings) -> dict:
    """openFDA takes the key as HTTP Basic user (keeps it out of the archived URL). Keyless: 1,000 req/day/IP."""
    keys = getattr(settings, "api_keys", {}) or {}
    key = keys.get("OPENFDA_API_KEY") or keys.get("API_DATA_GOV_KEY")
    if not key:
        return {}
    return {"Authorization": "Basic " + base64.b64encode(f"{key}:".encode()).decode()}


# ---- press releases ---------------------------------------------------------------------------------------
PRESS_KEEP = re.compile(
    r"\bApprov|\bGenerics?\b|\bANDA\b|\bBiosimilar|\bDrug Shortage|\bTariff|\bOnshor|\bPreCheck\b|\bImport|"
    r"\bForeign\b|\bMost.Favou?red|\bMFN\b|\bDrug Pric|\bManufactur|\bInspection|\bWarning Letter|\bRecall|"
    r"\bIndia\b|\bIndian\b|\bAPI\b|\bActive Pharmaceutical Ingredient", re.I)
PRESS_INDIA = re.compile(r"\bIndia\b|\bIndian\b", re.I)


def parse_press(content_bytes: bytes) -> list[dict]:
    out = []
    for e in feedparser.parse(content_bytes).entries:
        try:
            pub = parsedate_to_datetime(e.get("published")) if e.get("published") else None
        except (TypeError, ValueError):
            pub = None
        link = _https(e.get("link") or "")
        out.append({"title": re.sub(r"\s+", " ", e.get("title") or "").strip(), "url": link,
                    "slug": link.rstrip("/").rsplit("/", 1)[-1], "published": pub, "published_raw": e.get("published"),
                    "summary": re.sub(r"\s+", " ", e.get("summary") or "").strip()})
    return [r for r in out if r["title"] and r["url"]]


def classify_press(r: dict) -> tuple[str, str, list[str]]:
    hay = f"{r['title']} {r['summary']}"
    if PRESS_INDIA.search(hay):
        return "us_morning_wrap", "FDA action naming India / an Indian firm", ["fda:india"]
    m = PRESS_KEEP.search(r["title"])
    if m:
        return "us_morning_wrap", f"FDA approval / drug policy ('{m.group(0)}')", []
    return "drop", "FDA release outside the drug-policy scope", []


def _page(fetcher, url: str, source_id: str, doc_type: str, parent_id: int | None = None):
    return fetcher.get_with_retry(url, tries=3, base_delay=4, source_id=source_id, doc_type=doc_type, expect="html",
                                  conditional=False, impersonate=True, parent_id=parent_id)


class FdaPress(SourceAdapter):
    doc_type = "fda_press"

    def poll(self) -> list[RawItem]:
        items: list[RawItem] = []
        for name, url in self.cfg.urls.items():
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"fda_press:{name}", expect="xml",
                                       impersonate=True)
            if not res.not_modified:
                items += self.parse(res, name)
        return items

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_press(res.content):
            hint, reason, tags = classify_press(r)
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=r["slug"], url=r["url"], title=r["title"],
                source_published_at=r["published"], published_raw=r["published_raw"], document_id=res.document_id,
                summary=r["summary"] or None,
                meta={"kind": "Press release", "route_hint": hint, "scope_reason": reason, "priority_tags": tags}))
        return items

    def digest_body(self, it: RawItem) -> content.Body:
        r = _page(self.ctx.fetcher, it.url, self.cfg.id, "digest:fda_press_page", it.document_id)
        return uc.body(uc.html_body_first(r.content, PAGE_SELECTORS), it.url, r.document_id)


# ---- openFDA drug enforcement (recalls) ---------------------------------------------------------------------
EVENT_URL = "https://api.fda.gov/drug/enforcement.json?search=event_id:%22{id}%22&limit=100"


def parse_enforcement(content_bytes: bytes) -> list[dict]:
    """Recall events (grouped by event_id, in feed order) from an openFDA enforcement response."""
    d = json.loads(content_bytes)
    events: "OrderedDict[str, dict]" = OrderedDict()
    for r in d.get("results") or []:
        eid = str(r.get("event_id") or "")
        if not eid:
            continue
        ev = events.setdefault(eid, {"event_id": eid, "firm": (r.get("recalling_firm") or "").strip(),
                                     "city": (r.get("city") or "").strip(), "country": (r.get("country") or "").strip(),
                                     "classification": r.get("classification") or "", "status": r.get("status") or "",
                                     "report_date": r.get("report_date") or "", "reasons": [], "recalls": [],
                                     "products": []})
        ev["recalls"].append(r.get("recall_number") or "")
        reason = re.sub(r"\s+", " ", r.get("reason_for_recall") or "").strip()
        if reason and reason not in ev["reasons"]:
            ev["reasons"].append(reason)
        names = (r.get("openfda") or {}).get("generic_name") or []
        prod = names[0].title() if names else (r.get("product_description") or "").split(",")[0].strip()
        if prod and prod not in ev["products"]:
            ev["products"].append(prod)
        ev["report_date"] = max(ev["report_date"], r.get("report_date") or "")
    for ev in events.values():
        try:
            ev["date"] = datetime.strptime(ev["report_date"], "%Y%m%d").date()
        except ValueError:
            ev["date"] = None
    return list(events.values())


def recall_title(ev: dict) -> str:
    n = len(ev["recalls"])
    prods = ", ".join(ev["products"][:3]) + (f" and {len(ev['products']) - 3} more" if len(ev["products"]) > 3 else "")
    where = ", ".join(x for x in (ev["city"], ev["country"]) if x)
    reason = ev["reasons"][0] if ev["reasons"] else "reason not stated"
    reason = reason if len(reason) <= 160 else reason[:160].rsplit(" ", 1)[0] + " ..."
    return (f"{ev['firm']} ({where}): {ev['classification']} drug recall, {n} product{'s' if n != 1 else ''}"
            f"{' (' + prods + ')' if prods else ''}: {reason}")


class FdaRecalls(SourceAdapter):
    doc_type = "fda_recalls"

    def poll(self) -> list[RawItem]:
        items: list[RawItem] = []
        for name, url in self.cfg.urls.items():
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"fda_recalls:{name}", expect="json",
                                       headers=_openfda_headers(self.ctx.settings))
            if not res.not_modified:
                items += self.parse(res, name)
        return items

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for ev in parse_enforcement(res.content):
            india = ev["country"].lower() == "india"
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"event:{ev['event_id']}", url=EVENT_URL.format(id=ev["event_id"]),
                title=recall_title(ev), source_published_at=uc.et_midnight(ev["date"]),
                published_raw=ev["report_date"], document_id=res.document_id,
                meta={"kind": "Drug recall", "date_only": True, "event_id": ev["event_id"],
                      "classification": ev["classification"], "recall_numbers": ev["recalls"], "firm": ev["firm"],
                      "route_hint": "us_morning_wrap" if india else "drop",
                      "scope_reason": "FDA drug recall by an Indian facility" if india else "recall outside India",
                      "priority_tags": ["fda:india"] if india else []}))
        return items

    @staticmethod
    def event_text(content_bytes: bytes) -> str:
        """Pure: one block of lines per recalled product, from the event's openFDA records."""
        lines: list[str] = []
        for r in (json.loads(content_bytes) or {}).get("results") or []:
            def f(k):
                return re.sub(r"\s+", " ", str(r.get(k) or "")).strip()
            lines.append(f"Recall {f('recall_number')} ({f('classification')}, {f('status')}), reported "
                         f"{f('report_date')}: {f('product_description')}")
            lines.append(f"Reason for recall: {f('reason_for_recall')}")
            lines.append(f"Quantity: {f('product_quantity')}. Distribution: {f('distribution_pattern')}")
            lines.append(f"Recalling firm: {', '.join(x for x in (f('recalling_firm'), f('address_1'), f('address_2'), f('city'), f('country')) if x)}"
                         f". {f('voluntary_mandated')}.")
        return "\n".join(lines)

    def digest_body(self, it: RawItem) -> content.Body:
        r = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:openfda_event", expect="json", conditional=False,
                                            headers=_openfda_headers(self.ctx.settings))
        return uc.body(self.event_text(r.content), it.url, r.document_id)


# ---- warning letters ----------------------------------------------------------------------------------------
WL_COLUMNS = ["Posted Date", "Letter Issue Date", "Company Name", "Issuing Office", "Subject", "Response Letter",
              "Closeout Letter"]
CDER = re.compile(r"Center for Drug Evaluation and Research|\bCDER\b", re.I)


def _date(s: str):
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def parse_wl_listing(content_bytes: bytes) -> list[dict]:
    tree = HTMLParser(content_bytes.decode("utf-8", "replace"))
    table = next((t for t in tree.css("table") if "Posted Date" in (t.text() or "")), None)
    if table is None:
        raise ValueError("warning-letter table not found")
    header = [re.sub(r"\s+", " ", th.text()).strip() for th in table.css("thead th")]
    if header[:7] != WL_COLUMNS:
        raise ValueError(f"warning-letter columns changed: {header}")
    out = []
    for tr in table.css("tbody tr"):
        tds = tr.css("td")
        if len(tds) < 7:
            continue
        a = tds[2].css_first("a[href]")
        if a is None:
            continue
        href = a.attributes.get("href") or ""
        cell = [re.sub(r"\s+", " ", td.text()).strip() for td in tds]
        out.append({"posted": _date(cell[0]), "posted_raw": cell[0], "issued": _date(cell[1]), "issued_raw": cell[1],
                    "company": cell[2], "office": cell[3], "subject": cell[4], "url": urljoin(FDA, href),
                    "slug": href.rstrip("/").rsplit("/", 1)[-1]})
    return out


def letter_recipient(page: bytes) -> dict:
    """Recipient block of a warning-letter page: company and address country (the first address on the page
    is the recipient's; the issuing office's follows)."""
    tree = HTMLParser(page.decode("utf-8", "replace"))
    addr = tree.css_first("p.address")
    country = ""
    if addr is not None:
        c = addr.css_first(".country")
        country = c.text(strip=True) if c is not None else ""
    lines = [re.sub(r"\s+", " ", s.text(separator=" ")).strip() for s in (addr.css("span") if addr else [])]
    return {"country": country, "address": ", ".join(x for x in lines if x)}


class FdaWarningLetters(SourceAdapter):
    doc_type = "fda_wl"

    def poll(self) -> list[RawItem]:
        items: list[RawItem] = []
        for name, url in self.cfg.urls.items():
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"fda_wl:{name}", expect="html",
                                       impersonate=True)
            if not res.not_modified:
                items += self.parse(res, name)
        seen, out = set(), []
        for it in items:                     # the two pages overlap
            if it.ext_id not in seen:
                seen.add(it.ext_id)
                out.append(it)
        return out

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_wl_listing(res.content):
            cder = bool(CDER.search(r["office"]))
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=r["slug"], url=r["url"],
                title=f"FDA warning letter to {r['company']}: {r['subject']}",
                source_published_at=uc.et_midnight(r["posted"]), published_raw=r["posted_raw"],
                document_id=res.document_id,
                meta={"kind": "Warning letter", "date_only": True, "company": r["company"], "office": r["office"],
                      "subject": r["subject"], "issued": r["issued_raw"],
                      "route_hint": "us_morning_wrap" if cder else "drop",
                      "scope_reason": "CDER warning letter; recipient country not verified" if cder
                                      else f"warning letter from {r['office'] or 'a non-drug office'}",
                      "priority_tags": []}))
        return items

    @staticmethod
    def apply_letter(it: RawItem, page: bytes, document_id: int | None = None) -> RawItem:
        """Pure: keep a CDER letter only when its recipient is in India."""
        if it.meta.get("route_hint") == "drop":
            return it
        rec = letter_recipient(page)
        meta = {**it.meta, "recipient_country": rec["country"], "recipient_address": rec["address"],
                "letter_document_id": document_id}
        if rec["country"].lower() == "india":
            meta.update(route_hint="us_morning_wrap", scope_reason="CDER warning letter to an Indian facility",
                        priority_tags=sorted(set(meta.get("priority_tags") or []) | {"fda:india"}))
        elif rec["country"]:
            meta.update(route_hint="drop", scope_reason=f"CDER warning letter, recipient in {rec['country']}")
        else:
            meta.update(scope_reason="CDER warning letter; recipient country not stated on the letter page")
        return it.model_copy(update={"meta": meta})

    def enrich(self, it: RawItem) -> RawItem:
        if it.meta.get("route_hint") == "drop":
            return it                                   # non-CDER: no letter fetch
        r = _page(self.ctx.fetcher, it.url, self.cfg.id, "fda_wl:letter", it.document_id)
        return self.apply_letter(it, r.content, r.document_id)

    def digest_body(self, it: RawItem) -> content.Body:
        r = _page(self.ctx.fetcher, it.url, self.cfg.id, "digest:fda_wl_letter", it.document_id)
        return uc.body(uc.html_body_first(r.content, PAGE_SELECTORS), it.url, r.document_id)


