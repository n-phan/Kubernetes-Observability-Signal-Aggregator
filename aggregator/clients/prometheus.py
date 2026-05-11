import logging
from datetime import datetime

from aggregator.clients.base import BaseObservabilityClient, ObservabilityClientError
from aggregator.config import settings
from aggregator.models.signals import MetricSample, MetricSeries, MetricsSignal

logger = logging.getLogger(__name__)

# PromQL expressions to run for every query.
# Each tuple is (metric_name, promql_template). {target} is the job/service name;
# {ns} expands to a namespace label matcher (', namespace="..."') in Kubernetes
# mode, or to an empty string in docker-compose mode (where there is no such label).
METRIC_QUERIES: list[tuple[str, str]] = [
    (
        "cpu_usage",
        'rate(process_cpu_seconds_total{{job="{target}"{ns}}}[5m])',
    ),
    (
        "memory_bytes",
        'process_resident_memory_bytes{{job="{target}"{ns}}}',
    ),
    (
        "http_requests_per_second",
        'rate(http_requests_total{{job="{target}"{ns}}}[5m])',
    ),
    (
        "http_error_rate",
        'rate(http_requests_total{{job="{target}",status=~"5.."{ns}}}[5m])',
    ),
    (
        "http_latency_p99",
        'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{job="{target}"{ns}}}[5m]))',
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

        # In Kubernetes the Prometheus Operator adds a `namespace` label to every
        # ServiceMonitor target — scope by it. In docker-compose there is no such
        # label, so use no namespace filter (namespace == "default" sentinel).
        ns = (namespace or "").strip()
        ns_filter = f', namespace="{ns}"' if ns and ns != "default" else ""

        for metric_name, query_template in METRIC_QUERIES:
            query = query_template.format(target=target, ns=ns_filter)
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

    async def get_label_values(self, label: str) -> list[str]:
        """Return all values for a Prometheus label (e.g. "job")."""
        data, _ = await self._get(f"/api/v1/label/{label}/values")
        if data.get("status") != "success":
            raise ObservabilityClientError(
                self.backend_name,
                f"Non-success status: {data.get('status')} — {data.get('error', '')}",
            )
        return data.get("data", [])

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
