"""Delivery behind one interface. Email first; Slack/Telegram are stubs.

Modes
  live     claim a `sends` row (unique on ref, stage, kind) BEFORE sending. If the claim
           fails, the message was already sent or is in flight, so nothing goes out.
           A crash between claim and send leaves a 'claimed' row: it is never resent
           automatically; health reports it and `cli retry-send` is an explicit operator act.
  dry_run  render to out/dry_run/, touch nothing in `sends`.
  replay   render to out/replay/, touch nothing in `sends`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import structlog

from ..core.db import Database
from ..core.models import Message

log = structlog.get_logger()
Mode = Literal["live", "dry_run", "replay"]


class Channel(Protocol):
    name: str

    def send(self, msg: Message, recipients: list[str]) -> None: ...


@dataclass
class DispatchResult:
    status: Literal["sent", "already_sent", "written", "failed", "no_recipients"]
    detail: str | None = None
    path: Path | None = None


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:150]


class Dispatcher:
    def __init__(self, db: Database | None, channels: list[Channel], mode: Mode, out_dir: Path):
        self.db, self.channels, self.mode, self.out_dir = db, channels, mode, out_dir

    def write_file(self, msg: Message, recipients: list[str]) -> Path:
        d = self.out_dir / self.mode
        d.mkdir(parents=True, exist_ok=True)
        base = d / safe_name(f"{msg.ref}__{msg.stage}__{msg.kind}")
        base.with_suffix(".html").write_text(msg.body_html, encoding="utf-8")
        base.with_suffix(".txt").write_text(
            f"Subject: {msg.subject}\nTo: {', '.join(recipients) or '(none configured)'}\n\n{msg.body_text}",
            encoding="utf-8")
        return base.with_suffix(".html")

    def dispatch(self, msg: Message, recipients: list[str]) -> DispatchResult:
        if self.mode != "live":
            p = self.write_file(msg, recipients)
            log.info("dispatch_written", ref=msg.ref, stage=msg.stage, mode=self.mode, path=str(p))
            return DispatchResult("written", path=p)
        if not recipients:
            log.error("dispatch_no_recipients", ref=msg.ref)
            return DispatchResult("no_recipients")
        assert self.db is not None
        results = []
        for ch in self.channels:
            send_id = self.db.claim_send(ref=msg.ref, stage=msg.stage, kind=msg.kind, channel=ch.name,
                                         subject=msg.subject, body_html=msg.body_html,
                                         body_text=msg.body_text, recipients=recipients)
            if send_id is None:
                log.info("dispatch_already_claimed", ref=msg.ref, stage=msg.stage, kind=msg.kind)
                results.append(DispatchResult("already_sent"))
                continue
            try:
                ch.send(msg, recipients)
            except Exception as e:  # noqa: BLE001
                self.db.finish_send(send_id, False, f"{type(e).__name__}: {e}"[:1000])
                log.error("dispatch_failed", ref=msg.ref, channel=ch.name, error=str(e)[:300])
                results.append(DispatchResult("failed", str(e)[:300]))
                continue
            self.db.finish_send(send_id, True)
            log.info("dispatch_sent", ref=msg.ref, stage=msg.stage, channel=ch.name)
            results.append(DispatchResult("sent"))
        # One channel today; report the worst outcome.
        for s in ("failed", "sent", "already_sent"):
            for r in results:
                if r.status == s:
                    return r
        return DispatchResult("failed", "no channels")
