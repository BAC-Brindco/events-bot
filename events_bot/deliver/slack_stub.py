"""Slack channel placeholder (SPEC: Slack or Telegram come later)."""
from __future__ import annotations

from ..core.models import Message


class SlackChannel:
    name = "slack"

    def send(self, msg: Message, recipients: list[str]) -> None:
        raise NotImplementedError("Slack delivery is not built yet")
