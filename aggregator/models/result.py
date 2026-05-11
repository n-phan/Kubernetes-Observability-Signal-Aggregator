from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from aggregator.models.signals import MetricsSignal, LogsSignal, TracesSignal
from aggregator.models.rca import RCAResult


class CorrelationEvent(BaseModel):
    """
    A detected correlation between signals — e.g. an error spike
    that coincides with a log burst and slow traces.
    """

    correlation_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    kind: str  # e.g. "error_spike", "latency_spike", "log_burst"
    description: str
    severity: str = "info"  # info | warn | error
    timestamp: datetime | None = None
    related_metric: str | None = None
    related_log_sample: str | None = None
    related_trace_id: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class QueryMeta(BaseModel):
    """Metadata attached to every query result — identity, time window, and timing."""

    query_id: str = Field(default_factory=lambda: str(uuid4()))
    target: str
    namespace: str
    window_start: datetime
    window_end: datetime
    executed_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    total_duration_ms: float = 0.0


class HistoryOccurrence(BaseModel):
    """One past query record that matched the current incident signature."""

    created_at: datetime
    rca_summary: str | None = None
    rca_confidence: float | None = None


class RecurrenceInfo(BaseModel):
    """How often (and when) this incident signature has been seen before."""

    count: int = 0  # number of PRIOR matching records (0 = a new failure mode)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    occurrences: list[HistoryOccurrence] = Field(default_factory=list)  # most recent first, capped


class HistoryInfo(BaseModel):
    """Attached to a query result: its incident signature and prior recurrence."""

    signature: str
    recurrence: RecurrenceInfo = Field(default_factory=RecurrenceInfo)


class UnifiedResult(BaseModel):
    """
    The top-level output of the aggregator — everything in one place.
    Suitable for both JSON API responses and CLI rendering.
    """

    meta: QueryMeta
    metrics: MetricsSignal
    logs: LogsSignal
    traces: TracesSignal
    correlations: list[CorrelationEvent] = Field(default_factory=list)
    rca: RCAResult = Field(default_factory=RCAResult)
    history: HistoryInfo | None = None  # populated only for notable queries

    @property
    def has_any_errors(self) -> bool:
        return any(
            [
                self.metrics.error is not None,
                self.logs.error is not None,
                self.traces.error is not None,
            ]
        )

    @property
    def summary_line(self) -> str:
        parts = []
        if self.metrics.series:
            parts.append(f"{len(self.metrics.series)} metric series")
        if self.logs.total_lines:
            parts.append(f"{self.logs.total_lines} log lines ({self.logs.error_count} errors)")
        if self.traces.traces:
            parts.append(f"{len(self.traces.traces)} traces")
        if self.correlations:
            parts.append(f"{len(self.correlations)} correlation(s)")
        return ", ".join(parts) if parts else "no data"
