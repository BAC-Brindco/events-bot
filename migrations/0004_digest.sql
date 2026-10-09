-- Daily Macro Digest (F-29): an item sent in a digest is 'digested' and points at its digests row.
alter table items drop constraint if exists items_status_check;
alter table items add constraint items_status_check
    check (status in ('new', 'baseline', 'duplicate', 'filtered', 'routed', 'queued', 'error', 'pending_llm',
                      'digested'));
create index if not exists items_digest_queue_idx on items (first_seen_at)
    where status = 'queued' and route = 'india_eod' and digest_id is null;
