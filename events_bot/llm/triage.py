"""Batched LLM triage of stream items the rules left 'uncertain' (status pending_llm).

Run by the `llm` step of the tick workflow only when the queue is non-empty, so the model is
started at most once per tick and never for nothing. Decisions:
  relevant  -> delivered exactly like a rule keep (enrich, tags, realtime or digest);
  not       -> logged to filtered_items with rule 'llm' (reviewable with `cli rejected`);
  no answer -> stays queued; after `max_wait` it goes to the source's digest tagged 'unclassified',
               so an item is never lost because the model was down.
"""
from __future__ import annotations

from datetime import timedelta

import structlog

from ..core.models import RawItem
from ..core.pipeline import DIGEST_FOR_COUNTRY, PollStats
from ..core.timeutil import utcnow
from .client import LLM, LLMUnavailable
from .prompts import TRIAGE_SCHEMA, TRIAGE_SYSTEM, triage_user

log = structlog.get_logger()


def _raw(row: dict) -> RawItem:
    return RawItem(source_id=row["source_id"], ext_id=row["ext_id"], url=row["url"], title=row["title"],
                   source_published_at=row["source_published_at"], published_raw=row["published_raw"],
                   document_id=row["document_id"], meta=row["meta"] or {})


def run(app, llm: LLM | None, *, max_wait: timedelta = timedelta(minutes=60), limit: int = 200) -> PollStats:
    st = PollStats()
    rows = app.db.q("select * from items where status = 'pending_llm' order by first_seen_at limit %s", (limit,))
    st.seen = len(rows)
    up = llm is not None and llm.healthy()
    for row in rows:
        cfg = app.sources.get(row["source_id"])
        if cfg is None:
            continue
        it = _raw(row)
        adapter = app.adapter(cfg.id)
        enrich = getattr(adapter, "enrich", None)
        now = utcnow()
        verdict = None
        if up:
            try:
                verdict = bool(llm.ask("triage", TRIAGE_SYSTEM, triage_user(it), TRIAGE_SCHEMA)["relevant"])
            except LLMUnavailable as e:
                log.warning("llm_unavailable", error=str(e)[:200])
                up = False
        if verdict is True:
            it.meta["triage"] = "llm:relevant"            # deliver() persists it.meta
            app.pipeline.deliver(cfg, row["id"], it, now, st, enrich)
        elif verdict is False:
            app.db.insert_filtered(row["id"], cfg.id, it.title, it.url, "llm", "model: not market-relevant")
            app.db.update_item(row["id"], status="filtered", meta={**it.meta, "triage": "llm:not_relevant"})
            st.filtered += 1
        elif now - row["first_seen_at"] > max_wait:
            # Never lose an item because the model could not run: it goes to the digest, flagged.
            it.meta["triage"] = "fallback:unclassified"
            app.pipeline.deliver(cfg, row["id"], it, now, st, enrich, route=DIGEST_FOR_COUNTRY[cfg.country],
                                 extra_tags=["unclassified"])
    log.info("llm_triage", seen=st.seen, delivered=st.realtime + st.queued, filtered=st.filtered,
             calls=getattr(llm, "calls", 0), cache_hits=getattr(llm, "cache_hits", 0), up=up)
    return st
