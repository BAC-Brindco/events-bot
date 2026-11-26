"""PRS Legislative Research bill tracker (Tier 3, weekly digest).

PRS has no feed (Phase 0: /rss.xml and /feed 404). https://prsindia.org/billtrack is one server-rendered
page listing every tracked bill, newest first (977 rows on 2026-11-26), each with its current status
(Passed / Pending / In Committee / Draft / Rules / Ordinance: In force / Lapsed / Withdrawn ...). No dates.

One item per (bill, status): a new bill, or a bill whose status changes (Pending -> In Committee -> Passed),
appears as a new item. The first poll is the silent baseline, so later polls surface only movements.
The per-bill page (https://prsindia.org/billtrack/<slug>) carries the ministry and a dated stage timeline
(Drupal field collection `field-own-status` / `-title` / `-date`); `enrich` reads it for new items, so
the item is stamped with the stage date and can be kept on its ministry.

URL note: the shared dedupe (core/dedupe.py) matches items by normalised URL within the same source too,
so a status change on the same bill page would be marked a duplicate of the earlier status. The item URL
therefore carries the status as a query string (`?status=passed`), which the Drupal page ignores.

Desk scope: Tier 3 -> 'weekly' for bills with economic relevance (finance, tax, banking, insurance,
securities, trade, labour codes, energy, telecom, data protection ...) at a live legislative stage;
everything else 'drop'.
"""
from __future__ import annotations

import re
from datetime import datetime, time
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST
from ...digest import content

BASE = "https://prsindia.org"
_ROW = re.compile(r'<div class="views-row">.*?href="(/billtrack/[^"]+)">(.*?)</a>.*?'
                  r'views-field-field-bill-status">\s*<span\s+class="[^"]*">(.*?)</span>', re.S)

# Statuses that are a live legislative stage. Lapsed / Replaced by an Act / Model Act / Infructuous: drop.
KIND = {
    "Pending": "Bill introduced (pending)",
    "Introduced": "Bill introduced",
    "In Committee": "Bill referred to committee",
    "Report": "Committee report on bill",
    "Passed": "Bill passed",
    "Draft": "Draft bill",
    "Ordinance: In force": "Ordinance promulgated",
    "Repromulgated": "Ordinance repromulgated",
    "Withdrawn": "Bill withdrawn",
    "Negatived": "Bill negatived",
    "Introduced-Negatived": "Bill negatived",
    "Rules": "Rules notified",
}

ECON = re.compile(
    r"financ|\btax|income-tax|goods and services|\bGST\b|customs|excise|\bcess\b|appropriation|budget|fiscal|"
    r"\bbank|banking|insolvency|bankruptcy|securities|"
    r"\bSEBI\b|stock exchange|depositor|deposit|insurance|\bbima\b|pension|provident fund|"
    r"payment|currency|coinage|reserve bank|foreign exchange|\bFEMA\b|negotiable instruments|"
    r"\btrade\b|tariff|\bexports?\b|\bimports?\b|commerce|competition|compan(?:y|ies)|corporate laws?|limited liability|"
    r"micro, small|\bMSME|industrial relations|\bwages?\b|social security|occupational safety|labour code|"
    r"electricity|energy|nuclear|\bpower\b|\bcoal|mines and minerals|\bmineral|oilfields|petroleum|natural gas|"
    r"telecommunication|telecom|data protection|digital personal data|special economic zone|"
    r"jan vishwas|warehousing|stamp|bankers", re.I)

ECON_MINISTRIES = re.compile(
    r"^(?:Finance|Corporate Affairs|Commerce and Industry|Labour and Employment|Power|"
    r"Petroleum and Natural Gas|Coal|Mines|New and Renewable Energy|Communications|"
    r"Electronics and Information Technology|Micro,? Small and Medium Enterprises)\b", re.I)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", HTMLParser(f"<p>{s}</p>").body.text()).strip()


