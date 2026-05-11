"""
Notification system — deliver RCA summaries via Slack, SNS, or email.

Integrates with the RCA analyzer to send proactive incident alerts
when critical anomalies are detected.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class NotificationProvider(ABC):
    """Base class for notification channels."""

    @abstractmethod
    async def send(
        self,
        title: str,
        summary: str,
        severity: str,
        service_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Send a notification.

        Args:
            title: Brief incident title (e.g. "Error spike in payment-service")
            summary: RCA summary or key findings
            severity: "info" | "warn" | "error" | "critical"
            service_name: Target service name
            metadata: Additional context (timestamps, link to UI, etc.)

        Returns:
            True if sent successfully, False otherwise.
        """
        pass


class SlackNotifier(NotificationProvider):
    """Send RCA alerts to Slack via webhook."""

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    async def send(
        self,
        title: str,
        summary: str,
        severity: str,
        service_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Send message to Slack webhook."""
        metadata = metadata or {}

        # Color based on severity
        severity_colors = {
            "info": "#36a64f",
            "warn": "#ff8c00",
            "error": "#e74c3c",
            "critical": "#c0392b",
        }
        color = severity_colors.get(severity, "#95a5a6")

        # Build Slack message blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🔍 {title}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Service*\n{service_name}"},
                    {"type": "mrkdwn", "text": f"*Severity*\n{severity.upper()}"},
                ],
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": f"_{summary}_"}},
        ]

        # Add metadata fields if provided
        if metadata:
            fields = []
            if "timestamp" in metadata:
                fields.append(
                    {"type": "mrkdwn", "text": f"*Time*\n{metadata['timestamp']}"}
                )
            if "ui_link" in metadata:
                fields.append(
                    {"type": "mrkdwn", "text": f"*Dashboard*\n<{metadata['ui_link']}|View>"}
                )
            if fields:
                blocks.append({"type": "section", "fields": fields})

        payload = {
            "blocks": blocks,
            "attachments": [
                {
                    "color": color,
                    "footer": "K8s Observability Signal Aggregator",
                    "footer_icon": "https://emoji.slack-edge.com/T00000000/B00000000/12345678",
                }
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(self.webhook_url, json=payload)
                if response.status_code in (200, 201):
                    logger.info(f"Slack notification sent for {service_name}")
                    return True
                else:
                    logger.error(
                        f"Slack notification failed: {response.status_code} {response.text}"
                    )
                    return False
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return False


class SNSNotifier(NotificationProvider):
    """Send RCA alerts to AWS SNS (requires boto3)."""

    def __init__(self, topic_arn: str, region: str = "us-east-1") -> None:
        self.topic_arn = topic_arn
        self.region = region
        self._sns_client = None

    async def send(
        self,
        title: str,
        summary: str,
        severity: str,
        service_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Send alert to SNS topic."""
        metadata = metadata or {}

        # SNS message structure
        message = {
            "title": title,
            "service": service_name,
            "severity": severity,
            "summary": summary,
            "metadata": metadata,
        }

        try:
            # Lazy import boto3 so it's only required if SNS is used
            import boto3

            if self._sns_client is None:
                self._sns_client = boto3.client("sns", region_name=self.region)

            response = self._sns_client.publish(
                TopicArn=self.topic_arn,
                Subject=f"[{severity.upper()}] {title}",
                Message=json.dumps(message, indent=2, default=str),
            )

            logger.info(
                f"SNS notification sent ({response['MessageId']}) for {service_name}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send SNS notification: {e}")
            return False


class EmailNotifier(NotificationProvider):
    """Send RCA alerts via email (requires mailgun or similar)."""

    def __init__(self, mailgun_domain: str, mailgun_key: str, to_email: str) -> None:
        self.mailgun_domain = mailgun_domain
        self.mailgun_key = mailgun_key
        self.to_email = to_email
        self.mailgun_url = f"https://api.mailgun.net/v3/{mailgun_domain}/messages"

    async def send(
        self,
        title: str,
        summary: str,
        severity: str,
        service_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Send email via Mailgun."""
        metadata = metadata or {}

        # Build email body
        body = f"""
{title}

Service: {service_name}
Severity: {severity.upper()}

Summary:
{summary}
"""
        if metadata.get("ui_link"):
            body += f"\n\nView in Dashboard: {metadata['ui_link']}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.mailgun_url,
                    auth=("api", self.mailgun_key),
                    data={
                        "from": "alerts@observability.local",
                        "to": self.to_email,
                        "subject": f"[{severity.upper()}] {title}",
                        "text": body,
                    },
                )

                if response.status_code == 200:
                    logger.info(f"Email notification sent to {self.to_email}")
                    return True
                else:
                    logger.error(
                        f"Email notification failed: {response.status_code} {response.text}"
                    )
                    return False
        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")
            return False


class NotificationManager:
    """Orchestrates notifications across multiple providers."""

    def __init__(self) -> None:
        self.providers: list[NotificationProvider] = []

    def add_provider(self, provider: NotificationProvider) -> None:
        """Register a notification provider."""
        self.providers.append(provider)

    async def notify(
        self,
        title: str,
        summary: str,
        severity: str,
        service_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Send notification to all registered providers.

        Returns True if at least one succeeded.
        """
        if not self.providers:
            logger.warning("No notification providers configured")
            return False

        results = []
        for provider in self.providers:
            try:
                result = await provider.send(title, summary, severity, service_name, metadata)
                results.append(result)
            except Exception as e:
                logger.error(f"Notification provider failed: {e}")
                results.append(False)

        return any(results)
