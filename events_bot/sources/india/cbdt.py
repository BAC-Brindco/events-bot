"""CBDT (incometaxindia.gov.in): notifications, circulars and press releases.

The site is a Liferay SPA behind Akamai. httpx gets 403 "Access Denied"; curl_cffi with a Chrome
fingerprint gets through (FLAGS T-11, F-06). The listing pages render client-side from Liferay's search
API with a "blueprint" (found in the page's etds-* web-component bundles, 8 Oct 2026):

  POST /o/search/v1.0/search?page=1&pageSize=N&nestedFields=embedded&fields=...
    notifications   blueprint CIRCULAR_NOTIFICATION_BP_ERC, structure_id 36050, structure_key NOTIFICATION_KEY
    circulars       blueprint CIRCULAR_NOTIFICATION_BP_ERC, structure_id 36050, structure_key CIRCULAR_KEY
    press releases  blueprint MISCELLANEOUS_BP_ERC, structure_key PRESS_RELEASE

Results come newest first. Each item has the structured-content id (in itemURL), the verbatim title and
content fields: reportFile (the PDF path) and receivedDate ("2026-10-01 11:30", IST, when CBDT posted it).
The `accept: application/json` header matters: without it the API answers in XML.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST

BASE = "https://www.incometaxindia.gov.in"
SEARCH = (BASE + "/o/search/v1.0/search?page=1&pageSize={n}&restrictFields=actions%2Ccreator&nestedFields=embedded"
          "&fields=embedded.taxonomyCategoryBriefs,embedded.contentFields,itemURL,title,dateCreated,dateModified"
          "&search=")
HEADERS = {"accept": "application/json", "Accept-Language": "en-US", "Content-Type": "application/json",
           "Origin": BASE, "Referer": BASE + "/notifications"}
LISTINGS = {
    "notifications": ("CIRCULAR_NOTIFICATION_BP_ERC", {"structure_id": "36050", "structure_key": "NOTIFICATION_KEY"}),
    "circulars": ("CIRCULAR_NOTIFICATION_BP_ERC", {"structure_id": "36050", "structure_key": "CIRCULAR_KEY"}),
    "press": ("MISCELLANEOUS_BP_ERC", {"structure_key": "PRESS_RELEASE"}),
}
KIND = {"notifications": "Notification", "circulars": "Circular", "press": "Press release"}
PAGE_SIZE = 20


def requests(n: int = PAGE_SIZE) -> dict[str, tuple[str, dict]]:
    out = {}
    for name, (erc, attrs) in LISTINGS.items():
        body = {"attributes": {"search.empty.search": True,
                               "search.experiences.blueprint.external.reference.code": erc,
                               **{f"search.experiences.{k}": v for k, v in attrs.items()}}}
        out[name] = (SEARCH.format(n=n), body)
    return out


def _field(item: dict, name: str):
    for f in (item.get("embedded") or {}).get("contentFields") or []:
        if f.get("name") == name:
            v = f.get("contentFieldValue") or {}
            return v.get("data") or (v.get("document") or {}).get("contentUrl")
    return None


def parse_search(content: bytes) -> list[dict]:
    out = []
    for it in json.loads(content).get("items") or []:
        m = re.search(r"/structured-contents/(\d+)", it.get("itemURL") or "")
        title = re.sub(r"\s+", " ", it.get("title") or "").strip()
        if not m or not title:
            continue
        received = _field(it, "receivedDate")
        at = None
        if received:
            try:
                at = datetime.strptime(received.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            except ValueError:
                at = None
        pdf = _field(it, "reportFile")
        out.append({"id": int(m.group(1)), "title": title, "received": received, "at": at,
                    "url": (BASE + pdf) if pdf and pdf.startswith("/") else (pdf or f"{BASE}/notifications"),
                    "categories": [c.get("taxonomyCategoryName") for c in
                                   (it.get("embedded") or {}).get("taxonomyCategoryBriefs") or []]})
    return out


class Cbdt(SourceAdapter):
    doc_type = "cbdt"

    def poll(self) -> list[RawItem]:
        items: list[RawItem] = []
        for name, (url, body) in requests().items():
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"cbdt:{name}", expect="json",
                                       json_body=body, impersonate=True, headers=HEADERS)
            items += self.parse(res, name)
        return items

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        return [RawItem(
            source_id=self.cfg.id, ext_id=f"sc:{r['id']}", url=r["url"], title=r["title"],
            source_published_at=r["at"], published_raw=r["received"], document_id=res.document_id,
            meta={"kind": KIND[url_name], "feed": url_name, "priority_tags": [f"cbdt:{url_name}"]})
            for r in parse_search(res.content)]
