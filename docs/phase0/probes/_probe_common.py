"""Shared helpers for Phase 0 feed probes (India Tier 1).

fetch() is polite (>=1.1 s between requests to the same host), uses a real
browser UA, records status / content-type / validators, and saves the first
~50 KB of the body as evidence under docs/phase0/raw/india_t1/.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "india_t1"
RAW.mkdir(parents=True, exist_ok=True)
LOG = RAW / "_fetch_log.jsonl"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_last: dict[str, float] = {}
MIN_GAP = 1.1


def client(**kw) -> httpx.Client:
    return httpx.Client(headers=BASE_HEADERS, follow_redirects=True,
                        timeout=httpx.Timeout(30.0, connect=15.0), http2=False, **kw)


def _polite(host: str) -> None:
    gap = time.time() - _last.get(host, 0)
    if gap < MIN_GAP:
        time.sleep(MIN_GAP - gap)
    _last[host] = time.time()


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s)[:90].strip("_")


def fetch(c: httpx.Client, url: str, name: str | None = None, save: bool = True,
          headers: dict | None = None, keep: int = 50_000, verify_note: str = "") -> dict:
    host = urlparse(url).netloc
    _polite(host)
    rec = {"url": url, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    t0 = time.time()
    try:
        r = c.get(url, headers=headers)
        rec.update(status=r.status_code, final_url=str(r.url),
                   ctype=r.headers.get("content-type"), etag=r.headers.get("etag"),
                   last_modified=r.headers.get("last-modified"),
                   cache_control=r.headers.get("cache-control"),
                   server=r.headers.get("server"), bytes=len(r.content),
                   secs=round(time.time() - t0, 2))
        rec["_resp"] = r
        if save:
            ext = ".pdf" if "pdf" in (rec["ctype"] or "") else (
                ".json" if "json" in (rec["ctype"] or "") else (
                    ".xml" if "xml" in (rec["ctype"] or "") or "rss" in (rec["ctype"] or "") else ".html"))
            fn = RAW / f"{name or slug(url)}{ext}"
            fn.write_bytes(r.content[:keep])
            rec["saved"] = fn.name
    except Exception as e:  # noqa: BLE001
        rec.update(status=None, error=f"{type(e).__name__}: {e}"[:300],
                   secs=round(time.time() - t0, 2))
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: v for k, v in rec.items() if k != "_resp"}) + "\n")
    return rec


def cond_check(c: httpx.Client, rec: dict) -> str:
    """Re-request with If-None-Match / If-Modified-Since; report whether 304 is honoured."""
    h = {}
    if rec.get("etag"):
        h["If-None-Match"] = rec["etag"]
    if rec.get("last_modified"):
        h["If-Modified-Since"] = rec["last_modified"]
    if not h:
        return "no validators"
    _polite(urlparse(rec["url"]).netloc)
    try:
        r = c.get(rec["url"], headers=h)
        return f"conditional GET -> {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return f"conditional GET error {type(e).__name__}"


def show(rec: dict) -> None:
    print({k: v for k, v in rec.items() if k != "_resp"})
