"""Watchlist tagging against config/watchlist.csv (ticker,name,aliases with ';').

The file is supplied by the desk (FLAGS F-13). Until it exists nothing is tagged.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from ..core.models import RawItem


class Watchlist:
    def __init__(self, path: Path):
        self.path = path
        self.entries: list[tuple[str, list[re.Pattern]]] = []
        self._mtime: float | None = None
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            self.entries, self._mtime = [], None
            return
        mtime = self.path.stat().st_mtime
        if mtime == self._mtime:
            return
        entries = []
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                ticker = (row.get("ticker") or "").strip()
                if not ticker:
                    continue
                names = [ticker, (row.get("name") or "").strip()]
                names += [a.strip() for a in (row.get("aliases") or "").split(";")]
                pats = [re.compile(rf"(?<![\w]){re.escape(n)}(?![\w])", re.I if len(n) > 4 else 0)
                        for n in names if len(n) >= 3]
                entries.append((ticker, pats))
        self.entries, self._mtime = entries, mtime

    def tags(self, item: RawItem) -> list[str]:
        self.reload()
        text = f"{item.title}\n{item.summary or ''}"
        return [f"watch:{t}" for t, pats in self.entries if any(p.search(text) for p in pats)]
