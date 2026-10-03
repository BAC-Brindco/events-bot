"""Relevance classifier interface.

Phase 1 ships the pass-through. The LLM classifier (Phase 4) plugs in here and
only ever returns a label; it never produces text that reaches an email.
"""
from __future__ import annotations

from typing import Protocol

from ..core.models import RawItem, SourceConfig
from .keywords import Decision


class Classifier(Protocol):
    def classify(self, cfg: SourceConfig, item: RawItem) -> Decision: ...


class PassThrough:
    def classify(self, cfg: SourceConfig, item: RawItem) -> Decision:
        return Decision(True, "passthrough")
