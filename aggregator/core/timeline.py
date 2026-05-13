from __future__ import annotations

from datetime import datetime, timezone

from aggregator.models.result import CorrelationEvent, TimelineEvent
from aggregator.models.signals import LogsSignal, MetricsSignal, Severity, TracesSignal


def build_timeline(
    *,
    metrics: MetricsSignal,
    logs: LogsSignal,
    traces: TracesSignal,
    correlations: list[CorrelationEvent],
    window_start: datetime,
) -> list[TimelineEvent]:
    """Build a causal timeline from timestamped signals in the current window."""
    events: list[TimelineEvent] = []

    metric_event = _metric_spike_event(metrics)
    if metric_event:
        events.append(_with_offset(metric_event, window_start))

    log_event = _log_burst_event(logs)
    if log_event:
        events.append(_with_offset(log_event, window_start))

    trace_event = _trace_latency_event(traces)
    if trace_event:
        events.append(_with_offset(trace_event, window_start))

    for event in correlations:
        ts = event.timestamp or _fallback_now(window_start)
        events.append(
            _with_offset(
                TimelineEvent(
                    timestamp=ts,
                    source="correlation",
                    severity=event.severity,
                    title=event.kind.replace("_", " ").title(),
                    detail=event.description,
                ),
                window_start,
            )
        )

    events.sort(key=lambda e: e.timestamp)
    return events


def _metric_spike_event(metrics: MetricsSignal) -> TimelineEvent | None:
    for series in metrics.series:
        if not series.samples:
            continue
        peak_sample = max(
            (s for s in series.samples if s.value is not None),
            key=lambda s: s.value,
            default=None,
        )
        if not peak_sample:
            continue
        return TimelineEvent(
            timestamp=_as_utc(peak_sample.timestamp),
            source="metric",
            severity="warn",
            title=f"{series.name} peak",
            detail=f"Peak value {peak_sample.value:.4g}",
        )
    return None


def _log_burst_event(logs: LogsSignal) -> TimelineEvent | None:
    if not logs.lines:
        return None
    error_lines = [l for l in logs.lines if l.severity in (Severity.ERROR, Severity.CRITICAL)]
    line = error_lines[0] if error_lines else logs.lines[0]
    sev = "error" if error_lines else "info"
    return TimelineEvent(
        timestamp=_as_utc(line.timestamp),
        source="log",
        severity=sev,
        title="Error log burst" if error_lines else "Log activity",
        detail=line.message[:180],
    )


def _trace_latency_event(traces: TracesSignal) -> TimelineEvent | None:
    if not traces.traces:
        return None
    root_times = []
    for trace in traces.traces:
        root = trace.root_span
        if root:
            root_times.append((_as_utc(root.start_time), trace.duration_ms, trace.trace_id))
    if not root_times:
        return None
    ts, duration_ms, trace_id = max(root_times, key=lambda item: item[1])
    return TimelineEvent(
        timestamp=ts,
        source="trace",
        severity="warn" if duration_ms >= 1000 else "info",
        title="Slow trace observed",
        detail=f"trace={trace_id} duration={duration_ms:.0f} ms",
    )


def _with_offset(event: TimelineEvent, window_start: datetime) -> TimelineEvent:
    start = _as_utc(window_start)
    ts = _as_utc(event.timestamp)
    event.offset_seconds = max(0.0, (ts - start).total_seconds())
    event.timestamp = ts
    return event


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fallback_now(window_start: datetime) -> datetime:
    return _as_utc(window_start)
