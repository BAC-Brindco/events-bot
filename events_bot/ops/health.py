"""Source health (skeleton; Phase 8 adds window-closed, calendar and schema-drift rules).

Rules evaluated now:
  consecutive_errors  a source failed max_consecutive_errors times in a row
  stale               a stream source's newest item is older than stale_after_hours
  stuck_send          a send row stayed 'claimed' > 10 min (crash between claim and send)

An alert is raised once per open condition (health_alerts open index) and cleared
when the condition goes away. Alerts go to the operator, not the desk.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ..core.db import Database
from ..core.models import Message, SourceConfig
from ..core.timeutil import fmt_ist, utcnow


@dataclass
class Breach:
    source_id: str | None
    rule: str
    detail: str


def evaluate(db: Database, sources: dict[str, SourceConfig]) -> list[Breach]:
    now = utcnow()
    rows = {r["source_id"]: r for r in db.health_rows()}
    out: list[Breach] = []
    for sid, cfg in sources.items():
        if not cfg.enabled:
            continue
        h = rows.get(sid)
        if h is None:
            continue
        if h["consecutive_errors"] >= cfg.health_rules.max_consecutive_errors:
            out.append(Breach(sid, "consecutive_errors",
                              f"{h['consecutive_errors']} consecutive errors; last: {h['last_error']}"))
        stale_h = cfg.health_rules.stale_after_hours
        if stale_h and cfg.kind in ("stream", "both") and h["last_success_at"]:
            newest = h["last_new_item_at"]
            if newest is None:
                newest = db.one("select max(first_seen_at) as m from items where source_id = %s", (sid,))["m"]
            if newest is not None and now - newest > timedelta(hours=stale_h):
                out.append(Breach(sid, "stale", f"no new item since {fmt_ist(newest)} (threshold {stale_h} h)"))
    # Scheduled events are never silently missed. Both rules stay active while true, so each alerts once.
    for e in db.q("""select ref, source_id, title, window_start, window_end, status from events
                     where window_end < now() and window_end > now() - interval '7 days'
                       and not (meta ? 'stage1_at')"""):
        out.append(Breach(e["source_id"], f"missed:{e['ref']}",
                          f"{e['title']}: window {fmt_ist(e['window_start'])} to {fmt_ist(e['window_end'])} closed "
                          f"with no Stage 1 (status {e['status']}). Check the source and the windows run."))
    for e in db.q("""select ref, source_id, title, window_start from events
                     where window_start between now() and now() + interval '6 hours'
                       and not (meta ? 'burst_launched_at')"""):
        out.append(Breach(e["source_id"], f"unarmed:{e['ref']}",
                          f"{e['title']} opens {fmt_ist(e['window_start'])} and no burst job is armed. "
                          f"Run the calendars workflow or dispatch windows.yml."))
    for r in db.q("select id, ref, stage, kind, claimed_at from sends where status = 'claimed' "
                  "and claimed_at < now() - interval '10 minutes'"):
        out.append(Breach(None, f"stuck_send:{r['id']}",
                          f"send {r['id']} ({r['ref']} {r['stage']} {r['kind']}) claimed at "
                          f"{fmt_ist(r['claimed_at'])} and never finished; not resent automatically"))
    return out


def sync_alerts(db: Database, breaches: list[Breach]) -> list[Breach]:
    """Open new alerts, clear resolved ones. Returns only the newly raised breaches."""
    fresh = [b for b in breaches if db.raise_alert(b.source_id, b.rule, b.detail)]
    active = {(b.source_id or "", b.rule) for b in breaches}
    for a in db.open_alerts():
        if (a["source_id"] or "", a["rule"]) not in active:
            db.clear_alert(a["source_id"], a["rule"])
    return fresh


def alert_message(b: Breach) -> Message:
    at = utcnow()
    subject = f"[HEALTH] {b.source_id or 'bot'} | {b.rule}"
    text = f"{b.detail}\n\nRaised {fmt_ist(at)}."
    html = f"<p><b>{b.source_id or 'bot'}</b>: {b.rule}</p><p>{b.detail}</p><p>Raised {fmt_ist(at)}.</p>"
    ref = f"health:{b.source_id or 'bot'}:{b.rule}:{at:%Y%m%dT%H%M}"
    return Message(ref=ref, stage="health", kind="alert", subject=subject, body_html=html, body_text=text)
