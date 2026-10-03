"""Telegram channel placeholder (SPEC: Slack or Telegram come later)."""
from __future__ import annotations

from ..core.models import Message


class TelegramChannel:
    name = "telegram"

    def send(self, msg: Message, recipients: list[str]) -> None:
        raise NotImplementedError("Telegram delivery is not built yet")
