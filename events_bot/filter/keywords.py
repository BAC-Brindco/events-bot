"""Keyword / ministry filtering for stream sources.

Rules come from the source's `filters` block and, optionally, a keyword file in
config/keywords/<name>.yaml (editable without a code change):

    filters:
      keywords: pib              # file with include_any / exclude_any / ministries
      include_any: [repo rate]   # inline additions
      exclude_any: [quiz]
      ministries: [Ministry of Finance]

Decision: exclude wins; otherwise keep if the ministry is listed OR a keyword
matches. A source with no rules keeps everything. Every rejection is logged to
filtered_items by the pipeline, so misses can be reviewed (`cli rejected`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..core.config import load_keywords
from ..core.models import RawItem, SourceConfig


@dataclass
class Decision:
    keep: bool
    rule: str
    reason: str | None = None


@lru_cache(maxsize=512)
def _pattern(term: str) -> re.Pattern:
    # Word-boundary match, case-insensitive; "PLI" must not match "compliance".
    return re.compile(rf"(?<![\w]){re.escape(term)}(?![\w])", re.I)


def _matches(terms: list[str], text: str) -> str | None:
    for t in terms:
        if _pattern(t).search(text):
            return t
    return None


class KeywordFilter:
    def __init__(self, config_dir: Path):
        self.config_dir = config_dir

    def rules(self, cfg: SourceConfig) -> dict[str, list[str]]:
        f = cfg.filters or {}
        merged = {"include_any": [], "exclude_any": [], "ministries": []}
        if f.get("keywords"):
            for k, v in load_keywords(self.config_dir, f["keywords"]).items():
                if k in merged:
                    merged[k] += list(v or [])
        for k in merged:
            merged[k] += list(f.get(k) or [])
        return merged

    def decide(self, cfg: SourceConfig, item: RawItem) -> Decision:
        r = self.rules(cfg)
        if not any(r.values()):
            return Decision(True, "no_rules")
        text = f"{item.title}\n{item.summary or ''}"
        hit = _matches(r["exclude_any"], text)
        if hit:
            return Decision(False, "exclude_keyword", hit)
        ministry = (item.meta.get("ministry") or "").strip()
        if ministry and any(ministry.lower() == m.lower() for m in r["ministries"]):
            return Decision(True, "ministry", ministry)
        hit = _matches(r["include_any"], text)
        if hit:
            return Decision(True, "keyword", hit)
        if not r["include_any"] and not r["ministries"]:
            return Decision(True, "exclude_only")
        if (cfg.filters or {}).get("llm_triage"):
            # Neither a clear keep nor a clear reject: the batched LLM triage decides (or the digest
            # fallback if it cannot run). Hard excludes above never reach the model.
            return Decision(False, "uncertain", f"ministry={ministry or 'n/a'}")
        return Decision(False, "no_match", f"ministry={ministry or 'n/a'}")
