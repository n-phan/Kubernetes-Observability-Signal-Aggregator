"""
Core aggregator — the heart of the system.

Accepts a QueryRequest, fans out to all three backends concurrently,
collects results into a UnifiedResult, and runs correlation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from aggregator.clients.jaeger import JaegerClient
from aggregator.clients.loki import LokiClient
from aggregator.clients.prometheus import PrometheusClient
from aggregator.clients.github import GitHubLinker
from aggregator.config import settings
from aggregator.core.correlator import Correlator
from aggregator.core.rca_analyzer import RCAAnalyzer
from aggregator.models.query import QueryRequest
from aggregator.models.result import QueryMeta, UnifiedResult
from aggregator.models.signals import LogsSignal, MetricsSignal, TracesSignal

logger = logging.getLogger(__name__)


async def _skipped(signal):
    """Return a default signal when a backend is disabled via --no-* flag."""
    return signal


class SignalAggregator:
    """
    The main orchestrator.

    Usage:
        aggregator = SignalAggregator()
        result = await aggregator.query(request)
    """

    def __init__(
        self,
        prometheus: PrometheusClient | None = None,
        loki: LokiClient | None = None,
        jaeger: JaegerClient | None = None,
        correlator: Correlator | None = None,
        rca_analyzer: RCAAnalyzer | None = None,
        github_linker: GitHubLinker | None = None,
    ) -> None:
        # Clients are injected for testability; defaults use settings
        self._prometheus = prometheus or PrometheusClient()
        self._loki = loki or LokiClient()
        self._jaeger = jaeger or JaegerClient()
        self._correlator = correlator or Correlator()
        self._rca_analyzer = rca_analyzer or (
            RCAAnalyzer(
                api_key=settings.anthropic_api_key,
                repo=settings.github_repo,
            )
            if settings.rca_enabled
            else None
        )
        self._github_linker = github_linker or (
            GitHubLinker(
                token=settings.github_token,
                repo=settings.github_repo,
                default_branch=settings.github_default_branch,
                path_prefix=settings.github_path_prefix,
            )
            if settings.github_repo
            else None
        )

    async def query(self, request: QueryRequest) -> UnifiedResult:
        """
        Execute a unified observability query.

        All three backends are queried concurrently via asyncio.gather.
        Individual backend failures are caught and represented as
        error fields in the respective signal — they do not abort
        the overall query.
        """
        window = request.resolve_window(settings.default_lookback_minutes)
        t0 = time.monotonic()

        logger.info(
            "Querying target=%s namespace=%s window=[%s → %s]",
            request.target,
            request.namespace,
            window.start.isoformat(),
            window.end.isoformat(),
        )

        # Fan out all three queries concurrently
        metrics_task = (
            self._safe_metrics(request.target, request.namespace, window.start, window.end)
            if request.include_metrics
            else _skipped(MetricsSignal())
        )
        logs_task = (
            self._safe_logs(request.target, request.namespace, window.start, window.end)
            if request.include_logs
            else _skipped(LogsSignal())
        )
        traces_task = (
            self._safe_traces(request.target, request.namespace, window.start, window.end)
            if request.include_traces
            else _skipped(TracesSignal())
        )

        metrics, logs, traces = await asyncio.gather(metrics_task, logs_task, traces_task)

        # Correlate
        correlations = self._correlator.correlate(metrics, logs, traces)

        # Build preliminary result so RCA can read it
        total_ms = (time.monotonic() - t0) * 1000
        result = UnifiedResult(
            meta=QueryMeta(
                target=request.target,
                namespace=request.namespace,
                window_start=window.start,
                window_end=window.end,
                total_duration_ms=total_ms,
            ),
            metrics=metrics,
            logs=logs,
            traces=traces,
            correlations=correlations,
        )

        # RCA — runs only when there are error signals
        if self._rca_analyzer:
            rca = await self._rca_analyzer.analyze(result)
            if self._github_linker and rca.performed:
                rca = await self._github_linker.enrich(rca, logs)
            result.rca = rca

        total_ms = (time.monotonic() - t0) * 1000
        result.meta.total_duration_ms = total_ms

        logger.info(
            "Query complete: %d metric series, %d log lines, %d traces, "
            "%d correlations, rca=%s in %.0f ms",
            len(metrics.series),
            logs.total_lines,
            len(traces.traces),
            len(correlations),
            result.rca.performed,
            total_ms,
        )

        return result

    # ------------------------------------------------------------------
    # Safe wrappers — catch backend errors, return partial results
    # ------------------------------------------------------------------

    async def _safe_metrics(
        self, target: str, namespace: str, start: datetime, end: datetime
    ) -> MetricsSignal:
        try:
            return await self._prometheus.query_metrics(target, namespace, start, end)
        except Exception as exc:
            logger.error("Prometheus query failed: %s", exc)
            return MetricsSignal(error=str(exc))

    async def _safe_logs(
        self, target: str, namespace: str, start: datetime, end: datetime
    ) -> LogsSignal:
        try:
            return await self._loki.query_logs(target, namespace, start, end)
        except Exception as exc:
            logger.error("Loki query failed: %s", exc)
            return LogsSignal(error=str(exc))

    async def _safe_traces(
        self, target: str, namespace: str, start: datetime, end: datetime
    ) -> TracesSignal:
        try:
            return await self._jaeger.query_traces(target, namespace, start, end)
        except Exception as exc:
            logger.error("Jaeger query failed: %s", exc)
            return TracesSignal(error=str(exc))

    async def close(self) -> None:
        """Close all underlying HTTP clients."""
        closers = [
            self._prometheus.close(),
            self._loki.close(),
            self._jaeger.close(),
        ]
        if self._rca_analyzer:
            closers.append(self._rca_analyzer.close())
        if self._github_linker:
            closers.append(self._github_linker.close())
        await asyncio.gather(*closers)

    async def __aenter__(self) -> "SignalAggregator":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
