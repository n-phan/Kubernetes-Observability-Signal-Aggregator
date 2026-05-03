from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field, model_validator


class TimeWindow(BaseModel):
    """Absolute time window derived from a lookback duration or explicit start/end."""

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_order(self) -> "TimeWindow":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @classmethod
    def from_lookback(cls, minutes: int) -> "TimeWindow":
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(minutes=minutes)
        return cls(start=start, end=end)

    @classmethod
    def from_iso(cls, start: str, end: str) -> "TimeWindow":
        return cls(
            start=datetime.fromisoformat(start),
            end=datetime.fromisoformat(end),
        )


class QueryRequest(BaseModel):
    """Top-level query input accepted by both the CLI and REST API."""

    target: str = Field(
        ...,
        description="Kubernetes pod name, deployment name, or service name",
        examples=["my-api", "payment-service", "my-api-6d8b9f-xkpqr"],
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace",
    )
    lookback_minutes: int | None = Field(
        default=None,
        ge=1,
        le=10080,  # 1 week max
        description="Lookback window in minutes. Mutually exclusive with start/end.",
    )
    start: str | None = Field(
        default=None,
        description="ISO 8601 start timestamp (UTC). Use with end.",
    )
    end: str | None = Field(
        default=None,
        description="ISO 8601 end timestamp (UTC). Use with start.",
    )

    # Which backends to query (all enabled by default)
    include_metrics: bool = True
    include_logs: bool = True
    include_traces: bool = True

    # RCA is opt-in — skipped by default to save API tokens
    include_rca: bool = False

    @model_validator(mode="after")
    def validate_time_spec(self) -> "QueryRequest":
        has_lookback = self.lookback_minutes is not None
        has_range = self.start is not None or self.end is not None

        if has_lookback and has_range:
            raise ValueError("Specify either lookback_minutes OR start+end, not both")

        if has_range and not (self.start and self.end):
            raise ValueError("Both start and end must be provided together")

        return self

    def resolve_window(self, default_lookback_minutes: int = 30) -> TimeWindow:
        """Resolve the query's time spec into an absolute TimeWindow."""
        if self.start and self.end:
            return TimeWindow.from_iso(self.start, self.end)
        lookback = self.lookback_minutes or default_lookback_minutes
        return TimeWindow.from_lookback(lookback)