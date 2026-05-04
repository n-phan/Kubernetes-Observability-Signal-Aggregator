"""
Unit tests for SignalAggregator, Correlator, and related helpers.

Uses pytest-asyncio for async tests and unittest.mock to stub clients.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from aggregator.clients.loki import _group_multiline, _severity_from_message
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
        # All "error" events must come before any "warn" events
        error_positions = [i for i, s in enumerate(severities) if s == "error"]
        warn_positions = [i for i, s in enumerate(severities) if s == "warn"]
        if error_positions and warn_positions:
            assert max(error_positions) < min(warn_positions)


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


# ---------------------------------------------------------------------------
# TimeWindow validation tests
# ---------------------------------------------------------------------------


class TestTimeWindow:
    def test_from_lookback_produces_valid_window(self) -> None:
        window = TimeWindow.from_lookback(30)
        assert window.start < window.end
        assert abs(window.duration_seconds - 1800) < 5  # allow 5 s clock drift

    def test_validates_start_before_end(self) -> None:
        now = datetime.now(tz=timezone.utc)
        with pytest.raises(ValueError, match="start must be before end"):
            TimeWindow(start=now, end=now)

    def test_resolve_window_uses_explicit_range(self) -> None:
        req = QueryRequest(
            target="svc",
            start="2024-01-01T10:00:00",
            end="2024-01-01T11:00:00",
        )
        window = req.resolve_window()
        assert window.duration_seconds == 3600.0

    def test_resolve_window_falls_back_to_default_lookback(self) -> None:
        req = QueryRequest(target="svc")
        window = req.resolve_window(default_lookback_minutes=15)
        assert abs(window.duration_seconds - 900) < 5

    def test_lookback_and_range_mutually_exclusive(self) -> None:
        with pytest.raises(ValueError):
            QueryRequest(
                target="svc",
                lookback_minutes=30,
                start="2024-01-01T10:00:00",
                end="2024-01-01T11:00:00",
            )


# ---------------------------------------------------------------------------
# Loki helper tests — _group_multiline and _severity_from_message
# ---------------------------------------------------------------------------


class TestLokiHelpers:
    def _log(self, message: str, severity: Severity = Severity.ERROR) -> LogLine:
        return LogLine(
            timestamp=datetime.now(tz=timezone.utc),
            message=message,
            severity=severity,
        )

    def test_severity_from_message_extracts_level(self) -> None:
        sev, body = _severity_from_message("ERROR myservice connection refused")
        assert sev == Severity.ERROR
        assert "connection refused" in body

    def test_severity_from_message_unknown_passthrough(self) -> None:
        sev, body = _severity_from_message("something without a level prefix")
        assert sev == Severity.UNKNOWN
        assert body == "something without a level prefix"

    def test_group_multiline_merges_traceback_into_error(self) -> None:
        from datetime import timedelta
        now = datetime.now(tz=timezone.utc)
        lines = [
            LogLine(timestamp=now, message="ValueError: bad input", severity=Severity.ERROR),
            LogLine(timestamp=now + timedelta(milliseconds=50), message='  File "app.py", line 10, in handler', severity=Severity.UNKNOWN),
            LogLine(timestamp=now + timedelta(milliseconds=100), message="  x = int(val)", severity=Severity.UNKNOWN),
            LogLine(timestamp=now + timedelta(seconds=5), message="INFO: all good", severity=Severity.INFO),
        ]
        grouped = _group_multiline(lines)
        # First entry should have merged the three first lines; INFO stays separate
        assert len(grouped) == 2
        assert "File" in grouped[0].message
        assert grouped[1].severity == Severity.INFO

    def test_group_multiline_does_not_merge_distant_lines(self) -> None:
        from datetime import timedelta
        now = datetime.now(tz=timezone.utc)
        lines = [
            LogLine(timestamp=now, message="ValueError: bad input", severity=Severity.ERROR),
            # More than 1 second later — should NOT be merged
            LogLine(timestamp=now + timedelta(seconds=2), message='  File "app.py", line 10', severity=Severity.UNKNOWN),
        ]
        grouped = _group_multiline(lines)
        assert len(grouped) == 2

    def test_group_multiline_empty_input(self) -> None:
        assert _group_multiline([]) == []
