-- Raw archive inside Postgres (Actions runners have no persistent disk).
-- Content-addressed like the file archive; bytes are gzip-compressed.
create table if not exists raw_blobs (
    storage_key  text primary key,               -- <sha[:2]>/<sha>, same as the file archive
    sha256       text not null,
    bytes        integer not null,               -- uncompressed size
    gz           bytea not null,
    created_at   timestamptz not null default now()
);
