-- Phase 4: LLM triage queue + answer cache.
-- 'pending_llm': the keyword/ministry filter could not decide; the item waits for the batched
-- LLM triage job (or the timeout fallback to the digest). It is never silently dropped.
alter table items drop constraint if exists items_status_check;
alter table items add constraint items_status_check
    check (status in ('new', 'baseline', 'duplicate', 'filtered', 'routed', 'queued', 'error', 'pending_llm'));
create index if not exists items_pending_llm_idx on items (first_seen_at) where status = 'pending_llm';

-- Every model answer is cached by task + model + sha256 of the exact input, so the same
-- question is never asked twice (re-polls, replays, restarts).
create table if not exists llm_cache (
    task        text not null,
    model       text not null,
    input_sha   text not null,
    output      jsonb not null,
    ms          integer,
    created_at  timestamptz not null default now(),
    primary key (task, model, input_sha)
);
