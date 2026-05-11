"""
Incident timeline — show causal ordering of signals.

Extracts timestamps from metrics, logs, and traces, then determines the
sequence of events to answer "did the metric spike cause the error logs,
or vice versa?"
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from aggregator.models.signals import LogsSignal, MetricsSignal, TracesSignal

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """String enum compatible with Python 3.9+"""
    METRIC_SPIKE = "metric_spike"
    LOG_BURST = "log_burst"
    TRACE_ERROR = "trace_error"
    LATENCY_SPIKE = "latency_spike"


class TimelineEvent(BaseModel):
    """A single event in the incident timeline."""

    event_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    timestamp: datetime
    event_type: EventType
    severity: str  # info | warn | error
    summary: str
    details: dict = Field(default_factory=dict)


class IncidentTimeline(BaseModel):
    """Ordered sequence of events in an incident."""

    events: list[TimelineEvent] = Field(default_factory=list)
    earliest_event: datetime | None = None
    latest_event: datetime | None = None
    total_span_seconds: float = 0.0
    dominant_cause: str | None = None  # "metrics" | "logs" | "traces"


def build_timeline(
    metrics: MetricsSignal,
    logs: LogsSignal,
    traces: TracesSignal,
) -> IncidentTimeline:
    """
    Extract all signal events and sort by timestamp to reveal causality.

    Returns an ordered timeline showing which type of event occurred first,
    allowing operators to understand the incident flow.
    """
    events: list[TimelineEvent] = []

    # --- Extract metric events ---
    for series in metrics.series:
        if not series.samples:
            continue

        # Detect spikes: compare peak to baseline
        if len(series.samples) > 1:
            baseline = _percentile([s.value for s in series.samples], 25)
            peak = max(s.value for s in series.samples)

            if peak > baseline * 2:  # 2x baseline = notable spike
                peak_sample = max(series.samples, key=lambda s: s.value)
                events.append(
                    TimelineEvent(
                        timestamp=peak_sample.timestamp,
                        event_type=EventType.METRIC_SPIKE,
                        severity="warn",
                        summary=f"Metric spike in {series.name}",
                        details={
                            "metric_name": series.name,
                            "baseline_value": baseline,
                            "peak_value": peak,
                            "labels": series.labels,
                        },
                    )
                )

    # --- Extract log events ---
    if logs.lines:
        error_logs = [l for l in logs.lines if l.severity.value in ("error", "critical")]
        if error_logs:
            first_error = min(error_logs, key=lambda l: l.timestamp)
            last_error = max(error_logs, key=lambda l: l.timestamp)
            events.append(
                TimelineEvent(
                    timestamp=first_error.timestamp,
                    event_type=EventType.LOG_BURST,
                    severity="error",
                    summary=f"Error logs began ({len(error_logs)} total)",
                    details={
                        "error_count": len(error_logs),
                        "first_error": first_error.message[:100],
                        "last_error": last_error.message[:100],
                    },
                )
            )

    # --- Extract trace events ---
    if traces.traces:
        error_traces = [t for t in traces.traces if t.has_errors]
        if error_traces:
            first_error_trace = min(error_traces, key=lambda t: t.root_span.start_time if t.root_span else t.spans[0].start_time if t.spans else datetime.now())
            events.append(
                TimelineEvent(
                    timestamp=first_error_trace.root_span.start_time if first_error_trace.root_span else first_error_trace.spans[0].start_time if first_error_trace.spans else datetime.now(),
                    event_type=EventType.TRACE_ERROR,
                    severity="error",
                    summary=f"Distributed traces showed errors ({len(error_traces)} total)",
                    details={
                        "error_count": len(error_traces),
                        "first_error_trace_id": first_error_trace.trace_id,
                    },
                )
            )

        # Detect latency spikes
        if traces.traces:
            latencies = [t.duration_ms for t in traces.traces]
            baseline_latency = _percentile(latencies, 50)
            peak_latency = max(latencies)

            if peak_latency > baseline_latency * 3:  # 3x p50 = notable spike
                slow_trace = max(traces.traces, key=lambda t: t.duration_ms)
                events.append(
                    TimelineEvent(
                        timestamp=slow_trace.root_span.start_time if slow_trace.root_span else slow_trace.spans[0].start_time if slow_trace.spans else datetime.now(),
                        event_type=EventType.LATENCY_SPIKE,
                        severity="warn",
                        summary=f"Request latency spike ({peak_latency:.0f}ms)",
                        details={
                            "baseline_ms": baseline_latency,
                            "peak_ms": peak_latency,
                            "trace_id": slow_trace.trace_id,
                        },
                    )
                )

    # --- Sort and compute derived fields ---
    events.sort(key=lambda e: e.timestamp)

    if events:
        dominant_cause = _determine_dominant_cause(events)
    else:
        dominant_cause = None

    return IncidentTimeline(
        events=events,
        earliest_event=events[0].timestamp if events else None,
        latest_event=events[-1].timestamp if events else None,
        total_span_seconds=(
            (events[-1].timestamp - events[0].timestamp).total_seconds()
            if len(events) > 1
            else 0.0
        ),
        dominant_cause=dominant_cause,
    )


def _percentile(values: list[float], p: int) -> float:
    """Compute the p-th percentile of a list (0-100)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * p / 100)
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]


def _determine_dominant_cause(events: list[TimelineEvent]) -> str | None:
    """
    Infer the likely root cause by examining event order.

    Simple heuristic: whichever signal type appears first is likely causal.
    """
    if not events:
        return None

    first_event = events[0]

    if first_event.event_type == EventType.METRIC_SPIKE:
        return "metrics"
    elif first_event.event_type in (EventType.LOG_BURST, EventType.TRACE_ERROR):
        return "logs"
    elif first_event.event_type == EventType.LATENCY_SPIKE:
        return "traces"

    return None
