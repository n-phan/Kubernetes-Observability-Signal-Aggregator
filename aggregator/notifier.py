from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class Notifier:
    """Send watchdog anomaly summaries by email."""

    def __init__(
        self,
        *,
        smtp_host: str | None,
        smtp_port: int,
        smtp_username: str | None,
        smtp_password: str | None,
        smtp_from_email: str | None,
        smtp_use_starttls: bool,
        alert_email: str | None,
    ) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_username = smtp_username
        self._smtp_password = smtp_password
        self._smtp_from_email = smtp_from_email or smtp_username or alert_email
        self._smtp_use_starttls = smtp_use_starttls
        self._alert_email = alert_email

    async def notify(self, *, service: str, severity: str, summary: str, details: str) -> list[str]:
        if not self._smtp_host or not self._alert_email:
            logger.info("Email notification skipped: SMTP_HOST or ALERT_EMAIL not configured")
            return []

        subject = f"[Watchdog] {severity.upper()} {service}"
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._smtp_from_email or self._alert_email
        message["To"] = self._alert_email
        message.set_content(
            f"Service: {service}\n"
            f"Severity: {severity}\n"
            f"Summary: {summary}\n"
            f"Details: {details}\n"
        )

        def _send() -> None:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as client:
                if self._smtp_use_starttls:
                    client.starttls()
                if self._smtp_username and self._smtp_password:
                    client.login(self._smtp_username, self._smtp_password)
                client.send_message(message)

        try:
            await asyncio.to_thread(_send)
            return ["email"]
        except Exception as exc:
            logger.warning("Email notify failed: %s", exc)
            return []
