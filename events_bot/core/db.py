"""Postgres access (Supabase in production, local Postgres in dev/tests).

One connection per thread, autocommit; each method is a single statement or an
explicit transaction, so scheduler threads never share a cursor.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import RawItem, ScheduledEvent, SourceConfig


class Database:
    def __init__(self, dsn: str, schema: str | None = None):
        self.dsn = dsn
        self.schema = schema  # own schema on a shared database (Supabase); None = default search_path
        self._local = threading.local()

    @property
    def conn(self) -> psycopg.Connection:
        c = getattr(self._local, "conn", None)
        if c is None or c.closed:
            c = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)
            if self.schema:
                # Set per session, not via the DSN: the Supabase pooler drops `options`.
                c.execute(sql.SQL("create schema if not exists {}").format(sql.Identifier(self.schema)))
                c.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(self.schema)))
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None and not c.closed:
            c.close()

    def q(self, sql: str, params: Iterable[Any] | dict | None = None) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def one(self, sql: str, params: Iterable[Any] | dict | None = None) -> dict | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    # ---- schema -----------------------------------------------------------
    def migrate(self, migrations_dir: Path) -> list[str]:
        self.q("create table if not exists schema_migrations "
               "(name text primary key, applied_at timestamptz not null default now())")
        done = {r["name"] for r in self.q("select name from schema_migrations")}
        applied = []
        for f in sorted(migrations_dir.glob("*.sql")):
            if f.name in done:
                continue
            with self.conn.transaction():
                self.conn.execute(f.read_text(encoding="utf-8"))
                self.conn.execute("insert into schema_migrations (name) values (%s)", (f.name,))
            applied.append(f.name)
        return applied

    def sync_sources(self, cfgs: dict[str, SourceConfig]) -> None:
        for c in cfgs.values():
            self.q("""insert into sources (id, country, tier, config, enabled, synced_at)
                      values (%s, %s, %s, %s, %s, now())
                      on conflict (id) do update set country = excluded.country, tier = excluded.tier,
                          config = excluded.config, enabled = excluded.enabled, synced_at = now()""",
                   (c.id, c.country, c.tier, Jsonb(c.model_dump(mode="json")), c.enabled))

    # ---- http cache -------------------------------------------------------
    def get_validators(self, url: str) -> dict | None:
        return self.one("select etag, last_modified from http_cache where url = %s", (url,))

    def set_validators(self, url: str, etag: str | None, last_modified: str | None) -> None:
        self.q("""insert into http_cache (url, etag, last_modified, updated_at) values (%s, %s, %s, now())
                  on conflict (url) do update set etag = excluded.etag,
                      last_modified = excluded.last_modified, updated_at = now()""",
               (url, etag, last_modified))

    # ---- documents --------------------------------------------------------
    def insert_document(self, *, source_id: str, doc_type: str, url: str, fetched_at: datetime,
                        sha256: str, size: int, content_type: str | None, http_status: int,
                        headers: dict, storage_key: str, published_at: datetime | None = None,
                        parent_id: int | None = None) -> tuple[int, bool]:
        """Returns (document_id, is_new). Same url + same bytes = same document."""
        row = self.one("""insert into documents (parent_id, source_id, doc_type, url, published_at, fetched_at,
                              sha256, bytes, content_type, http_status, http_headers, storage_key)
                          values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                          on conflict (url, sha256) do nothing returning id""",
                       (parent_id, source_id, doc_type, url, published_at, fetched_at, sha256, size,
                        content_type, http_status, Jsonb(headers), storage_key))
        if row:
            return row["id"], True
        row = self.one("select id from documents where url = %s and sha256 = %s", (url, sha256))
        return row["id"], False

    def get_document(self, doc_id: int) -> dict | None:
        return self.one("select * from documents where id = %s", (doc_id,))

    # ---- items ------------------------------------------------------------
    def insert_item(self, it: RawItem, *, url_norm: str, title_norm: str, tier: int,
                    first_seen_at: datetime) -> int | None:
        """None if the item is already known (same source + ext_id)."""
        row = self.one("""insert into items (ref, source_id, ext_id, url, url_norm, title, title_norm,
                              source_published_at, published_raw, first_seen_at, document_id, tier, meta)
                          values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                          on conflict (source_id, ext_id) do nothing returning id""",
                       (it.ref, it.source_id, it.ext_id, it.url, url_norm, it.title, title_norm,
                        it.source_published_at, it.published_raw, first_seen_at, it.document_id, tier,
                        Jsonb(it.meta)))
        return row["id"] if row else None

    def get_item(self, ref: str) -> dict | None:
        return self.one("select * from items where ref = %s", (ref,))

    def items_by_url_norm(self, url_norm: str, exclude_id: int) -> list[dict]:
        return self.q("select id, source_id from items where url_norm = %s and id <> %s "
                      "and status <> 'duplicate' order by id limit 5", (url_norm, exclude_id))

    def recent_titles(self, since: datetime, exclude_id: int, other_than_source: str) -> list[dict]:
        return self.q("select id, source_id, title_norm from items where first_seen_at >= %s "
                      "and id <> %s and source_id <> %s and status <> 'duplicate' order by id",
                      (since, exclude_id, other_than_source))

    def items_with_doc_sha(self, sha: str, exclude_id: int) -> list[dict]:
        return self.q("""select i.id, i.source_id from items i join documents d on d.id = i.item_document_id
                         where d.sha256 = %s and i.id <> %s and i.status <> 'duplicate' order by i.id limit 5""",
                      (sha, exclude_id))

    def update_item(self, item_id: int, **fields) -> None:
        if "tags" in fields:
            fields["tags"] = list(fields["tags"])
        if "meta" in fields:
            fields["meta"] = Jsonb(fields["meta"])
        cols = ", ".join(f"{k} = %({k})s" for k in fields)
        self.q(f"update items set {cols} where id = %(id)s", {**fields, "id": item_id})

    def insert_filtered(self, item_id: int, source_id: str, title: str, url: str, rule: str,
                        reason: str | None) -> None:
        self.q("insert into filtered_items (item_id, source_id, title, url, rule, reason) "
               "values (%s,%s,%s,%s,%s,%s)", (item_id, source_id, title, url, rule, reason))

    def recent_filtered(self, n: int, source_id: str | None = None) -> list[dict]:
        if source_id:
            return self.q("select * from filtered_items where source_id = %s order by at desc limit %s",
                          (source_id, n))
        return self.q("select * from filtered_items order by at desc limit %s", (n,))

    # ---- events -----------------------------------------------------------
    def upsert_event(self, ev: ScheduledEvent, refreshed_at: datetime) -> None:
        self.q("""insert into events (ref, source_id, event_type, title, scheduled_at, window_start, window_end,
                      calendar_url, calendar_refreshed_at, meta)
                  values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                  on conflict (ref) do update set title = excluded.title, scheduled_at = excluded.scheduled_at,
                      window_start = excluded.window_start, window_end = excluded.window_end,
                      calendar_url = excluded.calendar_url, calendar_refreshed_at = excluded.calendar_refreshed_at,
                      -- calendar keys overwrite; progress keys (stage1_at, ...) survive a refresh.
                      -- The burst-job claim is dropped if the window moved, so it relaunches.
                      meta = case when events.window_start = excluded.window_start
                                  then events.meta || excluded.meta
                                  else (events.meta || excluded.meta) - 'burst_launched_at' end""",
               (ev.ref, ev.source_id, ev.event_type, ev.title, ev.scheduled_at, ev.window_start,
                ev.window_end, ev.calendar_url, refreshed_at, Jsonb(ev.meta)))

    def events_between(self, start: datetime, end: datetime) -> list[dict]:
        return self.q("select * from events where scheduled_at between %s and %s order by scheduled_at",
                      (start, end))

    def claim_window_launches(self, within: timedelta) -> list[str]:
        """Events whose window is open or opens within `within` and that have no burst job
        yet; marks them launched in the same statement, so overlapping ticks launch once."""
        rows = self.q("""update events set meta = meta || jsonb_build_object('burst_launched_at', now())
                         where window_start <= now() + %s and window_end >= now()
                           and status in ('scheduled', 'in_window')
                           and not (meta ? 'burst_launched_at')
                         returning ref""", (within,))
        return [r["ref"] for r in rows]

    def claim_arms(self, horizon: timedelta) -> list[dict]:
        """Events whose window opens within `horizon` (or is open) and has no burst job; claims them."""
        return self.q("""update events set meta = meta || jsonb_build_object('burst_launched_at', now())
                         where window_start <= now() + %s and window_end >= now()
                           and status in ('scheduled', 'in_window')
                           and not (meta ? 'burst_launched_at')
                         returning ref, window_start""", (horizon,))

    def get_event(self, ref: str) -> dict | None:
        return self.one("select * from events where ref = %s", (ref,))

    def mark_event(self, ref: str, status: str | None = None, **meta: Any) -> None:
        self.q("update events set status = coalesce(%s, status), meta = meta || %s where ref = %s",
               (status, Jsonb({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in meta.items()}), ref))

    # ---- documents and extractions ---------------------------------------
    def set_document_text(self, document_id: int, text: str) -> None:
        self.q("update documents set text = %s where id = %s", (text, document_id))

    def latest_document(self, url: str) -> dict | None:
        return self.one("select * from documents where url = %s order by fetched_at desc limit 1", (url,))

    def replace_extractions(self, document_id: int, exs: list) -> None:
        """Extractions are derived data: re-running a parse replaces the document's rows."""
        with self.conn.transaction():
            self.conn.execute("delete from extractions where document_id = %s", (document_id,))
            for e in exs:
                self.conn.execute(
                    """insert into extractions (document_id, field, value_text, value_norm, unit, period, page,
                           char_start, char_end, json_path, snippet, validated, validation_error)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (document_id, e.field, e.value_text, e.value_norm, e.unit, e.period, e.page, e.char_start,
                     e.char_end, e.json_path, e.snippet, e.validated, e.validation_error))

    # ---- sends ------------------------------------------------------------
    def claim_send(self, *, ref: str, stage: str, kind: str, channel: str, subject: str,
                   body_html: str, recipients: list[str], body_text: str = "") -> int | None:
        """Insert the send row first. None = this (ref, stage, kind) was already claimed,
        so the caller must not send. This is the double-send guard."""
        row = self.one("""insert into sends (ref, stage, kind, channel, subject, body_html, body_text, recipients)
                          values (%s,%s,%s,%s,%s,%s,%s,%s)
                          on conflict (ref, stage, kind) do nothing returning id""",
                       (ref, stage, kind, channel, subject, body_html, body_text, recipients))
        return row["id"] if row else None

    def finish_send(self, send_id: int, ok: bool, error: str | None = None) -> None:
        self.q("""update sends set status = %s, sent_at = case when %s then now() else sent_at end,
                      error = %s, attempts = attempts + 1 where id = %s""",
               ("sent" if ok else "failed", ok, error, send_id))

    def reclaim_failed_send(self, send_id: int) -> dict | None:
        """Only a failed send may be retried; flips it back to claimed atomically."""
        return self.one("update sends set status = 'claimed', error = null where id = %s "
                        "and status = 'failed' returning *", (send_id,))

    # ---- health -----------------------------------------------------------
    def health_ok(self, source_id: str, at: datetime, status: str, new_items: int) -> None:
        self.q("""insert into source_health (source_id, last_attempt_at, last_success_at, last_new_item_at,
                      consecutive_errors, last_status, last_error, updated_at)
                  values (%(s)s, %(at)s, %(at)s, case when %(n)s > 0 then %(at)s end, 0, %(st)s, null, now())
                  on conflict (source_id) do update set last_attempt_at = %(at)s, last_success_at = %(at)s,
                      last_new_item_at = case when %(n)s > 0 then %(at)s else source_health.last_new_item_at end,
                      consecutive_errors = 0, last_status = %(st)s, last_error = null, updated_at = now()""",
               {"s": source_id, "at": at, "st": status, "n": new_items})

    def health_error(self, source_id: str, at: datetime, error: str) -> int:
        row = self.one("""insert into source_health (source_id, last_attempt_at, consecutive_errors,
                              last_status, last_error, updated_at)
                          values (%(s)s, %(at)s, 1, 'error', %(e)s, now())
                          on conflict (source_id) do update set last_attempt_at = %(at)s,
                              consecutive_errors = source_health.consecutive_errors + 1,
                              last_status = 'error', last_error = %(e)s, updated_at = now()
                          returning consecutive_errors""",
                       {"s": source_id, "at": at, "e": error[:1000]})
        return row["consecutive_errors"]

    def health_rows(self) -> list[dict]:
        return self.q("select * from source_health order by source_id")

    def raise_alert(self, source_id: str | None, rule: str, detail: str) -> bool:
        """True if this is a new open alert (so it should be sent)."""
        row = self.one("""insert into health_alerts (source_id, rule, detail) values (%s, %s, %s)
                          on conflict (coalesce(source_id, ''), rule) where cleared_at is null do nothing
                          returning id""", (source_id, rule, detail))
        return row is not None

    def clear_alert(self, source_id: str | None, rule: str) -> None:
        self.q("update health_alerts set cleared_at = now() where coalesce(source_id, '') = %s "
               "and rule = %s and cleared_at is null", (source_id or "", rule))

    def open_alerts(self) -> list[dict]:
        return self.q("select * from health_alerts where cleared_at is null order by raised_at")


def json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, timedelta):
        return o.total_seconds()
    raise TypeError(type(o))


def dumps(o: Any) -> str:
    return json.dumps(o, default=json_default, ensure_ascii=False)
