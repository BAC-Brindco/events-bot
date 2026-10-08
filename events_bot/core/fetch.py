"""HTTP fetching: conditional GET, per-host politeness, content validation, archive-before-parse.

Rules this module enforces for every adapter:
- TLS verification is always on. `truststore` supplies the OS trust store, which fixes
  the incomplete chains on several .gov.in hosts (F-06). Never `verify=False`.
- An HTTP 200 is not success until the body looks like what we asked for (FLAGS T-05):
  RBI's rbidocs and BLS can return 200 HTML bot pages in place of a PDF or feed.
- Every 200 response is archived and gets a `documents` row before anyone parses it.
"""
from __future__ import annotations

import json
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import certifi
import httpx
import structlog
import truststore

from .archive import Archive
from .db import Database
from .models import FetchResult
from .timeutil import utcnow

log = structlog.get_logger()

BLOCK_MARKERS = (b"Request Rejected", b"Please enable JavaScript", b"support ID",
                 b"Access Denied", b"cf-browser-verification", b"captcha")
KEEP_HEADERS = ("content-type", "etag", "last-modified", "date", "cache-control", "content-length", "server")


class FetchError(Exception):
    def __init__(self, url: str, kind: str, detail: str, status: int | None = None):
        super().__init__(f"{kind}: {detail} ({url})")
        self.url, self.kind, self.detail, self.status = url, kind, detail, status


