"""Congress: enacted laws (Congress.gov API v3) and the federal operating status (OPM). Section "Congress & fiscal".

Congress.gov API  https://api.congress.gov/v3/law/{congress}?format=json&limit=250
  The key goes in the X-Api-Key header (api.data.gov accepts it there; verified 26 Nov 2026), never in the URL,
  so it is not written to http_cache / documents. Key: CONGRESS_API_KEY, else API_DATA_GOV_KEY, else DEMO_KEY.
  DEMO_KEY allows 10 requests an hour per IP (x-ratelimit-limit: 10), hence tick_every_minutes: 60.
  Titles come back mojibaked ("Veteransâ€™"); they are repaired (cp1252 -> UTF-8).
  Kept: appropriations, continuing resolutions, debt limit, reconciliation, tax, tariff / trade / customs,
  sanctions laws -> us_morning_wrap. Dropped: naming / land / medal / CRA-disapproval / everything else.

OPM operating status  https://www.opm.gov/json/operatingstatus.json
  One JSON object; an item only when StatusSummary changes (compared with the last stored item, in poll()).
  A status that speaks of a lapse in appropriations / shutdown -> realtime; the reopening after one ->
  us_morning_wrap; weather / local changes (delayed arrival, early dismissal, snow closures) -> drop.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import utcnow
from ...digest import content
from . import uspol_common as uc

API = "https://api.congress.gov/v3/law/{congress}?format=json&limit=250"
WEB_TYPE = {"HR": "house-bill", "S": "senate-bill", "HJRES": "house-joint-resolution",
            "SJRES": "senate-joint-resolution", "HRES": "house-resolution", "SRES": "senate-resolution",
            "HCONRES": "house-concurrent-resolution", "SCONRES": "senate-concurrent-resolution"}

KEEP = re.compile(
    r"\bAppropriations?\b|\bContinuing\b|\bDebt Limit\b|\bdebt ceiling\b|\bpublic debt\b|\bstatutory limit\b|"
    r"\breconciliation\b|\bTax(?:es)?\b|\bTax Relief\b|\bTariffs?\b|\bTrade\b|\bCustoms\b|\bDuty\b|\bDuties\b|"
    r"\bRescissions?\b|\bSanction|\bExport Control|\bAGOA\b|\bBudget (?:Act|Resolution|Control)\b|\bShutdown\b",
    re.I)
DROP_FIRST = re.compile(
    r"congressional disapproval|\bdisapproving\b|\bMedal\b|\bPostal Service\b|\bpost office\b|\bdesignate\b|"
    r"\bredesignat|\bto name\b|\bconvey\b|\bLand\b|\bMemorial\b|\bNational (?:Park|Historic|Monument)", re.I)


def current_congress(now: datetime | None = None) -> int:
    now = now or utcnow()
    return (now.year - 1789) // 2 + 1


def api_key(settings) -> str:
    keys = getattr(settings, "api_keys", {}) or {}
    return keys.get("CONGRESS_API_KEY") or keys.get("API_DATA_GOV_KEY") or "DEMO_KEY"


def web_url(congress: int, bill_type: str, number: str) -> str:
    kind = WEB_TYPE.get(bill_type.upper(), bill_type.lower())
    return f"https://www.congress.gov/bill/{congress}th-congress/{kind}/{number}"


def parse_laws(content_bytes: bytes) -> list[dict]:
    d = json.loads(content_bytes)
    out = []
    for b in d.get("bills") or []:
        laws = b.get("laws") or []
        if not laws:
            continue
        law = laws[0]
        la = b.get("latestAction") or {}
        try:
            acted = datetime.strptime(la.get("actionDate") or "", "%Y-%m-%d").date()
        except ValueError:
            acted = None
        ltype = (law.get("type") or "Public Law").strip()
        out.append({"law": law.get("number"), "law_type": ltype, "congress": b.get("congress"),
                    "bill_type": b.get("type") or "", "bill_number": str(b.get("number") or ""),
                    "title": uc.fix_mojibake(re.sub(r"\s+", " ", b.get("title") or "")).strip(),
                    "acted": acted, "raw_date": la.get("actionDate"), "action": la.get("text") or "",
                    "api_url": b.get("url") or ""})
    return [r for r in out if r["law"] and r["title"]]


def classify_law(r: dict) -> tuple[str, str]:
    if not r["law_type"].lower().startswith("public"):
        return "drop", "private law"
    if DROP_FIRST.search(r["title"]):
        return "drop", "law outside fiscal scope (naming / land / honours / CRA disapproval)"
    m = KEEP.search(r["title"])
    if m:
        return "us_morning_wrap", f"enacted fiscal / trade law ('{m.group(0)}')"
    return "drop", "law outside fiscal scope"


class CongressLaws(SourceAdapter):
    doc_type = "congress"

    def urls(self) -> list[tuple[str, str]]:
        c = current_congress()
        out = [(f"law{c}", API.format(congress=c))]
        now = utcnow()
        if now.month == 1 and now.year % 2 == 1:       # laws of the outgoing Congress signed in early January
            out.append((f"law{c - 1}", API.format(congress=c - 1)))
        return out

    def poll(self) -> list[RawItem]:
        items: list[RawItem] = []
        for name, url in self.urls():
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"congress:{name}", expect="json",
                                       headers={"X-Api-Key": api_key(self.ctx.settings), "Accept": "application/json"})
            if not res.not_modified:
                items += self.parse(res, name)
        return items

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_laws(res.content):
            hint, reason = classify_law(r)
            prefix = "Public Law" if r["law_type"].lower().startswith("public") else r["law_type"]
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"{prefix.split()[0].lower()}:{r['law']}",
                url=web_url(r["congress"], r["bill_type"], r["bill_number"]),
                title=f"{prefix} {r['law']}: {r['title']}",
                source_published_at=uc.et_midnight(r["acted"]), published_raw=r["raw_date"],
                document_id=res.document_id,
                meta={"kind": "Enacted law", "date_only": True, "route_hint": hint, "scope_reason": reason,
                      "bill": f"{r['bill_type']} {r['bill_number']}", "api_url": r["api_url"],
                      "number": f"P.L. {r['law']}", "action": r["action"]}))
        return items

    @staticmethod
    def summary_text(content_bytes: bytes) -> str:
        """Pure: the newest CRS summary (HTML in JSON) as paragraph lines."""
        sums = (json.loads(content_bytes) or {}).get("summaries") or []
        if not sums:
            return ""
        latest = max(sums, key=lambda s: (s.get("updateDate") or "", s.get("actionDate") or ""))
        return content.html_body(f"<div id='crs'>{latest.get('text') or ''}</div>", "#crs")

    def digest_body(self, it: RawItem) -> content.Body:
        """CRS summary of the enacted bill (api .../bill/{c}/{type}/{n}/summaries). New laws often have none yet."""
        api_url = (it.meta or {}).get("api_url") or ""
        url = re.sub(r"\?.*$", "", api_url) + "/summaries?format=json"
        r = self.ctx.fetcher.get_with_retry(url, tries=2, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:congress_summary", expect="json", conditional=False,
                                            headers={"X-Api-Key": api_key(self.ctx.settings)})
        text = self.summary_text(r.content)
        notes = [] if text else ["The Congressional Research Service has not published a summary of this law yet."]
        return uc.body(text, it.url, r.document_id, notes=notes)


# ---- OPM operating status ----------------------------------------------------------------------------------
LAPSE = re.compile(r"lapse in (?:appropriations|funding)|\bshut ?down\b|\bfurlough", re.I)
_MS = re.compile(r"/Date\((\d+)\)/")


def parse_status(content_bytes: bytes) -> dict:
    d = json.loads(content_bytes)
    m = _MS.search(d.get("DateStatusPosted") or "")
    posted = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc) if m else None
    text = " ".join(str(d.get(k) or "") for k in ("StatusSummary", "ShortStatusMessage", "LongStatusMessage"))
    return {"summary": (d.get("StatusSummary") or "").strip(), "type": d.get("StatusType") or "",
            "short": (d.get("ShortStatusMessage") or "").strip(), "long": d.get("LongStatusMessage") or "",
            "applies_to": d.get("AppliesTo") or "", "posted": posted, "posted_raw": d.get("DateStatusPosted"),
            "url": d.get("StatusWebPage") or d.get("Url") or "https://www.opm.gov/status/",
            "lapse": bool(LAPSE.search(text))}


def classify_status(s: dict, previous: dict | None) -> tuple[str, str]:
    if s["lapse"]:
        return "realtime", "federal government shutdown: lapse in appropriations (OPM)"
    if previous and previous.get("lapse") and s["summary"].lower().startswith("open"):
        return "us_morning_wrap", "federal government reopened after a lapse in appropriations (OPM)"
    return "drop", f"OPM operating status change ({s['summary']}): weather / local, not fiscal"


class OpmStatus(SourceAdapter):
    doc_type = "opm"

    def _previous(self) -> dict | None:
        row = self.ctx.db.one("select meta from items where source_id = %s order by first_seen_at desc, id desc "
                              "limit 1", (self.cfg.id,))
        return (row or {}).get("meta") or None

    def poll(self) -> list[RawItem]:
        """An item only when StatusSummary differs from the last stored one (the JSON is re-posted daily)."""
        items: list[RawItem] = []
        for name, url in self.cfg.urls.items():
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"opm:{name}", expect="json")
            if res.not_modified:
                continue
            prev = self._previous()
            for it in self.parse(res, name, previous=prev):
                if prev is None or prev.get("status") != it.meta["status"]:
                    items.append(it)
        return items

    def parse(self, res: FetchResult, url_name: str, previous: dict | None = None) -> list[RawItem]:
        s = parse_status(res.content)
        if not s["summary"]:
            raise ValueError("OPM status JSON has no StatusSummary")
        hint, reason = classify_status(s, previous)
        posted_key = s["posted"].strftime("%Y%m%dT%H%M") if s["posted"] else (s["applies_to"] or "na")
        return [RawItem(
            source_id=self.cfg.id, ext_id=f"{s['summary']}:{posted_key}", url=s["url"],
            title=f"Federal operating status: {s['summary']} ({s['short']})" if s["short"] else
                  f"Federal operating status: {s['summary']}",
            source_published_at=s["posted"], published_raw=s["posted_raw"], document_id=res.document_id,
            summary=s["short"] or None,
            meta={"kind": "Operating status", "status": s["summary"], "lapse": s["lapse"],
                  "applies_to": s["applies_to"], "route_hint": hint, "scope_reason": reason,
                  "previous_status": (previous or {}).get("status"),
                  "priority_tags": ["opm:shutdown"] if s["lapse"] else []})]

    @staticmethod
    def status_text(content_bytes: bytes) -> str:
        s = parse_status(content_bytes)
        lines = [f"Status: {s['summary']}", f"Applies to: {s['applies_to']}", s["short"]]
        lines += [re.sub(r"\s+", " ", ln).strip() for ln in s["long"].replace("\r", "").split("\n")]
        return "\n".join(ln for ln in lines if ln)

    def digest_body(self, it: RawItem) -> content.Body:
        """The status JSON archived with the item (no refetch: today's status may already differ)."""
        doc_id = it.document_id
        raw = self.ctx.db.one("select storage_key from documents where id = %s", (doc_id,)) if doc_id else None
        try:
            data = self.ctx.archive.get(raw["storage_key"]) if raw else None
        except (OSError, KeyError, ValueError):
            data = None
        if data is None:
            r = self.ctx.fetcher.get(next(iter(self.cfg.urls.values())), source_id=self.cfg.id,
                                     doc_type="digest:opm", expect="json", conditional=False)
            data, doc_id = r.content, r.document_id
        return uc.body(self.status_text(data), it.url, doc_id)
