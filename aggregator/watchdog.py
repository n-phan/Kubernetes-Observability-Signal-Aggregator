"""
Auto-watchdog mode — continuously monitor for anomalies.

Runs periodic queries in the background and surfaces anomalies
without requiring the operator to manually check the dashboard.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class WatchdogState(BaseModel):
    """Current state of the watchdog."""

    enabled: bool = False
    active_services: list[str] = Field(default_factory=list)
    check_interval_seconds: int = 60
    lookback_minutes: int = 15
    anomaly_threshold: float = 0.7  # confidence threshold to alert


class AnomalyAlert(BaseModel):
    """An anomaly detected by the watchdog."""

    detected_at: datetime
    service: str
    anomaly_type: str  # "error_spike", "latency_spike", "log_burst", etc.
    severity: str  # info | warn | error | critical
    confidence: float
    summary: str
    details: dict = Field(default_factory=dict)


class WatchdogMonitor:
    """
    Background monitor that periodically checks services for anomalies.

    Usage:
        watchdog = WatchdogMonitor(query_function=aggregator.query)
        await watchdog.start(services=["service-a", "service-b"])
        # ... runs in background ...
        await watchdog.stop()
    """

    def __init__(
        self,
        query_function: Callable,
    ) -> None:
        """
        Initialize watchdog.

        Args:
            query_function: async function to call for each query
                           signature: async def query(target, namespace, start, end) -> UnifiedResult
        """
        self.query_function = query_function
        self.state = WatchdogState()
        self._task: asyncio.Task | None = None
        self._alerts: list[AnomalyAlert] = []
        self._callbacks: list[Callable[[AnomalyAlert], None]] = []

    def add_alert_callback(self, callback: Callable[[AnomalyAlert], None]) -> None:
        """Register a callback to be called when an anomaly is detected."""
        self._callbacks.append(callback)

    async def start(
        self,
        services: list[str],
        check_interval_seconds: int = 60,
        lookback_minutes: int = 15,
        anomaly_threshold: float = 0.7,
    ) -> None:
        """Start the watchdog monitor."""
        self.state.enabled = True
        self.state.active_services = services
        self.state.check_interval_seconds = check_interval_seconds
        self.state.lookback_minutes = lookback_minutes
        self.state.anomaly_threshold = anomaly_threshold

        logger.info(
            f"Watchdog started for {len(services)} services "
            f"(interval={check_interval_seconds}s)"
        )

        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """Stop the watchdog monitor."""
        self.state.enabled = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Watchdog stopped")

    async def get_alerts(self, limit: int = 10) -> list[AnomalyAlert]:
        """Get recent alerts (most recent first)."""
        return self._alerts[:limit]

    async def clear_alerts(self) -> None:
        """Clear all stored alerts."""
        self._alerts.clear()

    async def _monitor_loop(self) -> None:
        """Main monitoring loop — runs in background."""
        while self.state.enabled:
            try:
                now = datetime.now(tz=timezone.utc)
                window_end = now
                window_start = now - timedelta(minutes=self.state.lookback_minutes)

                # Query each service
                for service in self.state.active_services:
                    try:
                        result = await self.query_function(
                            target=service,
                            namespace="default",
                            window_start=window_start,
                            window_end=window_end,
                            include_rca=False,  # don't need RCA for watchdog
                        )

                        # Extract anomalies from correlations
                        new_alerts = []
                        for correlation in result.correlations:
                            if (
                                correlation.confidence >= self.state.anomaly_threshold
                                and correlation.severity in ("error", "warn")
                            ):
                                alert = AnomalyAlert(
                                    detected_at=now,
                                    service=service,
                                    anomaly_type=correlation.kind,
                                    severity=correlation.severity,
                                    confidence=correlation.confidence,
                                    summary=correlation.description,
                                    details={
                                        "timestamp": correlation.timestamp,
                                        "related_metric": correlation.related_metric,
                                        "related_log": correlation.related_log_sample,
                                    },
                                )
                                new_alerts.append(alert)

                        # Store and dispatch alerts
                        for alert in new_alerts:
                            self._alerts.insert(0, alert)  # newest first
                            for callback in self._callbacks:
                                try:
                                    callback(alert)
                                except Exception as e:
                                    logger.error(f"Alert callback failed: {e}")

                    except Exception as e:
                        logger.error(f"Watchdog query failed for {service}: {e}")

                # Wait for next check
                await asyncio.sleep(self.state.check_interval_seconds)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watchdog monitoring error: {e}")
                await asyncio.sleep(5)  # Back off before retrying


class AlertNotificationBridge:
    """Bridge to send watchdog alerts to notification providers."""

    def __init__(self, notification_manager) -> None:
        self.notification_manager = notification_manager

    async def on_alert(self, alert: AnomalyAlert) -> None:
        """Called when watchdog detects an anomaly."""
        await self.notification_manager.notify(
            title=f"{alert.anomaly_type.upper()} detected in {alert.service}",
            summary=alert.summary,
            severity=alert.severity,
            service_name=alert.service,
            metadata={
                "timestamp": alert.detected_at.isoformat(),
                "confidence": f"{alert.confidence:.1%}",
                "anomaly_type": alert.anomaly_type,
            },
        )
