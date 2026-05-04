"""
Signal correlator.

Detects relationships between metrics, logs, and traces.
Currently rule-based; designed with ML-ready extension hooks.

Extension points are marked with # ML-HOOK comments.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta

from aggregator.models.result import CorrelationEvent
from aggregator.models.signals import (
    LogsSignal,
    MetricSeries,
    MetricsSignal,
    Severity,
    TracesSignal,
)

logger = logging.getLogger(__name__)

# Thresholds — could be loaded from config or tuned by ML
ERROR_RATE_THRESHOLD = 0.01          # > 1 % error rate is noteworthy
LATENCY_P99_THRESHOLD_MS = 1_000     # p99 > 1 s is noteworthy
ERROR_LOG_RATIO_THRESHOLD = 0.05     # > 5 % of logs being errors is noteworthy
RESTART_THRESHOLD = 1                # any restart in window is noteworthy
Z_SCORE_THRESHOLD = 2.0              # z-score for statistical spike detection


class Correlator:
    """
    Produces a list of CorrelationEvents from unified signal data.

    Rule evaluation order:
      1. Individual signal anomalies (spikes, bursts)
      2. Cross-signal correlations (metric spike + log errors in same window)
    """

    def correlate(
        self,
        metrics: MetricsSignal,
        logs: LogsSignal,
        traces: TracesSignal,
    ) -> list[CorrelationEvent]:
        events: list[CorrelationEvent] = []

        # --- Individual signal checks ---
        events.extend(self._check_error_rate(metrics))
        events.extend(self._check_restarts(metrics))
        events.extend(self._check_high_latency(traces))
        events.extend(self._check_log_error_burst(logs))

        # --- Cross-signal correlations ---
        events.extend(self._cross_correlate_errors_and_logs(metrics, logs))
        events.extend(self._cross_correlate_latency_and_traces(metrics, traces))

        # ML-HOOK: replace or augment rule events with model predictions
        # events = self._ml_score(events, metrics, logs, traces)

        return sorted(events, key=lambda e: _severity_rank(e.severity), reverse=True)

    # ------------------------------------------------------------------
    # Individual signal checks
    # ------------------------------------------------------------------

    def _check_error_rate(self, metrics: MetricsSignal) -> list[CorrelationEvent]:
        events: list[CorrelationEvent] = []
        for series in _get_series_by_name(metrics, "http_error_rate"):
            if not series.samples:
                continue
            peak = series.peak_value or 0.0
            if peak > ERROR_RATE_THRESHOLD:
                events.append(
                    CorrelationEvent(
                        kind="error_spike",
                        description=f"HTTP error rate peaked at {peak:.2%}",
                        severity="error",
                        related_metric="http_error_rate",
                        confidence=min(1.0, peak / (ERROR_RATE_THRESHOLD * 10)),
                    )
                )
            # ML-HOOK: statistical z-score spike detection
            z = _z_score_max(series)
            if z and z > Z_SCORE_THRESHOLD:
                events.append(
                    CorrelationEvent(
                        kind="error_rate_anomaly",
                        description=f"Error rate z-score={z:.1f} — statistically unusual",
                        severity="warn",
                        related_metric="http_error_rate",
                        confidence=min(1.0, z / (Z_SCORE_THRESHOLD * 2)),
                    )
                )
        return events

    def _check_restarts(self, metrics: MetricsSignal) -> list[CorrelationEvent]:
        events: list[CorrelationEvent] = []
        for series in _get_series_by_name(metrics, "restart_count"):
            peak = series.peak_value or 0.0
            if peak >= RESTART_THRESHOLD:
                events.append(
                    CorrelationEvent(
                        kind="container_restart",
                        description=f"Container restarted {int(peak)} time(s) in window",
                        severity="error",
                        related_metric="restart_count",
                        confidence=1.0,
                    )
                )
        return events

    def _check_high_latency(self, traces: TracesSignal) -> list[CorrelationEvent]:
        events: list[CorrelationEvent] = []
        if traces.p99_duration_ms and traces.p99_duration_ms > LATENCY_P99_THRESHOLD_MS:
            sample_trace_id = traces.traces[0].trace_id if traces.traces else None
            events.append(
                CorrelationEvent(
                    kind="latency_spike",
                    description=f"Trace p99 latency {traces.p99_duration_ms:.0f} ms exceeds {LATENCY_P99_THRESHOLD_MS} ms",
                    severity="warn",
                    related_trace_id=sample_trace_id,
                    confidence=min(1.0, traces.p99_duration_ms / (LATENCY_P99_THRESHOLD_MS * 5)),
                )
            )
        return events

    def _check_log_error_burst(self, logs: LogsSignal) -> list[CorrelationEvent]:
        events: list[CorrelationEvent] = []
        if not logs.total_lines:
            return events

        ratio = logs.error_count / logs.total_lines
        if ratio > ERROR_LOG_RATIO_THRESHOLD:
            # Find the most recent error message as a sample
            error_lines = [line for line in logs.lines if line.severity in (Severity.ERROR, Severity.CRITICAL)]
            sample = error_lines[-1].message[:120] if error_lines else None

            events.append(
                CorrelationEvent(
                    kind="log_error_burst",
                    description=f"{logs.error_count} error-level log lines ({ratio:.1%} of {logs.total_lines} total)",
                    severity="error" if ratio > 0.2 else "warn",
                    related_log_sample=sample,
                    confidence=min(1.0, ratio / 0.5),
                )
            )
        return events

    # ------------------------------------------------------------------
    # Cross-signal correlations
    # ------------------------------------------------------------------

    def _cross_correlate_errors_and_logs(
        self,
        metrics: MetricsSignal,
        logs: LogsSignal,
    ) -> list[CorrelationEvent]:
        """
        If we see both a metric error rate spike AND a log error burst,
        flag them as co-occurring — useful for incident triage.
        """
        events: list[CorrelationEvent] = []
        has_metric_errors = any(
            (s.peak_value or 0) > ERROR_RATE_THRESHOLD
            for s in _get_series_by_name(metrics, "http_error_rate")
        )
        has_log_errors = logs.error_count > 0

        if has_metric_errors and has_log_errors:
            events.append(
                CorrelationEvent(
                    kind="error_metric_log_correlation",
                    description=(
                        f"HTTP error rate spike and {logs.error_count} error log line(s) "
                        "occurred in the same window — likely the same incident"
                    ),
                    severity="error",
                    confidence=0.85,
                )
            )
        return events

    def _cross_correlate_latency_and_traces(
        self,
        metrics: MetricsSignal,
        traces: TracesSignal,
    ) -> list[CorrelationEvent]:
        """
        If p99 latency is high AND error traces are present, surface together.
        """
        events: list[CorrelationEvent] = []
        high_latency = (
            traces.p99_duration_ms is not None
            and traces.p99_duration_ms > LATENCY_P99_THRESHOLD_MS
        )
        if high_latency and traces.error_trace_count > 0:
            events.append(
                CorrelationEvent(
                    kind="latency_trace_correlation",
                    description=(
                        f"p99={traces.p99_duration_ms:.0f} ms with "
                        f"{traces.error_trace_count} error trace(s) — investigate slow error paths"
                    ),
                    severity="error",
                    confidence=0.9,
                )
            )
        return events


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _get_series_by_name(metrics: MetricsSignal, name: str) -> list[MetricSeries]:
    return [s for s in metrics.series if s.name == name]


def _z_score_max(series: MetricSeries) -> float | None:
    """Return the z-score of the peak value relative to the series mean."""
    values = [s.value for s in series.samples]
    if len(values) < 3:
        return None
    try:
        mean = statistics.mean(values)
        std = statistics.stdev(values)
        if std == 0:
            return None
        return (max(values) - mean) / std
    except statistics.StatisticsError:
        return None


def _severity_rank(severity: str) -> int:
    return {"error": 3, "warn": 2, "info": 1}.get(severity, 0)