def check_content(content: bytes, expect: str) -> str | None:
    """None if the body matches `expect`, else the reason it does not."""
    head = content[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if expect == "pdf":
        return None if content[:4] == b"%PDF" else "not a PDF (missing %PDF magic)"
    if expect == "xml":
        low = head[:300].lower()
        if not head.startswith(b"<") or low.startswith((b"<!doctype html", b"<html")):
            return "not XML"
        return None
    if expect == "json":
        try:
            json.loads(content)
            return None
        except ValueError:
            return "not JSON"
    if expect == "html":
        if any(m.lower() in head.lower() for m in BLOCK_MARKERS) and len(content) < 20_000:
            return "bot/block page"
        return None if b"<" in head else "not HTML"
    return None


CERTS = Path(__file__).parent / "certs"
# Hosts that do not send their intermediate certificate (FLAGS T-10). Windows fills the gap through AIA;
# OpenSSL on the Linux runners does not. For these hosts only, the missing intermediate (fetched from the
# issuer's AIA URL and shipped in core/certs) is added to a certifi-based store. Verification stays on.
HOST_INTERMEDIATES = {
    "www.cbic.gov.in": "sectigo_public_server_ca_ov_r36.pem",
    "taxinformation.cbic.gov.in": "sectigo_public_server_ca_ov_r36.pem",
}


class _Resp:
    """The parts of a response the fetcher needs, from httpx or curl_cffi alike."""

    def __init__(self, status: int, headers, content: bytes, url: str):
        self.status_code, self.content, self.url = status, content, url
        self.headers = {k.lower(): v for k, v in headers.items()}


class Fetcher:
    def __init__(self, db: Database | None, archive: Archive, user_agent: str,
                 min_gap: float = 1.1, timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        self.db, self.archive, self.min_gap, self.timeout = db, archive, min_gap, timeout
        self.transport = transport
        self._client_kw = dict(
            headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9",
                     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            follow_redirects=True, timeout=httpx.Timeout(timeout, connect=15.0), transport=transport)
        self.client = httpx.Client(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), **self._client_kw)
        self._pinned: dict[str, httpx.Client] = {}
        self._cffi = None
        self._host_locks: dict[str, threading.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._guard = threading.Lock()

    def _client_for(self, host: str) -> httpx.Client:
        name = HOST_INTERMEDIATES.get(host)
        if name is None:
            return self.client
        with self._guard:
            if name not in self._pinned:
                ctx = ssl.create_default_context(cafile=certifi.where())
                ctx.load_verify_locations(cafile=str(CERTS / name))
                self._pinned[name] = httpx.Client(verify=ctx, **self._client_kw)
            return self._pinned[name]

    def _impersonated(self, method: str, url: str, headers: dict, json_body: dict | None) -> _Resp:
        """curl_cffi with a Chrome TLS fingerprint, for Akamai-fronted hosts that 403 httpx (F-06):
        api.bseindia.com, incometaxindia.gov.in. Tests (a mock transport) go through httpx instead."""
        if self.transport is not None:
            r = self.client.request(method, url, headers=headers, json=json_body)
            return _Resp(r.status_code, r.headers, r.content, str(r.url))
        from curl_cffi import requests as creq
        with self._guard:
            if self._cffi is None:
                self._cffi = creq.Session(impersonate="chrome124")
        try:
            r = self._cffi.request(method, url, headers=headers, json=json_body, timeout=self.timeout)
        except Exception as e:  # noqa: BLE001  curl_cffi raises its own exception tree
            raise httpx.TransportError(f"{type(e).__name__}: {e}") from e
        return _Resp(r.status_code, r.headers, r.content, str(r.url))

    def _polite(self, host: str) -> threading.Lock:
        with self._guard:
            lock = self._host_locks.setdefault(host, threading.Lock())
        return lock

    def get(self, url: str, *, source_id: str, doc_type: str, expect: str = "any",
            conditional: bool = True, archive: bool = True, parent_id: int | None = None,
            headers: dict | None = None, json_body: dict | None = None,
            impersonate: bool = False) -> FetchResult:
        """GET (or POST with `json_body`, e.g. MoSPI's POST-only release API). POSTs are never conditional.
        `impersonate` sends the request through curl_cffi with a Chrome fingerprint."""
        host = urlparse(url).netloc
        h = dict(headers or {})
        if json_body is not None:
            conditional = False
        if conditional and self.db is not None:
            v = self.db.get_validators(url)
            if v:
                if v.get("etag"):
                    h["If-None-Match"] = v["etag"]
                if v.get("last_modified"):
                    h["If-Modified-Since"] = v["last_modified"]
        with self._polite(host):
            gap = time.monotonic() - self._host_last.get(host, 0.0)
            if gap < self.min_gap:
                time.sleep(self.min_gap - gap)
            try:
                method = "POST" if json_body is not None else "GET"
                if impersonate:
                    r = self._impersonated(method, url, h, json_body)
                else:
                    r = self._client_for(host).request(method, url, headers=h, json=json_body)
            except httpx.HTTPError as e:
                raise FetchError(url, "network", f"{type(e).__name__}: {e}"[:300]) from e
            finally:
                self._host_last[host] = time.monotonic()
        fetched_at = utcnow()
        kept = {k: v for k, v in r.headers.items() if k.lower() in KEEP_HEADERS}
        if r.status_code == 304:
            return FetchResult(url=url, final_url=str(r.url), status=304, fetched_at=fetched_at,
                               headers=kept, not_modified=True)
        if r.status_code != 200:
            raise FetchError(url, "http", f"HTTP {r.status_code}", r.status_code)
        bad = check_content(r.content, expect)
        if bad:
            raise FetchError(url, "bad_content", f"{bad}; first bytes {r.content[:120]!r}", 200)
        res = FetchResult(url=url, final_url=str(r.url), status=200, fetched_at=fetched_at,
                          headers=kept, content=r.content)
        if archive:
            res.sha256, key = self.archive.put(r.content)
            if self.db is not None:
                res.document_id, _ = self.db.insert_document(
                    source_id=source_id, doc_type=doc_type, url=url, fetched_at=fetched_at,
                    sha256=res.sha256, size=len(r.content), content_type=r.headers.get("content-type"),
                    http_status=200, headers=kept, storage_key=key, parent_id=parent_id)
        if conditional and self.db is not None and (r.headers.get("etag") or r.headers.get("last-modified")):
            self.db.set_validators(url, r.headers.get("etag"), r.headers.get("last-modified"))
        return res

    def get_with_retry(self, url: str, *, tries: int = 4, base_delay: float = 5.0, **kw) -> FetchResult:
        """For documents that are known to flap (rbidocs PDFs): back off on bad content or 5xx."""
        last: FetchError | None = None
        for i in range(tries):
            try:
                return self.get(url, **kw)
            except FetchError as e:
                last = e
                if e.kind == "http" and e.status and e.status < 500 and e.status != 429:
                    raise
                log.warning("fetch_retry", url=url, attempt=i + 1, kind=e.kind, detail=e.detail[:120])
                time.sleep(min(60.0, base_delay * 2 ** i))
        assert last is not None
        raise last

    def close(self) -> None:
        self.client.close()
        for c in self._pinned.values():
            c.close()
        if self._cffi is not None:
            self._cffi.close()
