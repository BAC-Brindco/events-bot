"""structlog setup: JSON lines to stderr and to logs/events_bot.log."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog


def configure(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr),
                                       logging.FileHandler(log_dir / "events_bot.log", encoding="utf-8")]
    logging.basicConfig(level=level, format="%(message)s", handlers=handlers, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    structlog.configure(
        processors=[structlog.processors.add_log_level,
                    structlog.processors.TimeStamper(fmt="iso", utc=True),
                    structlog.processors.JSONRenderer(ensure_ascii=False)],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
