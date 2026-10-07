"""MoSPI / NSO releases: CPI, IIP, GDP (with extracted numbers) and every other NSO release.

mospi.gov.in is a React SPA (every HTML route is the same shell). The /latest-releases page calls
a POST-only JSON API (GET is 403), approved as a monitored poller in Phase 0 (FLAGS F-04):
  POST https://www.mospi.gov.in/api/latest-release/get-web-latest-release-list
Each row has id, title, published_year (date), and file_one.path -> the English release PDF.
CPI / IIP / GDP rows get the PDF fetched, numbers extracted + validated, and a data-print alert;
other NSO releases (PLFS, ASI, ISP, surveys) go out as normal release alerts.
PIB echoes many of these too; the cross-source title rule dedupes them.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, time

from ...core.models import FetchResult, Message, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST
from ...extract import mospi as mx
from ...extract.validator import validate_all
from ...stage1 import mospi as sm
from ...stage1.render import Doc

BASE = "https://www.mospi.gov.in/"
BODY = {"page_no": 1, "page_size": 30, "sort_field": "published_year", "sort_order": "DESC", "lang": "en",
        "data_source": "web"}
PARSERS = {"cpi": mx.parse_cpi, "iip": mx.parse_iip, "gdp": mx.parse_gdp}


def rows_of(payload) -> list[dict]:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    return data if isinstance(data, list) else []


def pdf_path(row: dict) -> str | None:
    f = row.get("file_one")
    if isinstance(f, str):
        try:
            f = ast.literal_eval(f)
        except (ValueError, SyntaxError):
            m = re.search(r"'path':\s*'([^']+)'", f)
            return m.group(1) if m else None
    return f.get("path") if isinstance(f, dict) else None


class Mospi(SourceAdapter):
    doc_type = "mospi"

    def poll(self) -> list[RawItem]:
        res = self.ctx.fetcher.get(self.cfg.urls["releases"], source_id=self.cfg.id, doc_type="mospi:releases",
                                   expect="json", json_body=BODY,
                                   headers={"Origin": "https://www.mospi.gov.in",
                                            "Referer": "https://www.mospi.gov.in/latest-releases"})
        return self.parse(res, "releases")

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        import json
        items = []
        for r in rows_of(json.loads(res.content)):
            title = re.sub(r"\s+", " ", str(r.get("title") or "")).strip()
            path = pdf_path(r)
            if not title or not path:
                continue
            d = None
            try:
                d = datetime.strptime(str(r.get("published_year"))[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
            kind = mx.kind_of(title)
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"id:{r.get('id')}", url=BASE + path.lstrip("/"), title=title,
                source_published_at=datetime.combine(d, time(0, 0), tzinfo=IST) if d else None,
                published_raw=str(r.get("published_year") or "")[:10] or None, document_id=res.document_id,
                meta={"kind": kind, "date_only": True, "priority_tags": [f"data:{kind}"] if kind else []}))
        return items

    def render_stage1(self, it: RawItem, *, first_seen: datetime, mode: str) -> Message | None:
        """Data prints: fetch the PDF, extract + validate, build the print-vs-prior alert."""
        kind = (it.meta or {}).get("kind")
        if kind not in PARSERS:
            return None
        res = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=5, source_id=self.cfg.id,
                                              doc_type=f"mospi:{kind}_pdf", expect="pdf", conditional=False,
                                              parent_id=it.document_id)
        texts, ex = PARSERS[kind](res.content)
        validate_all(ex, texts)
        row = self.ctx.db.latest_document(it.url)
        if row is not None:
            self.ctx.db.set_document_text(row["id"], next(iter(texts.values()), ""))
            self.ctx.db.replace_extractions(row["id"], ex)
        return sm.build(kind, it.ref, ex, texts, release_title=it.title, docs=[Doc("Release (PDF)", it.url)],
                        published=it.source_published_at, first_seen=first_seen, mode=mode)
