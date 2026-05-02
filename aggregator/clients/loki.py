import logging
from datetime import datetime

from aggregator.clients.base import BaseObservabilityClient, ObservabilityClientError
from aggregator.config import settings
from aggregator.models.signals import LogLine, LogsSignal

logger = logging.getLogger(__name__)


class LokiClient(BaseObservabilityClient):
    backend_name = "loki"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or settings.loki_url)

    async def query_logs(
        self,
        target: str,
        namespace: str,
        start: datetime,
        end: datetime,
        limit: int | None = None,
    ) -> LogsSignal:
        """
        Query Loki for log streams matching the target pod/service.

        Tries a pod-level selector first; falls back to app-label selector.
        """
        limit = limit or settings.max_log_lines

        # Try two different label strategies; first match wins.
        selectors = [
            f'{{job="{target}"}}',
            f'{{service=~".*{target}.*"}}',
        ]

        for selector in selectors:
            try:
                lines, duration_ms = await self._query_range(
                    selector=selector,
                    start=start,
                    end=end,
                    limit=limit,
                )
                if lines:
                    signal = LogsSignal(lines=lines, query_duration_ms=duration_ms)
                    signal.compute_counts()
                    return signal
            except ObservabilityClientError as exc:
                logger.warning("Loki selector '%s' failed: %s", selector, exc)

        # All selectors returned empty — still a valid (empty) result.
        return LogsSignal()

    async def _query_range(
        self,
        selector: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> tuple[list[LogLine], float]:
        """Execute a Loki query_range call and parse the response into LogLines."""
        data, duration_ms = await self._get(
            "/loki/api/v1/query_range",
            params={
                "query": selector,
                "start": _to_ns(start),
                "end": _to_ns(end),
                "limit": limit,
                "direction": "backward",
            },
        )

        if data.get("status") != "success":
            raise ObservabilityClientError(
                self.backend_name,
                f"Non-success status: {data.get('status')}",
            )

        lines: list[LogLine] = []
        for stream in data.get("data", {}).get("result", []):
            labels: dict[str, str] = stream.get("stream", {})
            for ts_ns, message in stream.get("values", []):
                lines.append(LogLine.from_loki_entry(ts_ns, message, dict(labels)))

        # Sort chronologically (Loki returns newest-first by default)
        lines.sort(key=lambda l: l.timestamp)
        return lines, duration_ms


def _to_ns(dt: datetime) -> str:
    """Convert a datetime to nanosecond epoch string for Loki API."""
    return str(int(dt.timestamp() * 1_000_000_000))
