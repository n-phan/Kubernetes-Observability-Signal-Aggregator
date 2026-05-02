import logging
from datetime import datetime

from aggregator.clients.base import BaseObservabilityClient, ObservabilityClientError
from aggregator.config import settings
from aggregator.models.signals import Span, SpanReference, Trace, TracesSignal

logger = logging.getLogger(__name__)


class JaegerClient(BaseObservabilityClient):
    backend_name = "jaeger"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or settings.jaeger_url)

    async def query_traces(
        self,
        target: str,
        namespace: str,
        start: datetime,
        end: datetime,
        limit: int | None = None,
    ) -> TracesSignal:
        """
        Search Jaeger for traces belonging to a service derived from the target name.

        Jaeger searches by service name. We try the target name directly, then
        fall back to a namespace-prefixed form.
        """
        limit = limit or settings.max_traces
        service_candidates = [target, f"{namespace}/{target}", f"{target}-service"]

        for service in service_candidates:
            try:
                traces, duration_ms = await self._search_traces(
                    service=service,
                    start=start,
                    end=end,
                    limit=limit,
                )
                if traces:
                    signal = TracesSignal(traces=traces, query_duration_ms=duration_ms)
                    signal.compute_stats()
                    return signal
            except ObservabilityClientError as exc:
                logger.warning("Jaeger service '%s' not found: %s", service, exc)

        return TracesSignal()

    async def _search_traces(
        self,
        service: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> tuple[list[Trace], float]:
        """Execute a Jaeger trace search and parse results."""
        data, duration_ms = await self._get(
            "/api/traces",
            params={
                "service": service,
                "start": _to_us(start),
                "end": _to_us(end),
                "limit": limit,
            },
        )

        traces: list[Trace] = []
        for raw_trace in data.get("data", []):
            trace_id = raw_trace.get("traceID", "")
            processes: dict[str, str] = {
                pid: p.get("serviceName", "unknown")
                for pid, p in raw_trace.get("processes", {}).items()
            }

            spans: list[Span] = []
            for raw_span in raw_trace.get("spans", []):
                process_id = raw_span.get("processID", "")
                service_name = processes.get(process_id, "unknown")

                tags = {
                    t["key"]: str(t["value"])
                    for t in raw_span.get("tags", [])
                    if "key" in t and "value" in t
                }
                is_error = tags.get("error", "false").lower() in ("true", "1")

                refs = [
                    SpanReference(
                        trace_id=r["traceID"],
                        span_id=r["spanID"],
                        ref_type=r.get("refType", "CHILD_OF"),
                    )
                    for r in raw_span.get("references", [])
                ]

                start_time_us = raw_span.get("startTime", 0)
                spans.append(
                    Span(
                        trace_id=trace_id,
                        span_id=raw_span.get("spanID", ""),
                        operation_name=raw_span.get("operationName", ""),
                        service_name=service_name,
                        start_time=datetime.fromtimestamp(start_time_us / 1e6),
                        duration_us=raw_span.get("duration", 0),
                        tags=tags,
                        is_error=is_error,
                        references=refs,
                    )
                )

            if spans:
                traces.append(Trace(trace_id=trace_id, spans=spans))

        return traces, duration_ms


def _to_us(dt: datetime) -> int:
    """Convert datetime to microsecond epoch for Jaeger API."""
    return int(dt.timestamp() * 1_000_000)
