"""Office of the Economic Adviser (DPIIT): WPI (14th, 12:00 IST) and Eight Core Industries (~20th, 17:00).

The homepage carries "Latest ... Press Release for the Month of ..." links to the current PDFs.
The WPI file name follows the data month (press_release_YYYYMM.pdf) and the ICI file name the release
date (Press_Release_ICI_YYYYMMDD.pdf, shifts with holidays), so both are discovered from the homepage
rather than constructed. A new link = a new release; the PDF is fetched only then.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ...core.models import FetchResult, Message, RawItem
from ...core.registry import SourceAdapter
from ...extract import oea as ox
from ...extract.validator import validate_all
from ...stage1 import mospi as sm
from ...stage1.render import Doc

BASE = "https://eaindustry.nic.in/"


def kind_of(href: str) -> str | None:
    if re.search(r"press_release/press_release_\d{6}\.pdf$", href):
        return "wpi"
    if re.search(r"Press_Release_ICI_\d{8}\.pdf$", href, re.I):
        return "ici"
    return None


def parse_home(content: bytes) -> list[dict]:
    tree = HTMLParser(content.decode("utf-8", "replace"))
    out, seen = [], set()
    for a in tree.css("a"):
        href, text = a.attributes.get("href") or "", re.sub(r"\s+", " ", a.text(strip=True))
        if not href.lower().endswith(".pdf") or not text.startswith("Latest") or href in seen:
            continue
        seen.add(href)
        out.append({"url": urljoin(BASE, href), "title": text, "kind": kind_of(href)})
    return out


class Oea(SourceAdapter):
    doc_type = "oea"

    def poll(self) -> list[RawItem]:
        from .data_prints import held_kinds
        held = held_kinds(self.ctx.db)          # files can appear before the release time (T-09)
        return [i for i in super().poll() if i.meta.get("kind") not in held]

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        return [RawItem(source_id=self.cfg.id, ext_id=f"file:{r['url'].rsplit('/', 1)[-1]}", url=r["url"],
                        title=r["title"], document_id=res.document_id,
                        meta={"kind": r["kind"], "priority_tags": [f"data:{r['kind']}"] if r["kind"] else []})
                for r in parse_home(res.content)]

    def render_stage1(self, it: RawItem, *, first_seen: datetime, mode: str) -> Message | None:
        kind = (it.meta or {}).get("kind")
        if kind not in ("wpi", "ici"):
            return None
        res = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=5, source_id=self.cfg.id,
                                              doc_type=f"oea:{kind}_pdf", expect="pdf", conditional=False,
                                              parent_id=it.document_id)
        texts, ex = (ox.parse_wpi if kind == "wpi" else ox.parse_ici)(res.content)
        validate_all(ex, texts)
        row = self.ctx.db.latest_document(it.url)
        if row is not None:
            self.ctx.db.set_document_text(row["id"], texts.get("text", ""))
            self.ctx.db.replace_extractions(row["id"], ex)
        return sm.build(kind, it.ref, ex, texts, release_title=it.title, docs=[Doc("Release (PDF)", it.url)],
                        published=None, first_seen=first_seen, mode=mode)
