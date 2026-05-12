from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class Severity(StrEnum):
    UNKNOWN = "unknown"
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Metrics (Prometheus)
# ---------------------------------------------------------------------------


class MetricSample(BaseModel):
    """A single time-series sample."""

    timestamp: datetime
    value: float | None


class MetricSeries(BaseModel):
    """A named metric with its labels and samples."""

    name: str
    labels: dict[str, str] = Field(default_factory=dict)
    samples: list[MetricSample] = Field(default_factory=list)

    @computed_field
    @property
    def latest_value(self) -> float | None:
        if not self.samples:
            return None
        latest = max(self.samples, key=lambda s: s.timestamp)
        return latest.value

    @computed_field
    @property
    def peak_value(self) -> float | None:
        values = [sample.value for sample in self.samples if sample.value is not None]
        if not values:
            return None
        return max(values)


class MetricsSignal(BaseModel):
    """Aggregated metrics result for a target."""

    series: list[MetricSeries] = Field(default_factory=list)
    query_duration_ms: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Logs (Loki)
# ---------------------------------------------------------------------------


class LogLine(BaseModel):
    """A single log entry."""

    timestamp: datetime
    message: str
    severity: Severity = Severity.UNKNOWN
    labels: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_loki_entry(
        cls,
        ts_ns: str,
        message: str,
        labels: dict[str, str],
    ) -> "LogLine":
        ts = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc)
        raw_level = labels.get("level", labels.get("severity", "")).lower()
        severity = _parse_severity(raw_level)
        return cls(timestamp=ts, message=message, severity=severity, labels=labels)


class LogsSignal(BaseModel):
    """Aggregated log result for a target."""

    lines: list[LogLine] = Field(default_factory=list)
    total_lines: int = 0
    error_count: int = 0
    warn_count: int = 0
    query_duration_ms: float = 0.0
    error: str | None = None

    def compute_counts(self) -> None:
        """Populate error_count, warn_count, and total_lines from self.lines."""
        self.error_count = sum(
            1 for line in self.lines if line.severity in (Severity.ERROR, Severity.CRITICAL)
        )
        self.warn_count = sum(1 for line in self.lines if line.severity == Severity.WARN)
        self.total_lines = len(self.lines)


# ---------------------------------------------------------------------------
# Traces (Jaeger)
# ---------------------------------------------------------------------------


class SpanReference(BaseModel):
    trace_id: str
    span_id: str
    ref_type: str = "CHILD_OF"


class Span(BaseModel):
    """A single Jaeger span."""

    trace_id: str
    span_id: str
    operation_name: str
    service_name: str
    start_time: datetime
    duration_us: int  # microseconds — stored as-is, exposed as duration_ms
    tags: dict[str, str] = Field(default_factory=dict)
    is_error: bool = False
    references: list[SpanReference] = Field(default_factory=list)

    @computed_field
    @property
    def duration_ms(self) -> float:
        """Duration in milliseconds — serialized to JSON for the frontend."""
        return self.duration_us / 1000.0


class Trace(BaseModel):
    """A complete distributed trace."""

    trace_id: str
    spans: list[Span] = Field(default_factory=list)
    root_service: str | None = None  # set by JaegerClient from the root span

    @property
    def root_span(self) -> Span | None:
        roots = [s for s in self.spans if not s.references]
        return roots[0] if roots else (self.spans[0] if self.spans else None)

    @computed_field
    @property
    def duration_ms(self) -> float:
        """Total wall-clock duration of the trace in milliseconds."""
        if not self.spans:
            return 0.0
        start = min(s.start_time for s in self.spans)
        end_times = [s.start_time.timestamp() * 1000 + s.duration_ms for s in self.spans]
        return max(end_times) - start.timestamp() * 1000

    @property
    def has_errors(self) -> bool:
        return any(s.is_error for s in self.spans)


class TracesSignal(BaseModel):
    """Aggregated traces result for a target."""

    traces: list[Trace] = Field(default_factory=list)
    error_trace_count: int = 0
    p99_duration_ms: float | None = None
    query_duration_ms: float = 0.0
    error: str | None = None

    def compute_stats(self) -> None:
        """Populate error_trace_count and p99_duration_ms from self.traces."""
        self.error_trace_count = sum(1 for t in self.traces if t.has_errors)
        durations = sorted(t.duration_ms for t in self.traces)
        if durations:
            idx = max(0, int(len(durations) * 0.99) - 1)
            self.p99_duration_ms = durations[idx]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_severity(raw: str) -> Severity:
    mapping = {
        "debug": Severity.DEBUG,
        "trace": Severity.DEBUG,
        "info": Severity.INFO,
        "information": Severity.INFO,
        "warn": Severity.WARN,
        "warning": Severity.WARN,
        "error": Severity.ERROR,
        "err": Severity.ERROR,
        "critical": Severity.CRITICAL,
        "crit": Severity.CRITICAL,
        "fatal": Severity.CRITICAL,
    }
    return mapping.get(raw, Severity.UNKNOWN)
