"""Shared RCA gating rules."""
from __future__ import annotations

from aggregator.models.result import UnifiedResult
from aggregator.models.signals import MetricsSignal

LATENCY_ANOMALY_THRESHOLD_S = 1.0
LATENCY_P99_THRESHOLD_MS = 1_000
LATENCY_METRIC_KEYWORDS = ("latency", "duration", "p99", "p95")


def should_run_rca(result: UnifiedResult) -> bool:
    """Return True when the result contains evidence worth RCA analysis."""
    return (
        result.logs.error_count > 0
        or any(event.severity == "error" for event in result.correlations)
        or result.traces.error_trace_count > 0
        or _has_slow_traces(result)
        or has_latency_metric_anomaly(result.metrics)
    )


def has_latency_metric_anomaly(metrics: MetricsSignal) -> bool:
    return any(
        any(keyword in series.name.lower() for keyword in LATENCY_METRIC_KEYWORDS)
        and series.peak_value is not None
        and series.peak_value > LATENCY_ANOMALY_THRESHOLD_S
        for series in metrics.series
    )


def _has_slow_traces(result: UnifiedResult) -> bool:
    return (
        result.traces.p99_duration_ms is not None
        and result.traces.p99_duration_ms > LATENCY_P99_THRESHOLD_MS
    )
