"""Content-addressed raw archive. Every new document's bytes land here before parsing.

Local filesystem for now; a Supabase Storage backend can implement the same two
methods once F-10 is settled.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


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