def parse_listing(data: bytes) -> list[dict]:
    html = data.decode("utf-8", "replace")
    out, seen = [], set()
    for href, title, status in _ROW.findall(html):
        slug = href.rsplit("/", 1)[-1]
        status = _clean(status)
        if not slug or (slug, status) in seen:
            continue
        seen.add((slug, status))
        out.append({"slug": slug, "title": _clean(title), "status": status})
    return out


def scope(title: str, status: str, ministry: str = "") -> tuple[str, str]:
    if status not in KIND:
        return "drop", f"PRS: status '{status or 'none'}' is not a live legislative stage"
    if ECON.search(title):
        return "weekly", f"PRS bill tracker: {KIND[status].lower()} (economic)"
    if ministry and ECON_MINISTRIES.search(ministry):
        return "weekly", f"PRS bill tracker: {KIND[status].lower()} (Ministry of {ministry})"
    return "drop", "PRS: bill outside economic scope"


def parse_bill(data: bytes) -> dict:
    """Per-bill page: ministry and the dated stage timeline, oldest first."""
    tree = HTMLParser(data.decode("utf-8", "replace"))
    m = tree.css_first(".field-name-field-ministry .field-item")
    stages = []
    for n in tree.css(".field-collection-item-field-own-status-details"):
        def f(sel: str) -> str:
            x = n.css_first(sel)
            return re.sub(r"\s+", " ", x.text()).strip() if x else ""
        raw = f(".field-name-field-own-status-date .date-display-single")
        try:
            d = datetime.strptime(raw, "%b %d, %Y").date() if raw else None
        except ValueError:
            d = None
        stages.append({"status": f(".field-name-field-own-status .field-item"),
                       "where": f(".field-name-field-own-status-title .field-item"), "date": d, "date_raw": raw})
    return {"ministry": re.sub(r"\s+", " ", m.text()).strip() if m else "", "stages": stages}


def _status_slug(status: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", status.lower()).strip("-") or "none"


class Prs(SourceAdapter):
    doc_type = "prs"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_listing(res.content):
            hint, reason = scope(r["title"], r["status"])
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"{r['slug']}|{r['status']}",
                url=f"{BASE}/billtrack/{r['slug']}?status={_status_slug(r['status'])}", title=r["title"],
                document_id=res.document_id,
                meta={"kind": KIND.get(r["status"], r["status"] or "Bill"), "status": r["status"],
                      "slug": r["slug"], "route_hint": hint, "scope_reason": reason}))
        return items

    def _page_url(self, it: RawItem) -> str:
        p = urlsplit(it.url)
        return f"{p.scheme}://{p.netloc}{p.path}"

    def enrich(self, it: RawItem) -> RawItem:
        """New (non-baseline) items only: ministry + the stage timeline; the latest stage dates the item."""
        res = self.ctx.fetcher.get(self._page_url(it), source_id=self.cfg.id, doc_type="prs:bill", expect="html",
                                   conditional=False, parent_id=it.document_id)
        b = parse_bill(res.content)
        hint, reason = scope(it.title, it.meta.get("status", ""), b["ministry"])
        dated = [s for s in b["stages"] if s["date"]]
        last = dated[-1] if dated else None
        meta = {**it.meta, "ministry": b["ministry"], "route_hint": hint, "scope_reason": reason,
                "stages": [{k: s[k] for k in ("status", "where", "date_raw")} for s in b["stages"]],
                "bill_document_id": res.document_id, "date_only": True}
        if last:
            meta["stage"] = f"{last['status']}, {last['where']}".strip(", ")
        upd = {"meta": meta}
        if last:
            upd.update(source_published_at=datetime.combine(last["date"], time(0, 0), tzinfo=IST),
                       published_raw=last["date_raw"])
        return it.model_copy(update=upd)

    def digest_body(self, it: RawItem) -> content.Body:
        """The bill page: PRS's stage timeline is in the item meta; the body is PRS's bill summary."""
        url = self._page_url(it)
        r = self.ctx.fetcher.get_with_retry(url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:prs_bill", expect="html", conditional=False)
        text = content.html_body(r.content, ".field-name-body")
        return content.Body(text, content._paragraphs(text), url, None, r.document_id)
