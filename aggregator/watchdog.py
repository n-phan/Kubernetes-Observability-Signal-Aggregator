from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from aggregator.config import settings
from aggregator.models.query import QueryRequest
from aggregator.models.result import UnifiedResult
from aggregator.notification_config import NotificationConfig, load_config, save_config, merge_incoming
from aggregator.notifier import Notifier

logger = logging.getLogger(__name__)


@dataclass
class WatchdogConfig:
    enabled: bool = False
    interval_seconds: int = 60
    lookback_minutes: int = 15
    anomaly_threshold: float = 0.7


class AutoWatchdog:
    def __init__(self, *, aggregator_getter) -> None:
        self._aggregator_getter = aggregator_getter
        self._task: asyncio.Task | None = None
        self._alerts: list[dict] = []
        self._cfg = WatchdogConfig()
        self._notifier = Notifier(load_config())

    # ── Notification config (used by /api/watchdog/notifications) ────────────

    def get_notification_config(self) -> dict:
        """Public view of the current config — secrets masked."""
        return self._notifier.config.public()

    def update_notification_config(self, incoming: dict) -> dict:
        """Merge a partial UI update, persist, and hot-swap into the notifier."""
        merged = merge_incoming(self._notifier.config, incoming or {})
        save_config(merged)
        self._notifier.update_config(merged)
        return merged.public()

    async def test_notification(self) -> dict:
        """Fire a test notification through every enabled channel."""
        sent = await self._notifier.notify(
            service="test",
            severity="info",
            summary="Watchdog test notification",
            details="Triggered manually from the UI",
        )
        return {"sent": sent}

    def status(self) -> dict[str, object]:
        return {
            "enabled": self._task is not None and not self._task.done(),
            "interval_seconds": self._cfg.interval_seconds,
            "lookback_minutes": self._cfg.lookback_minutes,
            "anomaly_threshold": self._cfg.anomaly_threshold,
            "alerts": len(self._alerts),
        }

    def get_alerts(self) -> list[dict]:
        return list(reversed(self._alerts))

    def clear_alerts(self) -> None:
        self._alerts.clear()

    async def start(self, *, interval_seconds: int, lookback_minutes: int, anomaly_threshold: float) -> dict[str, object]:
        self._cfg.interval_seconds = max(15, interval_seconds)
        self._cfg.lookback_minutes = max(1, lookback_minutes)
        self._cfg.anomaly_threshold = min(1.0, max(0.0, anomaly_threshold))

        if self._task and not self._task.done():
            return self.status()

        self._task = asyncio.create_task(self._run_loop(), name="obs-watchdog")
        return self.status()

    async def stop(self) -> dict[str, object]:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        return self.status()

    async def _run_loop(self) -> None:
        logger.info(
            "Auto-watchdog started interval=%ss lookback=%sm threshold=%.2f",
            self._cfg.interval_seconds,
            self._cfg.lookback_minutes,
            self._cfg.anomaly_threshold,
        )
        while True:
            try:
                await self._scan_once()
            except Exception as exc:
                logger.warning("Auto-watchdog scan failed: %s", exc)
            await asyncio.sleep(self._cfg.interval_seconds)

    async def _scan_once(self) -> None:
        aggregator = self._aggregator_getter()
        if aggregator is None:
            return
        for service in _load_services(settings.prometheus_config_path):
            request = QueryRequest(
                target=service,
                namespace="default",
                lookback_minutes=self._cfg.lookback_minutes,
                include_rca=False,
            )
            result = await aggregator.query(request)
            score = _score(result)
            if score < self._cfg.anomaly_threshold:
                continue
            alert = {
                "id": f"{service}-{int(datetime.now(tz=timezone.utc).timestamp())}",
                "service": service,
                "score": round(score, 3),
                "severity": _severity(score),
                "summary": _summary(result),
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            self._alerts.append(alert)
            self._alerts = self._alerts[-300:]
            channels = await self._notifier.notify(
                service=service,
                severity=alert["severity"],
                summary=alert["summary"],
                details=f"correlations={len(result.correlations)} errors={result.logs.error_count}",
            )
            if channels:
                alert["channels"] = channels


def _load_services(prometheus_path: str) -> list[str]:
    path = Path(prometheus_path)
    if not path.exists():
        return []
    with path.open() as fh:
        cfg = yaml.safe_load(fh) or {}
    services: list[str] = []
    for job in cfg.get("scrape_configs", []):
        name = job.get("job_name")
        if not name or name in {"prometheus", "loki", "jaeger", "promtail", "aggregator", "node", "node-exporter"}:
            continue
        services.append(name)
    return services


def _score(result: UnifiedResult) -> float:
    score = 0.0
    if result.logs.total_lines:
        score += min(0.5, result.logs.error_count / max(1, result.logs.total_lines))
    if result.traces.p99_duration_ms:
        score += min(0.3, result.traces.p99_duration_ms / 5000.0)
    if result.correlations:
        score += min(0.3, len(result.correlations) * 0.1)
    return min(1.0, score)


def _severity(score: float) -> str:
    if score >= 0.8:
        return "error"
    if score >= 0.5:
        return "warn"
    return "info"


def _summary(result: UnifiedResult) -> str:
    if result.correlations:
        return result.correlations[0].description
    if result.logs.error_count > 0:
        return f"{result.logs.error_count} error logs in window"
    if result.traces.p99_duration_ms:
        return f"p99 trace latency {result.traces.p99_duration_ms:.0f} ms"
    return "Anomaly score threshold exceeded"
