"""
Unit tests for SignalAggregator and Correlator.

Uses pytest-asyncio for async tests and unittest.mock to stub clients.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from aggregator.core.aggregator import SignalAggregator
from aggregator.core.correlator import Correlator
from aggregator.models.query import QueryRequest, TimeWindow
from aggregator.models.signals import (
    LogLine,
    LogsSignal,
    MetricSample,
    MetricSeries,
    MetricsSignal,
    Severity,
    Span,
    Trace,
    TracesSignal,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_window() -> TimeWindow:
    return TimeWindow.from_lookback(30)


def make_metrics(error_rate: float = 0.0, has_restart: bool = False) -> MetricsSignal:
    now = datetime.now(tz=timezone.utc)
    series = [
        MetricSeries(
            name="http_error_rate",
            samples=[MetricSample(timestamp=now, value=error_rate)],
        )
    ]
    if has_restart:
        series.append(
            MetricSeries(
                name="restart_count",
                samples=[MetricSample(timestamp=now, value=1.0)],
            )
        )
    return MetricsSignal(series=series)


def make_logs(error_count: int = 0, total: int = 10) -> LogsSignal:
    lines: list[LogLine] = []
    for i in range(total):
        severity = Severity.ERROR if i < error_count else Severity.INFO
        lines.append(
            LogLine(
                timestamp=datetime.now(tz=timezone.utc),
                message=f"log line {i}",
                severity=severity,
            )
        )
    signal = LogsSignal(lines=lines)
    signal.compute_counts()
    return signal


def make_traces(count: int = 1, has_errors: bool = False, duration_ms: float = 100.0) -> TracesSignal:
    traces = []
    for i in range(count):
        span = Span(
            trace_id=f"trace{i:04d}",
            span_id=f"span{i:04d}",
            operation_name="GET /api",
            service_name="my-api",
            start_time=datetime.now(tz=timezone.utc),
            duration_us=int(duration_ms * 1000),
            is_error=has_errors,
        )
        traces.append(Trace(trace_id=f"trace{i:04d}", spans=[span]))
    signal = TracesSignal(traces=traces)
    signal.compute_stats()
    return signal


# ---------------------------------------------------------------------------
# Correlator unit tests
# ---------------------------------------------------------------------------


class TestCorrelator:
    def setup_method(self) -> None:
        self.correlator = Correlator()

    def test_no_events_when_healthy(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(error_rate=0.0),
            logs=make_logs(error_count=0),
            traces=make_traces(count=1, duration_ms=100.0),
        )
        assert events == []

    def test_detects_error_rate_spike(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(error_rate=0.05),
            logs=make_logs(),
            traces=make_traces(),
        )
        kinds = {e.kind for e in events}
        assert "error_spike" in kinds

    def test_detects_restart(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(has_restart=True),
            logs=make_logs(),
            traces=make_traces(),
        )
        kinds = {e.kind for e in events}
        assert "container_restart" in kinds

    def test_detects_log_error_burst(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(),
            logs=make_logs(error_count=8, total=10),
            traces=make_traces(),
        )
        kinds = {e.kind for e in events}
        assert "log_error_burst" in kinds

    def test_cross_correlation_errors_and_logs(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(error_rate=0.05),
            logs=make_logs(error_count=5, total=10),
            traces=make_traces(),
        )
        kinds = {e.kind for e in events}
        assert "error_metric_log_correlation" in kinds

    def test_events_sorted_by_severity(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(error_rate=0.05),
            logs=make_logs(error_count=9, total=10),
            traces=make_traces(duration_ms=2000.0),
        )
        severities = [e.severity for e in events]
        # errors should come first
        error_positions = [i for i, s in enumerate(severities) if s == "error"]
        warn_positions = [i for i, s in enumerate(severities) if s == "warn"]
        if error_positions and warn_positions:
            assert max(error_positions) < min(warn_positions) + len(error_positions)


# ---------------------------------------------------------------------------
# SignalAggregator integration tests (with mocked clients)
# ---------------------------------------------------------------------------


class TestSignalAggregator:
    def _make_aggregator(
        self,
        metrics: MetricsSignal | None = None,
        logs: LogsSignal | None = None,
        traces: TracesSignal | None = None,
    ) -> SignalAggregator:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=metrics or make_metrics())
        prometheus.close = AsyncMock()

        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=logs or make_logs())
        loki.close = AsyncMock()

        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(return_value=traces or make_traces())
        jaeger.close = AsyncMock()

        return SignalAggregator(prometheus=prometheus, loki=loki, jaeger=jaeger)

    @pytest.mark.asyncio
    async def test_basic_query_returns_unified_result(self) -> None:
        agg = self._make_aggregator()
        request = QueryRequest(target="my-api", namespace="default", lookback_minutes=30)
        result = await agg.query(request)

        assert result.meta.target == "my-api"
        assert result.meta.namespace == "default"
        assert result.metrics.series
        assert result.logs.lines
        assert result.traces.traces

    @pytest.mark.asyncio
    async def test_backend_error_does_not_abort_query(self) -> None:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(side_effect=RuntimeError("Prometheus down"))
        prometheus.close = AsyncMock()

        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs())
        loki.close = AsyncMock()

        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(return_value=make_traces())
        jaeger.close = AsyncMock()

        agg = SignalAggregator(prometheus=prometheus, loki=loki, jaeger=jaeger)
        request = QueryRequest(target="my-api")
        result = await agg.query(request)

        assert result.metrics.error is not None
        assert "Prometheus down" in result.metrics.error
        assert result.logs.lines  # logs still present

    @pytest.mark.asyncio
    async def test_include_flags_skip_backends(self) -> None:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=make_metrics())
        prometheus.close = AsyncMock()

        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs())
        loki.close = AsyncMock()

        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(return_value=make_traces())
        jaeger.close = AsyncMock()

        agg = SignalAggregator(prometheus=prometheus, loki=loki, jaeger=jaeger)
        request = QueryRequest(target="my-api", include_metrics=False, include_traces=False)
        await agg.query(request)

        prometheus.query_metrics.assert_not_called()
        jaeger.query_traces.assert_not_called()
        loki.query_logs.assert_called_once()
