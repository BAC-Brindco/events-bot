"""Typed models shared across the bot. Source-specific shapes never live here."""
from __future__ import annotations

from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Country = Literal["IN", "US"]
Route = Literal["realtime", "us_morning_wrap", "india_eod", "weekly"]


class HealthRules(BaseModel):
    max_consecutive_errors: int = 3
    stale_after_hours: float | None = None      # stream: newest item older than this -> alert
    min_items: int | None = None                # parse must yield at least this many rows
    expect: Literal["xml", "json", "html", "pdf", "any"] = "any"


class ActiveHours(BaseModel):
    """Local (IST) polling window, e.g. SEBI 10:00-23:00. Outside it the poller sleeps."""
    start: time
    end: time

    def contains(self, t: time) -> bool:
        if self.start <= self.end:
            return self.start <= t <= self.end
        return t >= self.start or t <= self.end


class SourceConfig(BaseModel):
    id: str
    country: Country
    tier: Literal[1, 2, 3]
    kind: Literal["scheduled", "stream", "both"]
    feed_type: Literal["rss", "api", "ics", "html"]
    urls: dict[str, str]
    poll_interval: int = Field(gt=0, description="seconds")
    window_poll_interval: int | None = Field(default=None, gt=0)
    filters: dict = Field(default_factory=dict)
    adapter: str                                  # "india.rbi:RbiRss" under events_bot.sources
    stage2_eligible: bool = False
    health_rules: HealthRules = Field(default_factory=HealthRules)
    active_hours: ActiveHours | None = None
    route: Route | None = None                    # override of the default tier routing
    label: str | None = None                      # short tag for subjects, e.g. "RBI"
    event_name: str = "Release"                   # subject noun for stream items, e.g. "Press release"
    enabled: bool = True

    @field_validator("adapter")
    @classmethod
    def _adapter_path(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError("adapter must be 'module.path:ClassName'")
        return v

    @property
    def tag(self) -> str:
        return self.label or self.id.split("_")[0].upper()


class FetchResult(BaseModel):
    url: str
    final_url: str
    status: int
    fetched_at: datetime
    headers: dict[str, str] = Field(default_factory=dict)
    content: bytes = b""
    not_modified: bool = False
    document_id: int | None = None                # set once archived
    sha256: str | None = None

    model_config = {"arbitrary_types_allowed": True}


class RawItem(BaseModel):
    """What a stream adapter hands to the pipeline. Text is verbatim from the source."""
    source_id: str
    ext_id: str
    url: str
    title: str
    source_published_at: datetime | None = None
    published_raw: str | None = None
    document_id: int | None = None
    summary: str | None = None
    meta: dict = Field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"item:{self.source_id}:{self.ext_id}"


class ScheduledEvent(BaseModel):
    ref: str
    source_id: str
    event_type: str
    title: str
    scheduled_at: datetime
    window_start: datetime
    window_end: datetime
    calendar_url: str
    meta: dict = Field(default_factory=dict)


class Message(BaseModel):
    """A rendered outbound message, channel-neutral."""
    ref: str
    stage: str
    kind: str
    subject: str
    body_html: str
    body_text: str
