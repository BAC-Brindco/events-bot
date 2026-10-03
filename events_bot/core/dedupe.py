"""Cross-channel dedupe: the same announcement on several channels (e.g. a ministry
notice and its PIB echo) becomes one alert. Three rules, cheapest first:
URL (normalised), document hash, then title similarity within a time window.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .db import Database

TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|source$)", re.I)
TITLE_WINDOW = timedelta(hours=48)
TITLE_THRESHOLD = 0.92


def normalise_url(url: str) -> str:
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    q = sorted((k.lower(), v) for k, v in parse_qsl(p.query, keep_blank_values=True)
               if not TRACKING.match(k))
    # ASP.NET paths on .gov.in hosts are case-insensitive; lower them so /Scripts == /scripts.
    return urlunsplit(("https", host, path.lower(), urlencode(q), ""))


def normalise_title(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).lower()
    t = re.sub(r"[‐-―‘’“”'\"`]", " ", t)
    t = re.sub(r"[^\w%.]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


@dataclass
class DedupeResult:
    dup_of: int | None
    rule: str | None

    @property
    def is_dup(self) -> bool:
        return self.dup_of is not None


def _numbers(t: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", t))


def check(db: Database, *, item_id: int, source_id: str, url_norm: str, title_norm: str,
          first_seen_at: datetime, doc_sha: str | None = None) -> DedupeResult:
    for r in db.items_by_url_norm(url_norm, item_id):
        return DedupeResult(r["id"], "url")
    if doc_sha:
        for r in db.items_with_doc_sha(doc_sha, item_id):
            return DedupeResult(r["id"], "doc_hash")
    # Title rule is cross-channel only: within one source, ext_id already dedupes, and
    # recurring titles ("Money Market Operations as on <date>") differ only by numbers.
    # Short titles ("Press Release") are too generic to match on.
    if len(title_norm) >= 25:
        nums = _numbers(title_norm)
        best: tuple[float, int | None] = (0.0, None)
        for r in db.recent_titles(first_seen_at - TITLE_WINDOW, item_id, source_id):
            if _numbers(r["title_norm"]) != nums:
                continue
            s = title_similarity(title_norm, r["title_norm"])
            if s > best[0]:
                best = (s, r["id"])
        if best[0] >= TITLE_THRESHOLD:
            return DedupeResult(best[1], f"title:{best[0]:.2f}")
    return DedupeResult(None, None)
