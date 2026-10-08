"""CBIC tax information portal: latest GST, Customs and Central Excise notifications and circulars.

taxinformation.cbic.gov.in is an Angular (JHipster) SPA. Its home page "Latest Updates" tabs call a public
JSON endpoint per tax (taxIds from /api/cbic-tax-msts, verified 8 Oct 2026):

  GET /api/cbic-notification-msts/fetchUpdatesByTaxId/{taxId}    GST 1000001, Customs 1000002, Excise 1000003

Each returns the newest 4 notifications/circulars (id, updatedDate "30-Sep-2026", notificationNo,
notificationName, updateType, updateCategory). Four is shallow: on Budget day dozens of customs
notifications land at once and most will be missed here (FLAGS F-24); Budget-day notifications are on
indiabudget.gov.in and PIB. The list endpoints that page through a year (fetchNotificationByYearAndCategory)
return HTTP 500 for every parameter shape tried.

TLS: the host does not send its Sectigo OV R36 intermediate (T-10); core.fetch adds the shipped
intermediate for this host only.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST

API = "https://taxinformation.cbic.gov.in/api/cbic-notification-msts/fetchUpdatesByTaxId/{}"
TAXES = {"gst": 1000001, "customs": 1000002, "excise": 1000003}
URLS = {name: API.format(tid) for name, tid in TAXES.items()}
VIEW = "https://taxinformation.cbic.gov.in/view-pdf/{id}/ENG/{tab}"
LABEL = {"gst": "GST", "customs": "Customs", "excise": "Central Excise"}
# Rate-bearing categories: these move prices (duty, anti-dumping, safeguard, GST rate changes).
RATE = re.compile(r"\((?:Rate|ADD|CVD|SG)\)|(?<!Non )\bTariff\b|Anti Dumping|Countervailing|Safeguard|\bRate\b",
                  re.I)


def parse_updates(content: bytes) -> list[dict]:
    out = []
    for r in json.loads(content) or []:
        try:
            d = datetime.strptime(str(r.get("updatedDate")), "%d-%b-%Y").date()
        except ValueError:
            d = None
        name = re.sub(r"\s+", " ", str(r.get("notificationName") or "")).strip()
        no = re.sub(r"\s+", " ", str(r.get("notificationNo") or "")).strip()
        utype = str(r.get("updateType") or "")
        out.append({"id": r.get("id"), "date": d, "raw_date": r.get("updatedDate"), "no": no, "name": name,
                    "type": utype, "category": r.get("updateCategory") or "",
                    "title": f"{no}: {name}" if no else name})
    return [r for r in out if r["id"] and r["name"]]


class Cbic(SourceAdapter):
    doc_type = "cbic"

    def poll(self) -> list[RawItem]:
        # The host resets connections now and then (runner tick, 8 Oct 2026): retry before failing the poll.
        items: list[RawItem] = []
        for name, url in self.cfg.urls.items():
            res = self.ctx.fetcher.get_with_retry(url, tries=3, base_delay=3, source_id=self.cfg.id,
                                                  doc_type=f"cbic:{name}", expect="json")
            if not res.not_modified:
                items += self.parse(res, name)
        return items

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_updates(res.content):
            tab = "Circulars" if r["type"].lower().startswith("circular") else "Notifications"
            tags = [f"cbic:{url_name}"]
            if RATE.search(f"{r['no']} {r['category']}"):
                tags.append("cbic:rate")
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"{tab.lower()}:{r['id']}", url=VIEW.format(id=r["id"], tab=tab),
                title=r["title"],
                source_published_at=datetime.combine(r["date"], time(0, 0), tzinfo=IST) if r["date"] else None,
                published_raw=r["raw_date"], document_id=res.document_id,
                meta={"kind": f"{LABEL[url_name]} {r['type'].lower() or 'update'}",
                      "category": r["category"], "date_only": True, "feed": url_name, "priority_tags": tags}))
        return items
