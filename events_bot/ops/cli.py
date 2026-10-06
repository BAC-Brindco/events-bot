"""Command line.

  uv run python -m events_bot.ops.cli migrate
  uv run python -m events_bot.ops.cli sources
  uv run python -m events_bot.ops.cli poll rbi_pr [--dry-run]
  uv run python -m events_bot.ops.cli run [--dry-run]
  uv run python -m events_bot.ops.cli tick [--dry-run]                  # one pass (GitHub Actions)
  uv run python -m events_bot.ops.cli windows [--max-minutes 330] [--dry-run]
  uv run python -m events_bot.ops.cli replay item:rbi_pr:prid:63719
  uv run python -m events_bot.ops.cli replay --file tests/fixtures/rbi/x.xml --source rbi_pr
  uv run python -m events_bot.ops.cli rejected [-n 50] [--source pib]
  uv run python -m events_bot.ops.cli health
  uv run python -m events_bot.ops.cli calendar
  uv run python -m events_bot.ops.cli retry-send <send_id>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core.app import App
from ..core.logsetup import configure
from ..core.models import Message
from ..core.settings import ROOT, Settings
from ..core.timeutil import fmt_ist


def _app(dry_run: bool = False, mode: str | None = None) -> App:
    configure(ROOT / "logs")
    app = App(Settings.from_env(), mode=mode or ("dry_run" if dry_run else "live"))
    app.setup()
    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="events_bot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("sources")
    p = sub.add_parser("poll")
    p.add_argument("source")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("run")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("tick")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("due-windows")
    p.add_argument("--within", type=int, default=25, help="minutes ahead")
    p = sub.add_parser("windows")
    p.add_argument("--max-minutes", type=int, default=330)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("replay")
    p.add_argument("ref", nargs="?")
    p.add_argument("--file", type=Path)
    p.add_argument("--source")
    p = sub.add_parser("rejected")
    p.add_argument("-n", type=int, default=30)
    p.add_argument("--source")
    sub.add_parser("health")
    p = sub.add_parser("calendar")
    p.add_argument("--days", type=int, default=14)
    p = sub.add_parser("retry-send")
    p.add_argument("send_id", type=int)
    a = ap.parse_args(argv)

    if a.cmd == "migrate":
        configure(ROOT / "logs")
        app = App(Settings.from_env())
        print("applied:", app.setup() or "nothing new")
        return 0

    if a.cmd == "sources":
        app = _app()
        rows = {r["source_id"]: r for r in app.db.health_rows()}
        for sid, c in app.sources.items():
            h = rows.get(sid, {})
            print(f"{sid:14} {c.country} T{c.tier} {c.kind:9} every {c.poll_interval:>4}s  "
                  f"last ok {fmt_ist(h.get('last_success_at'))}  errors {h.get('consecutive_errors', 0)}")
        return 0

    if a.cmd == "poll":
        app = _app(a.dry_run)
        st = app.poll_source(a.source)
        if st is None:
            print("poll failed; see `health`")
            return 1
        print({k: v for k, v in st.__dict__.items() if k != "refs"})
        for r in st.refs:
            print(" ", r)
        return 0

    if a.cmd == "run":
        from ..core.scheduler import build_scheduler
        app = _app(a.dry_run)
        print(f"running ({app.mode}); Ctrl+C to stop")
        try:
            build_scheduler(app).start()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            app.close()
        return 0

    if a.cmd == "tick":
        app = _app(a.dry_run)
        try:
            for sid, res in app.tick().items():
                print(f"{sid:14} {res}")
        finally:
            app.close()
        return 0

    if a.cmd == "due-windows":
        # Printed refs are claimed; the workflow dispatches one burst job when any print.
        from datetime import timedelta
        app = _app()
        try:
            for ref in app.db.claim_window_launches(timedelta(minutes=a.within)):
                print(ref)
        finally:
            app.close()
        return 0

    if a.cmd == "windows":
        # Burst job: poll scheduled events while any is inside its window, then exit.
        import time as _t
        from datetime import timedelta
        from ..core.scheduler import WindowPoller
        from ..core.timeutil import utcnow
        app = _app(a.dry_run)
        wp, stop = WindowPoller(app), _t.monotonic() + a.max_minutes * 60
        try:
            while _t.monotonic() < stop:
                now = utcnow()
                live = [e for e in app.db.events_between(now - timedelta(hours=3), now + timedelta(minutes=30))
                        if e["window_end"] >= now]
                if not live:
                    print("no event window open or due within 30 min; exiting")
                    break
                wp.tick()
                _t.sleep(5)
        finally:
            app.close()
        return 0

    if a.cmd == "replay":
        from .replay import replay_file, replay_ref
        app = _app(mode="replay")
        if a.file:
            if not a.source:
                ap.error("--file needs --source")
            paths = replay_file(app, a.source, a.file)
        elif a.ref:
            paths = replay_ref(app, a.ref)
        else:
            ap.error("give a ref or --file")
        for p in paths:
            print(p)
        return 0

    if a.cmd == "rejected":
        app = _app()
        for r in app.db.recent_filtered(a.n, a.source):
            print(f"{fmt_ist(r['at'])}  {r['source_id']:10} {r['rule']:16} {r['title'][:90]}  ({r['reason'] or ''})")
        return 0

    if a.cmd == "health":
        app = _app()
        for r in app.db.health_rows():
            print(f"{r['source_id']:14} {r['last_status'] or '-':6} ok {fmt_ist(r['last_success_at'])}  "
                  f"new {fmt_ist(r['last_new_item_at'])}  errors {r['consecutive_errors']}  {r['last_error'] or ''}")
        for b in app.db.open_alerts():
            print(f"OPEN ALERT {b['source_id'] or 'bot'} {b['rule']}: {b['detail']}")
        return 0

    if a.cmd == "calendar":
        from datetime import timedelta
        from ..core.timeutil import utcnow
        app = _app()
        print("refreshed", app.refresh_calendars(), "events")
        for e in app.db.events_between(utcnow(), utcnow() + timedelta(days=a.days)):
            print(f"{fmt_ist(e['scheduled_at'])}  {e['source_id']:12} {e['title']}")
        return 0

    if a.cmd == "retry-send":
        app = _app()
        row = app.db.reclaim_failed_send(a.send_id)
        if row is None:
            print("only a send in status 'failed' can be retried")
            return 1
        msg = Message(ref=row["ref"], stage=row["stage"], kind=row["kind"], subject=row["subject"],
                      body_html=row["body_html"], body_text=row["body_text"])
        ch = app.dispatcher.channels[0]
        try:
            ch.send(msg, list(row["recipients"]))
        except Exception as e:  # noqa: BLE001
            app.db.finish_send(row["id"], False, str(e)[:1000])
            print("failed again:", e)
            return 1
        app.db.finish_send(row["id"], True)
        print("sent")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
