import logging
from datetime import datetime

from aggregator.clients.base import BaseObservabilityClient, ObservabilityClientError
from aggregator.config import settings
from aggregator.models.signals import MetricSample, MetricSeries, MetricsSignal

logger = logging.getLogger(__name__)

# PromQL expressions to run for every query.
# Each tuple is (metric_name, promql_template).
# {target} and {namespace} are substituted at query time.
METRIC_QUERIES: list[tuple[str, str]] = [
    (
        "cpu_usage",
        'rate(process_cpu_seconds_total{{job="{target}"}}[5m])',
    ),
    (
        "memory_bytes",
        'process_resident_memory_bytes{{job="{target}"}}',
    ),
    (
        "http_requests_per_second",
        'rate(http_requests_total{{job="{target}"}}[5m])',
    ),
    (
        "http_error_rate",
        'rate(http_requests_total{{job="{target}",status=~"5.."}}[5m])',
    ),
    (
        "http_latency_p99",
        'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{job="{target}"}}[5m]))',
    ),
]


class PrometheusClient(BaseObservabilityClient):
    backend_name = "prometheus"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or settings.prometheus_url)

    async def query_metrics(
        self,
        target: str,
        namespace: str,
        start: datetime,
        end: datetime,
        step: str = "30s",
    ) -> MetricsSignal:
        """
        Run all configured PromQL range queries in sequence and return
        a unified MetricsSignal.

        Note: queries run sequentially here for simplicity. For very
        high cardinality targets you may want to run them concurrently
        with asyncio.gather.
        """
        all_series: list[MetricSeries] = []
        total_duration = 0.0

        for metric_name, query_template in METRIC_QUERIES:
            query = query_template.format(target=target, namespace=namespace)
            try:
                series, duration_ms = await self._range_query(
                    query=query,
                    start=start,
                    end=end,
                    step=step,
                )
                total_duration += duration_ms
                for s in series:
                    s.name = metric_name  # override with our friendly name
                    all_series.append(s)
            except ObservabilityClientError as exc:
                logger.warning("Skipping metric %s: %s", metric_name, exc)

        return MetricsSignal(series=all_series, query_duration_ms=total_duration)

    async def _range_query(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str,
    ) -> tuple[list[MetricSeries], float]:
        """Execute a single PromQL range_query and parse results."""
        data, duration_ms = await self._get(
            "/api/v1/query_range",
            params={
                "query": query,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
            },
        )

        if data.get("status") != "success":
            raise ObservabilityClientError(
                self.backend_name,
                f"Non-success status: {data.get('status')} — {data.get('error', '')}",
            )

        result_type = data["data"]["resultType"]
        if result_type != "matrix":
            raise ObservabilityClientError(
                self.backend_name,
                f"Expected matrix result, got {result_type}",
            )

        series_list: list[MetricSeries] = []
        for item in data["data"]["result"]:
            metric_labels: dict[str, str] = item.get("metric", {})
            name = metric_labels.pop("__name__", "unknown")
            samples = [
                MetricSample(timestamp=datetime.fromtimestamp(float(ts)), value=float(val))
                for ts, val in item.get("values", [])
            ]
            series_list.append(MetricSeries(name=name, labels=metric_labels, samples=samples))

        return series_list, duration_ms
