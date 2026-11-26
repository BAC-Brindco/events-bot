"""Loads the YAML registry (sources), delivery schedule and keyword lists."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .models import Route, SourceConfig


class CronSpec(BaseModel):
    """APScheduler cron fields, in IST."""
    hour: int | str
    minute: int | str = 0
    day_of_week: str | None = None


class DeliveryConfig(BaseModel):
    realtime_channels: list[str] = ["email"]
    us_morning_wrap: CronSpec
    india_eod: CronSpec
    weekly: CronSpec
    heartbeat: CronSpec
    # Default route per (country, tier). Source-level `route` overrides it.
    routes: dict[str, Route] = Field(default_factory=dict)
    # F-08 toggles
    us_realtime_keywords: list[str] = Field(default_factory=list)
    us_realtime_prints: bool = False
    realtime_max_age_hours: float = 12

    def route_for(self, src: SourceConfig) -> Route:
        if src.route:
            return src.route
        key = f"{src.country}:{src.tier}"
        if key not in self.routes:
            raise KeyError(f"delivery.yaml has no route for {key}")
        return self.routes[key]


def _load_yaml(p: Path) -> dict:
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_sources(config_dir: Path) -> dict[str, SourceConfig]:
    """sources.yaml, then every config/sources.d/*.yaml in name order (one file per source group)."""
    entries = list(_load_yaml(config_dir / "sources.yaml").get("sources", []))
    for extra in sorted((config_dir / "sources.d").glob("*.yaml")):
        entries += _load_yaml(extra).get("sources", []) or []
    out: dict[str, SourceConfig] = {}
    for entry in entries:
        cfg = SourceConfig.model_validate(entry)
        if cfg.id in out:
            raise ValueError(f"duplicate source id {cfg.id}")
        out[cfg.id] = cfg
    return out


def load_delivery(config_dir: Path) -> DeliveryConfig:
    return DeliveryConfig.model_validate(_load_yaml(config_dir / "delivery.yaml"))


def load_keywords(config_dir: Path, name: str) -> dict:
    """config/keywords/<name>.yaml; editable without a code change."""
    p = config_dir / "keywords" / f"{name}.yaml"
    return _load_yaml(p) if p.exists() else {}


def config_problems(sources: dict[str, SourceConfig], delivery: DeliveryConfig) -> list[str]:
    """Settings that promise a delivery no enabled source can make. `us_realtime_prints: true` sat for seven weeks
    with no US print source behind it, so the Nov 2026 CPI / PCE prints sent nothing and nothing complained
    (FLAGS F-30). App logs these at start-up; tests/test_routing_and_digest_kinds.py fails on any."""
    from .registry import resolve
    from ..sources.us.prints_base import ScheduledPrint
    out = []
    prints = [s.id for s in sources.values()
              if s.enabled and s.country == "US" and issubclass(resolve(s.adapter), ScheduledPrint)]
    if delivery.us_realtime_prints and not prints:
        out.append("delivery.us_realtime_prints is true but no enabled US ScheduledPrint source exists")
    if delivery.us_realtime_keywords and not any(
            s.enabled and s.id in ("whitehouse", "ustr") for s in sources.values()):
        out.append("delivery.us_realtime_keywords is set but neither whitehouse nor ustr is enabled")
    return out
