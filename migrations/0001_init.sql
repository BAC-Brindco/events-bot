-- Phase 1 core schema. All timestamps are UTC (timestamptz); rendering is IST.

create table if not exists sources (
    id            text primary key,
    country       text not null check (country in ('IN', 'US')),
    tier          smallint not null check (tier in (1, 2, 3)),
    config        jsonb not null,
    enabled       boolean not null default true,
    synced_at     timestamptz not null default now()
);

-- Conditional-GET validators, so a restart does not refetch everything.
create table if not exists http_cache (
    url           text primary key,
    etag          text,
    last_modified text,
    updated_at    timestamptz not null default now()
);

-- Raw bytes live in the archive store; this row points at them.
create table if not exists documents (
    id            bigserial primary key,
    parent_id     bigint references documents(id),
    source_id     text not null references sources(id),
    doc_type      text not null,
    url           text not null,
    published_at  timestamptz,
    fetched_at    timestamptz not null,
    sha256        text not null,
    bytes         integer not null,
    content_type  text,
    http_status   smallint,
    http_headers  jsonb not null default '{}',
    storage_key   text not null,
    text          text,
    unique (url, sha256)
);
create index if not exists documents_sha_idx on documents (sha256);
create index if not exists documents_source_idx on documents (source_id, fetched_at desc);

-- Scheduled items from official calendars.
create table if not exists events (
    id                     bigserial primary key,
    ref                    text not null unique,          -- e.g. rbi_mpc:2026-10-07
    source_id              text not null references sources(id),
    event_type             text not null,
    title                  text not null,
    scheduled_at           timestamptz not null,
    window_start           timestamptz not null,
    window_end             timestamptz not null,
    calendar_url           text not null,
    calendar_refreshed_at  timestamptz not null,
    status                 text not null default 'scheduled'
                           check (status in ('scheduled', 'in_window', 'captured', 'missed', 'cancelled')),
    meta                   jsonb not null default '{}'
);
create index if not exists events_sched_idx on events (scheduled_at);

-- Stream items (unscheduled releases).
create table if not exists items (
    id                  bigserial primary key,
    ref                 text not null unique,             -- item:<source_id>:<ext_id>
    source_id           text not null references sources(id),
    ext_id              text not null,
    url                 text not null,
    url_norm            text not null,
    title               text not null,
    title_norm          text not null,
    source_published_at timestamptz,                     -- time the source states
    published_raw       text,
    first_seen_at       timestamptz not null,            -- our fetch time
    document_id         bigint references documents(id), -- the feed/listing response it came from
    item_document_id    bigint references documents(id), -- the item's own page or file, once fetched
    tier                smallint not null,
    status              text not null default 'new'
                        check (status in ('new', 'baseline', 'duplicate', 'filtered', 'routed', 'queued', 'error')),
    route               text,                             -- realtime / us_morning_wrap / india_eod / weekly
    digest_id           bigint,                           -- set once a digest has carried it
    dup_of              bigint references items(id),
    dup_rule            text,
    tags                text[] not null default '{}',
    meta                jsonb not null default '{}',
    unique (source_id, ext_id)
);
create index if not exists items_url_norm_idx on items (url_norm);
create index if not exists items_seen_idx on items (first_seen_at desc);
create index if not exists items_route_idx on items (route, status);

create table if not exists extractions (
    id            bigserial primary key,
    document_id   bigint not null references documents(id),
    field         text not null,
    value_text    text not null,          -- exact source string
    value_norm    numeric,
    unit          text,
    period        text,
    page          integer,
    char_start    integer,
    char_end      integer,
    json_path     text,                   -- for API responses
    snippet       text,
    validated     boolean not null default false,
    validation_error text,
    created_at    timestamptz not null default now()
);
create index if not exists extractions_doc_idx on extractions (document_id);

-- Every stream item a filter rejected, for review and keyword expansion.
create table if not exists filtered_items (
    id         bigserial primary key,
    item_id    bigint not null references items(id),
    source_id  text not null references sources(id),
    title      text not null,
    url        text not null,
    rule       text not null,
    reason     text,
    at         timestamptz not null default now()
);
create index if not exists filtered_items_at_idx on filtered_items (at desc);

-- One row per outbound message. The unique key is what makes a restart unable to double-send.
create table if not exists sends (
    id          bigserial primary key,
    ref         text not null,
    stage       text not null,            -- stage1 / stage2 / stage2_followup / digest / health
    kind        text not null,            -- realtime / us_morning_wrap / india_eod / weekly / heartbeat / alert
    channel     text not null,
    status      text not null default 'claimed' check (status in ('claimed', 'sent', 'failed')),
    subject     text not null,
    body_html   text not null,
    body_text   text not null default '',
    recipients  text[] not null,
    claimed_at  timestamptz not null default now(),
    sent_at     timestamptz,
    error       text,
    attempts    smallint not null default 0,
    unique (ref, stage, kind)
);

create table if not exists digests (
    id            bigserial primary key,
    kind          text not null,
    period_start  timestamptz not null,
    period_end    timestamptz not null,
    item_ids      bigint[] not null default '{}',
    send_id       bigint references sends(id),
    rendered_at   timestamptz not null default now(),
    unique (kind, period_start)
);

create table if not exists source_health (
    source_id           text primary key references sources(id),
    last_attempt_at     timestamptz,
    last_success_at     timestamptz,
    last_new_item_at    timestamptz,
    consecutive_errors  integer not null default 0,
    last_status         text,
    last_error          text,
    updated_at          timestamptz not null default now()
);

-- Raised health alerts, so one condition alerts once until it clears.
create table if not exists health_alerts (
    id          bigserial primary key,
    source_id   text,
    rule        text not null,
    detail      text not null,
    raised_at   timestamptz not null default now(),
    cleared_at  timestamptz
);
create unique index if not exists health_alerts_open_idx
    on health_alerts (coalesce(source_id, ''), rule) where cleared_at is null;
