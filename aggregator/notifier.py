from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class Notifier:
    """Send watchdog anomaly summaries to email when SMTP is configured."""

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
        self._smtp_from_email = smtp_from_email or smtp_username
        self._smtp_use_starttls = smtp_use_starttls
        self._alert_email = alert_email

    async def notify(self, *, service: str, severity: str, summary: str, details: str) -> list[str]:
        if not self._can_send_email():
            logger.info(
                "Email notification skipped for %s: missing SMTP_HOST/SMTP_FROM_EMAIL/ALERT_EMAIL",
                service,
            )
            return []
        ok = await self._send_email(service=service, severity=severity, summary=summary, details=details)
        return ["email"] if ok else []

    def _can_send_email(self) -> bool:
        return bool(
            self._smtp_host
            and self._smtp_port
            and self._smtp_from_email
            and self._alert_email
        )

    async def _send_email(self, *, service: str, severity: str, summary: str, details: str) -> bool:
        message = EmailMessage()
        message["Subject"] = f"[Watchdog] {severity.upper()} on {service}"
        message["From"] = self._smtp_from_email
        message["To"] = self._alert_email
        message.set_content(
            "\n".join(
                [
                    "Watchdog anomaly detected",
                    f"Service: {service}",
                    f"Severity: {severity}",
                    f"Summary: {summary}",
                    f"Details: {details}",
                ]
            )
        )

        def _send() -> None:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as client:
                if self._smtp_use_starttls:
                    client.starttls()
                if self._smtp_username:
                    client.login(self._smtp_username, self._smtp_password or "")
                client.send_message(message)

        try:
            await asyncio.to_thread(_send)
            return True
        except Exception as exc:
            logger.warning("Email notify failed: %s", exc)
            return False
