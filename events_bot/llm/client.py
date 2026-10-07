"""Small local LLM, provider-agnostic. No Claude/OpenAI dependency.

Production runs llama.cpp's llama-server on the GitHub runner itself (CPU, a few-GB GGUF model
from the Actions cache), so nothing depends on the PC or on a paid API. Any OpenAI-compatible
endpoint works by config: LLM_BASE_URL, LLM_MODEL, optional LLM_API_KEY.

Optimisation rules every task follows:
  - the model only sees what the deterministic rules could not decide;
  - every answer is cached in llm_cache by (task, model, sha256 of the exact request), so a
    re-poll, replay or restart never asks twice;
  - the system prompt is byte-identical across calls (llama-server reuses its KV cache), so a
    call costs only the item's own tokens;
  - output is schema-constrained JSON of a few tokens (a boolean, or sentence indices), never prose
    that would need the number guard.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass

import httpx
import structlog
from psycopg.types.json import Jsonb

log = structlog.get_logger()


class LLMUnavailable(Exception):
    pass


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str | None = None
    timeout: float = 120.0

    @classmethod
    def from_env(cls) -> "LLMConfig | None":
        url = os.environ.get("LLM_BASE_URL")
        if not url:
            return None
        return cls(base_url=url.rstrip("/"), model=os.environ.get("LLM_MODEL", "local"),
                   api_key=os.environ.get("LLM_API_KEY") or None)


class LLM:
    def __init__(self, cfg: LLMConfig, db=None):
        self.cfg, self.db = cfg, db
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        self.http = httpx.Client(timeout=cfg.timeout, headers=headers)
        self.calls = self.cache_hits = 0

    def healthy(self) -> bool:
        try:
            r = self.http.get(f"{self.cfg.base_url}/health", timeout=5)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def ask(self, task: str, system: str, user: str, schema: dict, *, max_tokens: int = 16) -> dict:
        body = {"model": self.cfg.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0, "max_tokens": max_tokens, "cache_prompt": True,
                "response_format": {"type": "json_schema", "json_schema": {"name": task, "schema": schema}}}
        sha = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if self.db is not None:
            row = self.db.one("select output from llm_cache where task = %s and model = %s and input_sha = %s",
                              (task, self.cfg.model, sha))
            if row is not None:
                self.cache_hits += 1
                return row["output"]
        t0 = time.time()
        try:
            r = self.http.post(f"{self.cfg.base_url}/v1/chat/completions", json=body)
            r.raise_for_status()
            out = json.loads(r.json()["choices"][0]["message"]["content"])
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise LLMUnavailable(f"{type(e).__name__}: {e}"[:300]) from e
        ms = int((time.time() - t0) * 1000)
        self.calls += 1
        if self.db is not None:
            self.db.q("insert into llm_cache (task, model, input_sha, output, ms) values (%s, %s, %s, %s, %s) "
                      "on conflict do nothing", (task, self.cfg.model, sha, Jsonb(out), ms))
        return out

    def close(self) -> None:
        self.http.close()
