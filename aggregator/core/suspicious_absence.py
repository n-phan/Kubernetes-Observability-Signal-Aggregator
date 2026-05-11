"""
Suspicious absence detector.

These rules surface cases where missing telemetry is itself worth
investigating, even when the usual error/latency rules stay quiet.
"""
from __future__ import annotations

from aggregator.models.result import CorrelationEvent
from aggregator.models.signals import LogsSignal, MetricsSignal, TracesSignal

SUSPICIOUS_ABSENCE_KINDS = frozenset(
    {
        "metrics_unavailable",
        "logs_unavailable",
        "traces_unavailable",
        "traffic_without_logs",
        "traffic_without_traces",
        "activity_without_metrics",
    }
)

REQUEST_TRAFFIC_METRIC = "http_requests_per_second"


class SuspiciousAbsenceDetector:
    """Detect telemetry gaps that should trigger a low-confidence RCA."""

    def detect(
        self,
        metrics: MetricsSignal,
        logs: LogsSignal,
        traces: TracesSignal,
        *,
        include_metrics: bool = True,
        include_logs: bool = True,
        include_traces: bool = True,
    ) -> list[CorrelationEvent]:
        events: list[CorrelationEvent] = []

        if include_metrics and metrics.error:
            events.append(
                CorrelationEvent(
                    kind="metrics_unavailable",
                    description=(
                        "Prometheus metrics were unavailable for the incident window; "
                        "RCA evidence is incomplete."
                    ),
                    severity="error",
                    related_metric=REQUEST_TRAFFIC_METRIC,
                    confidence=0.9,
                )
            )
        if include_logs and logs.error:
            events.append(
                CorrelationEvent(
                    kind="logs_unavailable",
                    description=(
                        "Loki logs were unavailable for the incident window; absence of "
                        "log errors cannot be treated as service health."
                    ),
                    severity="error",
                    confidence=0.9,
                )
            )
        if include_traces and traces.error:
            events.append(
                CorrelationEvent(
                    kind="traces_unavailable",
                    description=(
                        "Jaeger traces were unavailable for the incident window; absence "
                        "of error spans cannot be treated as service health."
                    ),
                    severity="error",
                    confidence=0.9,
                )
            )

        has_traffic = _has_request_traffic(metrics)
        if (
            include_metrics
            and include_logs
            and has_traffic
            and not logs.error
            and logs.total_lines == 0
        ):
            events.append(
                CorrelationEvent(
                    kind="traffic_without_logs",
                    description=(
                        "Prometheus shows request traffic, but Loki returned zero log "
                        "lines for the same service and window."
                    ),
                    severity="warn",
                    related_metric=REQUEST_TRAFFIC_METRIC,
                    confidence=0.75,
                )
            )
        if (
            include_metrics
            and include_traces
            and has_traffic
            and not traces.error
            and not traces.traces
        ):
            events.append(
                CorrelationEvent(
                    kind="traffic_without_traces",
                    description=(
                        "Prometheus shows request traffic, but Jaeger returned zero "
                        "traces for the same service and window."
                    ),
                    severity="warn",
                    related_metric=REQUEST_TRAFFIC_METRIC,
                    confidence=0.75,
                )
            )

        has_observed_activity = (
            (include_logs and not logs.error and logs.total_lines > 0)
            or (include_traces and not traces.error and bool(traces.traces))
        )
        if include_metrics and not metrics.error and not metrics.series and has_observed_activity:
            events.append(
                CorrelationEvent(
                    kind="activity_without_metrics",
                    description=(
                        "Logs or traces show service activity, but Prometheus returned "
                        "no metric series for the same service and window."
                    ),
                    severity="warn",
                    confidence=0.75,
                )
            )

        return events


def is_suspicious_absence_event(event: CorrelationEvent) -> bool:
    return event.kind in SUSPICIOUS_ABSENCE_KINDS


def _has_request_traffic(metrics: MetricsSignal) -> bool:
    for series in metrics.series:
        if series.name != REQUEST_TRAFFIC_METRIC:
            continue
        latest = series.latest_value or 0.0
        peak = series.peak_value or 0.0
        if latest > 0 or peak > 0:
            return True
    return False
