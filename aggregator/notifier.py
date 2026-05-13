from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

import httpx

from aggregator.notification_config import NotificationConfig

logger = logging.getLogger(__name__)

BARK_SERVER = "https://api.day.app"


class Notifier:
    """Send watchdog anomaly summaries via the channels configured at runtime.

    Currently supports SMTP email and Bark (iOS push). The config object is
    swappable at runtime — `update_config()` is called when the UI saves new
    settings, so the running watchdog picks them up without restart.
    """

    def __init__(self, config: NotificationConfig) -> None:
        self._config = config

    def update_config(self, config: NotificationConfig) -> None:
        self._config = config

    @property
    def config(self) -> NotificationConfig:
        return self._config

    async def notify(self, *, service: str, severity: str, summary: str, details: str) -> list[str]:
        sent: list[str] = []
        tasks: list[tuple[str, asyncio.Task]] = []
        if self._config.email.enabled and self._can_send_email():
            tasks.append(("email", asyncio.create_task(
                self._send_email(service=service, severity=severity, summary=summary, details=details)
            )))
        if self._config.bark.enabled and self._config.bark.device_key:
            tasks.append(("bark", asyncio.create_task(
                self._send_bark(service=service, severity=severity, summary=summary, details=details)
            )))
        for name, task in tasks:
            try:
                if await task:
                    sent.append(name)
            except Exception as exc:
                logger.warning("%s notify raised: %s", name, exc)
        if not tasks:
            logger.info("No notification channels enabled/configured for %s", service)
        return sent

    # ── Email ────────────────────────────────────────────────────────────────

    def _can_send_email(self) -> bool:
        e = self._config.email
        return bool(e.smtp_host and e.smtp_port and (e.smtp_from_email or e.smtp_username) and e.alert_email)

    async def _send_email(self, *, service: str, severity: str, summary: str, details: str) -> bool:
        e = self._config.email
        message = EmailMessage()
        message["Subject"] = f"[Watchdog] {severity.upper()} on {service}"
        message["From"] = e.smtp_from_email or e.smtp_username
        message["To"] = e.alert_email
        message.set_content(
            "\n".join([
                "Watchdog anomaly detected",
                f"Service: {service}",
                f"Severity: {severity}",
                f"Summary: {summary}",
                f"Details: {details}",
            ])
        )

        def _send() -> None:
            with smtplib.SMTP(e.smtp_host, e.smtp_port, timeout=10) as client:
                if e.smtp_use_starttls:
                    client.starttls()
                if e.smtp_username:
                    client.login(e.smtp_username, e.smtp_password or "")
                client.send_message(message)

        try:
            await asyncio.to_thread(_send)
            return True
        except Exception as exc:
            logger.warning("Email notify failed: %s", exc)
            return False

    # ── Bark (iOS push) ──────────────────────────────────────────────────────

    async def _send_bark(self, *, service: str, severity: str, summary: str, details: str) -> bool:
        b = self._config.bark
        url = f"{BARK_SERVER.rstrip('/')}/{b.device_key}"
        # Bark level: critical | active | timeSensitive | passive.
        level = {"error": "critical", "warn": "timeSensitive"}.get(severity, "active")
        payload = {
            "title": f"Watchdog · {severity.upper()} · {service}",
            "body":  summary or details or "anomaly detected",
            "group": "watchdog",
            "level": level,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                logger.warning("Bark notify HTTP %s: %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            logger.warning("Bark notify failed: %s", exc)
            return False
