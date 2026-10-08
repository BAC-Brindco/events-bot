"""Runtime settings from the environment (and `.env`). Secrets only ever come from here."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader: KEY=VALUE lines, # comments, optional quotes. Real env wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        os.environ.setdefault(k, v)


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


class Settings(BaseModel):
    dsn: str = "host=localhost port=54433 user=postgres dbname=events_bot"
    db_schema: str | None = None
    ops_live: bool = True            # EVENTS_BOT_OPS_LIVE=0 keeps operator alerts in dry run too
    archive_dir: Path = ROOT / "archive" / "raw"
    archive_backend: str = "file"   # file | db
    out_dir: Path = ROOT / "out"
    config_dir: Path = ROOT / "config"
    migrations_dir: Path = ROOT / "migrations"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    recipients: list[str] = Field(default_factory=list)
    operator_emails: list[str] = Field(default_factory=list)

    anthropic_api_key: str | None = None
    fmp_api_key: str | None = None
    api_keys: dict[str, str] = Field(default_factory=dict)  # BLS_API_KEY, BEA_API_KEY, ...

    user_agent: str = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

    @classmethod
    def from_env(cls, dotenv: bool = True) -> "Settings":
        if dotenv:
            load_dotenv()
        e = os.environ
        kw: dict = {}
        if e.get("EVENTS_BOT_DSN"):
            kw["dsn"] = e["EVENTS_BOT_DSN"]
        if e.get("EVENTS_BOT_OPS_LIVE"):
            kw["ops_live"] = e["EVENTS_BOT_OPS_LIVE"].lower() in ("1", "true", "yes")
        if e.get("EVENTS_BOT_DB_SCHEMA"):
            kw["db_schema"] = e["EVENTS_BOT_DB_SCHEMA"]
        if e.get("EVENTS_BOT_ARCHIVE_DIR"):
            kw["archive_dir"] = Path(e["EVENTS_BOT_ARCHIVE_DIR"])
        if e.get("EVENTS_BOT_ARCHIVE"):
            kw["archive_backend"] = e["EVENTS_BOT_ARCHIVE"]
        if e.get("EVENTS_BOT_OUT_DIR"):
            kw["out_dir"] = Path(e["EVENTS_BOT_OUT_DIR"])
        kw.update(
            smtp_host=e.get("SMTP_HOST"), smtp_user=e.get("SMTP_USER"),
            smtp_password=e.get("SMTP_PASSWORD"), smtp_from=e.get("SMTP_FROM"),
            recipients=_csv(e.get("EVENTS_BOT_RECIPIENTS")),
            operator_emails=_csv(e.get("EVENTS_BOT_OPERATOR_EMAILS")),
            anthropic_api_key=e.get("ANTHROPIC_API_KEY"), fmp_api_key=e.get("FMP_API_KEY"),
            api_keys={k: e[k] for k in ("BLS_API_KEY", "BEA_API_KEY", "CENSUS_API_KEY",
                                        "EIA_API_KEY", "API_DATA_GOV_KEY") if e.get(k)},
        )
        if e.get("SMTP_PORT"):
            kw["smtp_port"] = int(e["SMTP_PORT"])
        if e.get("SMTP_STARTTLS"):
            kw["smtp_starttls"] = e["SMTP_STARTTLS"].lower() in ("1", "true", "yes")
        return cls(**kw)
