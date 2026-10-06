"""Content-addressed raw archive. Every new document's bytes land here before parsing.

Two backends with the same two methods: local files (dev) and a gzip'd `raw_blobs`
table (GitHub Actions, where the runner disk is thrown away after every job).
"""
from __future__ import annotations

import gzip
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .db import Database


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Archive:
    def __init__(self, root: Path):
        self.root = root

    def put(self, content: bytes) -> tuple[str, str]:
        """Store bytes; returns (sha256, storage_key). Idempotent."""
        h = sha256(content)
        key = f"{h[:2]}/{h}"
        p = self.root / key
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            tmp.write_bytes(content)
            tmp.replace(p)
        return h, key

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()


class DbArchive:
    def __init__(self, db: "Database"):
        self.db = db

    def put(self, content: bytes) -> tuple[str, str]:
        h = sha256(content)
        key = f"{h[:2]}/{h}"
        self.db.q("insert into raw_blobs (storage_key, sha256, bytes, gz) values (%s, %s, %s, %s) "
                  "on conflict (storage_key) do nothing",
                  (key, h, len(content), gzip.compress(content, compresslevel=6)))
        return h, key

    def get(self, key: str) -> bytes:
        row = self.db.one("select gz from raw_blobs where storage_key = %s", (key,))
        if row is None:
            raise KeyError(key)
        return gzip.decompress(row["gz"])
