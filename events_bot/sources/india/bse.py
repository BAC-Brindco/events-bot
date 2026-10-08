"""BSE notices (exchange circulars): index changes, buybacks, delistings, suspensions, equity corporate actions.

  GET https://api.bseindia.com/BseIndiaAPI/api/getCurrPreNextNoticesData_New/w?flag=          today
  GET ...getCurrPreNextNoticesData_New/w?flag=YYYYMMDD                                         that day

Akamai returns 403 to httpx; curl_cffi with a Chrome fingerprint gets 200 JSON (Phase 0, F-06). The
response is {"Table": [...]} with Notice_no ("20261007-49", sequential per day), Subject, category_name,
Dept_Name, Segment_Name, dt_tm (date only) and FileName (the PDF; plain httpx can fetch those).
Each poll reads today and yesterday (IST), so notices posted late in the evening are not lost at midnight.

About 40-50 notices a day, most of them routine (SME listings, group changes, price bands, debt and
mutual-fund notices). The keyword file keeps the market-moving ones; the segment, category and department
are put in the item summary so the excludes can match them and every rejection is logged.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST, utcnow

API = "https://api.bseindia.com/BseIndiaAPI/api/getCurrPreNextNoticesData_New/w?flag={}"
HEADERS = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
           "Accept": "application/json, text/plain, */*"}
PAGE = "https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx?page={}"


def parse_notices(content: bytes) -> list[dict]:
    payload = json.loads(content)
    rows = payload.get("Table") if isinstance(payload, dict) else None
    out = []
    for r in rows or []:
        no = str(r.get("Notice_no") or "").strip()
        subject = re.sub(r"\s+", " ", str(r.get("Subject") or "")).strip()
        if not re.fullmatch(r"\d{8}-\d+", no) or not subject:
            continue
        try:
            d = datetime.strptime(str(r.get("dt_tm") or r.get("Notice_Date"))[:10], "%Y-%m-%d").date()
        except ValueError:
            d = None
        out.append({"no": no, "subject": subject, "date": d, "raw_date": r.get("dt_tm"),
                    "category": (r.get("category_name") or "").strip(), "dept": (r.get("Dept_Name") or "").strip(),
                    "segment": (r.get("Segment_Name") or "").strip(),
                    "pdf": (r.get("FileName") or "").strip() or None,
                    "scrips": [s for s in str(r.get("Scrip_cd") or "").split(";") if s.strip()]})
    return out


class BseNotices(SourceAdapter):
    doc_type = "bse"

    def poll(self) -> list[RawItem]:
        today = utcnow().astimezone(IST).date()
        items: list[RawItem] = []
        for name, flag in (("today", ""), ("yesterday", (today - timedelta(days=1)).strftime("%Y%m%d"))):
            res = self.ctx.fetcher.get(API.format(flag), source_id=self.cfg.id, doc_type=f"bse:notices_{name}",
                                       expect="json", impersonate=True, headers=HEADERS, conditional=False)
            items += self.parse(res, name)
        return items

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in parse_notices(res.content):
            tags = ["bse:index"] if r["category"] == "Index" else []
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"notice:{r['no']}", url=r["pdf"] or PAGE.format(r["no"]),
                title=r["subject"],
                summary=f"Notice {r['no']} · Segment: {r['segment']} · Category: {r['category']} · Dept: {r['dept']}",
                source_published_at=datetime.combine(r["date"], time(0, 0), tzinfo=IST) if r["date"] else None,
                published_raw=str(r["raw_date"] or "")[:10] or None, document_id=res.document_id,
                meta={"kind": "Notice", "segment": r["segment"], "category": r["category"], "dept": r["dept"],
                      "scrip_codes": r["scrips"], "date_only": True, "priority_tags": tags}))
        return items
