"""
Runtime notification settings for the watchdog notifier.

The UI under Watchdog → Notifications lets the user configure email (SMTP) and
Bark push channels. Values are persisted to a JSON file in the same data volume
as history.db so they survive restarts. On first start they fall back to the
matching values in .env, so existing deployments keep working without touching
the UI.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, Field

from aggregator.config import settings

logger = logging.getLogger(__name__)

_CONFIG_FILE = Path(settings.history_db_path).with_name("notifications.json")
_lock = Lock()
# Visible token used to indicate "value present, redacted in API responses".
MASKED = "********"


class EmailConfig(BaseModel):
    enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_use_starttls: bool = True
    alert_email: str | None = None


class BarkConfig(BaseModel):
    enabled: bool = False
    device_key: str | None = None  # https://api.day.app/<device_key> is hit


class NotificationConfig(BaseModel):
    email: EmailConfig = Field(default_factory=EmailConfig)
    bark:  BarkConfig  = Field(default_factory=BarkConfig)

    def public(self) -> dict:
        """Return a dict suitable for the API: mask secrets so we never echo
        them back to the browser."""
        data = self.model_dump()
        if data["email"].get("smtp_password"):
            data["email"]["smtp_password"] = MASKED
        if data["bark"].get("device_key"):
            data["bark"]["device_key"] = MASKED
        return data


def _from_env() -> NotificationConfig:
    """Initial config sourced from .env — used until the user saves via UI."""
    s = settings
    email = EmailConfig(
        enabled=bool(s.smtp_host and s.smtp_from_email and s.alert_email),
        smtp_host=s.smtp_host,
        smtp_port=s.smtp_port,
        smtp_username=s.smtp_username,
        smtp_password=s.smtp_password,
        smtp_from_email=s.smtp_from_email,
        smtp_use_starttls=s.smtp_use_starttls,
        alert_email=s.alert_email,
    )
    return NotificationConfig(email=email)


def load_config() -> NotificationConfig:
    """Load from disk, or seed from .env on first run."""
    with _lock:
        if _CONFIG_FILE.exists():
            try:
                with _CONFIG_FILE.open("r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                return NotificationConfig.model_validate(raw)
            except Exception as exc:
                logger.warning("Could not parse %s, falling back to env: %s", _CONFIG_FILE, exc)
        return _from_env()


def save_config(cfg: NotificationConfig) -> None:
    with _lock:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CONFIG_FILE.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(cfg.model_dump(), fh, indent=2)
        tmp.replace(_CONFIG_FILE)


def merge_incoming(existing: NotificationConfig, incoming: dict) -> NotificationConfig:
    """Merge a partial update from the UI. Empty strings clear values; MASKED
    means "keep the stored secret unchanged" so the masked GET → POST round-trip
    doesn't overwrite the real password with the mask."""
    existing_d = existing.model_dump()
    for section in ("email", "bark"):
        if section not in incoming:
            continue
        for k, v in (incoming[section] or {}).items():
            if v == MASKED:
                continue
            existing_d[section][k] = v
    return NotificationConfig.model_validate(existing_d)
