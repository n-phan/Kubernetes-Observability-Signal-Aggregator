"""
MCP server exposing the aggregator's observability signals to Hermes.

The server speaks a minimal stdio JSON-RPC subset of MCP and forwards tool
calls to the running aggregator HTTP API. It intentionally avoids importing the
aggregator runtime so Hermes can launch it as a small, independent subprocess.
"""
from __future__ import annotations

import logging
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

AGGREGATOR_API_URL = os.getenv("AGGREGATOR_API_URL", "http://localhost:8080").rstrip("/")
DEFAULT_LOOKBACK_MINUTES = int(os.getenv("K8S_OBS_MCP_LOOKBACK_MINUTES", "30"))
MAX_CONTENT_CHARS = int(os.getenv("K8S_OBS_MCP_MAX_CONTENT_CHARS", "16000"))
MAX_FOCUS_TERMS = 12
FOCUS_TIME_TOLERANCE_SECONDS = int(os.getenv("K8S_OBS_MCP_FOCUS_TOLERANCE_SECONDS", "360"))
LATENCY_THRESHOLD_MS = int(os.getenv("K8S_OBS_MCP_LATENCY_THRESHOLD_MS", "1000"))

JSONRPC_VERSION = "2.0"
logger = logging.getLogger(__name__)

_ROUTE_RE = re.compile(r"/[A-Za-z0-9_./{}:-]+")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        try:
            request_msg = json.loads(raw_line)
            response = _handle_request(request_msg)
        except Exception as exc:
            response = _error_response(None, -32603, str(exc))
        if response is not None:
            _write_response(response)


def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        return _result_response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "k8s-observability-signal-aggregator",
                    "version": "0.1.0",
                },
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _result_response(request_id, {"tools": _tool_definitions()})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            name = _text(params.get("name")) or "<missing>"
            args = params.get("arguments") or {}
            logger.info(
                "MCP tools/call name=%s target=%s namespace=%s scope=%s",
                name,
                _text(args.get("target")) or "<missing>",
                _text(args.get("namespace")) or "default",
                _scope_summary(args),
            )
            content = _call_tool(name, args)
            return _result_response(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": _compact_json(content),
                        }
                    ],
                    "isError": False,
                },
            )
        except Exception as exc:
            return _result_response(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )

    return _error_response(request_id, -32601, f"Method not found: {method}")


def _tool_definitions() -> list[dict[str, Any]]:
    shared = {
        "target": {
            "type": "string",
            "description": "Service name to inspect, for example service-a.",
        },
        "namespace": {
            "type": "string",
            "description": "Kubernetes namespace. Defaults to default.",
            "default": "default",
        },
        "lookback_minutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10080,
            "description": (
                "Explicit rolling lookback window in minutes for ad hoc queries. "
                "For scoped incident RCA, pass start and end instead."
            ),
        },
        "start": {
            "type": "string",
            "description": "ISO 8601 start timestamp. Use with end for an exact incident window.",
        },
        "end": {
            "type": "string",
            "description": "ISO 8601 end timestamp. Use with start for an exact incident window.",
        },
    }
    time_scope = [
        {"required": ["start", "end"]},
        {"required": ["lookback_minutes"]},
    ]
    return [
        {
            "name": "get_aggregate",
            "description": (
                "Fetch the aggregator's compact cross-signal incident overview. "
                "Call this before drilling into individual signal tools."
            ),
            "inputSchema": {
                "type": "object",
                "properties": shared,
                "required": ["target"],
                "anyOf": time_scope,
                "additionalProperties": False,
            },
        },
        {
            "name": "get_metrics",
            "description": "Fetch compact Prometheus metric summaries for a service.",
            "inputSchema": {
                "type": "object",
                "properties": shared,
                "required": ["target"],
                "anyOf": time_scope,
                "additionalProperties": False,
            },
        },
        {
            "name": "get_logs",
            "description": "Fetch recent Loki log lines for a service.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **shared,
                    "severity": {
                        "type": "string",
                        "enum": ["debug", "info", "warn", "warning", "error", "critical"],
                        "description": "Optional severity filter.",
                    },
                },
                "required": ["target"],
                "anyOf": time_scope,
                "additionalProperties": False,
            },
        },
        {
            "name": "get_traces",
            "description": "Fetch compact Jaeger trace summaries for a service.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **shared,
                    "errors_only": {
                        "type": "boolean",
                        "description": "Only return traces that contain error spans.",
                        "default": False,
                    },
                },
                "required": ["target"],
                "anyOf": time_scope,
                "additionalProperties": False,
            },
        },
        {
            "name": "get_correlations",
            "description": "Fetch all three signals and return correlation events.",
            "inputSchema": {
                "type": "object",
                "properties": shared,
                "required": ["target"],
                "anyOf": time_scope,
                "additionalProperties": False,
            },
        },
    ]


def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    target = _required_text(args, "target")
    namespace = _text(args.get("namespace")) or "default"
    time_scope = _time_scope(args)
    result = _query_aggregator(target, namespace, time_scope)

    if name == "get_aggregate":
        return _aggregate_result(name, target, namespace, result)
    if name == "get_metrics":
        focus = _incident_focus(result)
        focus_terms = focus["terms"]
        return {
            "ok": result.get("metrics", {}).get("error") is None,
            "tool": name,
            "target": target,
            "namespace": namespace,
            "error": result.get("metrics", {}).get("error"),
            "focus_terms": focus_terms[:MAX_FOCUS_TERMS],
            "focus_timestamp": _format_optional_datetime(focus["timestamp"]),
            "focus_family": focus["family"],
            "metrics": _summarize_metrics(
                result.get("metrics", {}),
                focus_terms=focus_terms,
                focus_time=focus["timestamp"],
            ),
        }
    if name == "get_logs":
        severity = _normalize_severity(args.get("severity"))
        focus = _incident_focus(result)
        focus_terms = focus["terms"]
        logs = _summarize_logs(
            result.get("logs", {}),
            severity=severity,
            focus_terms=focus_terms,
            focus_time=focus["timestamp"],
        )
        return {
            "ok": result.get("logs", {}).get("error") is None,
            "tool": name,
            "target": target,
            "namespace": namespace,
            "error": result.get("logs", {}).get("error"),
            "focus_terms": focus_terms[:MAX_FOCUS_TERMS],
            "focus_timestamp": _format_optional_datetime(focus["timestamp"]),
            "focus_family": focus["family"],
            "total_lines": result.get("logs", {}).get("total_lines", 0),
            "error_count": result.get("logs", {}).get("error_count", 0),
            "warn_count": result.get("logs", {}).get("warn_count", 0),
            "logs": logs,
        }
    if name == "get_traces":
        errors_only = bool(args.get("errors_only", False))
        focus = _incident_focus(result)
        focus_terms = focus["terms"]
        return {
            "ok": result.get("traces", {}).get("error") is None,
            "tool": name,
            "target": target,
            "namespace": namespace,
            "error": result.get("traces", {}).get("error"),
            "focus_terms": focus_terms[:MAX_FOCUS_TERMS],
            "focus_timestamp": _format_optional_datetime(focus["timestamp"]),
            "focus_family": focus["family"],
            "error_trace_count": result.get("traces", {}).get("error_trace_count", 0),
            "p99_duration_ms": result.get("traces", {}).get("p99_duration_ms"),
            "traces": _summarize_traces(
                result.get("traces", {}),
                errors_only=errors_only,
                focus_terms=focus_terms,
                focus_time=focus["timestamp"],
            ),
        }
    if name == "get_correlations":
        focus = _incident_focus(result)
        return {
            "ok": not _has_signal_errors(result),
            "tool": name,
            "target": target,
            "namespace": namespace,
            "focus_terms": focus["terms"][:MAX_FOCUS_TERMS],
            "focus_timestamp": _format_optional_datetime(focus["timestamp"]),
            "focus_family": focus["family"],
            "signal_errors": {
                "metrics": result.get("metrics", {}).get("error"),
                "logs": result.get("logs", {}).get("error"),
                "traces": result.get("traces", {}).get("error"),
            },
            "correlations": _summarize_correlations(
                result.get("correlations", []),
                focus_time=focus["timestamp"],
                focus_family=focus["family"],
            ),
        }
    raise ValueError(f"Unknown tool: {name}")


def _aggregate_result(
    tool_name: str,
    target: str,
    namespace: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    metrics = result.get("metrics", {})
    logs = result.get("logs", {})
    traces = result.get("traces", {})
    meta = result.get("meta", {})
    correlations = result.get("correlations", [])
    focus = _incident_focus(result)
    focus_terms = focus["terms"]
    return {
        "ok": not _has_signal_errors(result),
        "tool": tool_name,
        "target": target,
        "namespace": namespace,
        "window_start": meta.get("window_start"),
        "window_end": meta.get("window_end"),
        "focus_terms": focus_terms[:MAX_FOCUS_TERMS],
        "focus_timestamp": _format_optional_datetime(focus["timestamp"]),
        "focus_family": focus["family"],
        "signal_errors": {
            "metrics": metrics.get("error"),
            "logs": logs.get("error"),
            "traces": traces.get("error"),
        },
        "counts": {
            "metric_series": len(metrics.get("series") or []),
            "total_log_lines": logs.get("total_lines", 0),
            "error_log_lines": logs.get("error_count", 0),
            "warn_log_lines": logs.get("warn_count", 0),
            "traces": len(traces.get("traces") or []),
            "error_traces": traces.get("error_trace_count", 0),
            "correlations": len(correlations),
        },
        "aggregate": {
            "target": target,
            "namespace": namespace,
            "window_start": meta.get("window_start"),
            "window_end": meta.get("window_end"),
            "correlations": _summarize_correlations(
                correlations,
                focus_time=focus["timestamp"],
                focus_family=focus["family"],
            ),
            "metrics": _summarize_metrics(
                metrics,
                focus_terms=focus_terms,
                focus_time=focus["timestamp"],
            ),
            "logs": _summarize_logs(
                logs,
                focus_terms=focus_terms,
                focus_time=focus["timestamp"],
            ),
            "traces": _summarize_traces(
                traces,
                focus_terms=focus_terms,
                focus_time=focus["timestamp"],
            ),
        },
    }


def _query_aggregator(
    target: str,
    namespace: str,
    time_scope: dict[str, Any],
) -> dict[str, Any]:
    body = json.dumps(
        {
            "target": target,
            "namespace": namespace,
            **time_scope,
            "include_metrics": True,
            "include_logs": True,
            "include_traces": True,
            "include_rca": False,
        }
    ).encode("utf-8")
    req = request.Request(
        f"{AGGREGATOR_API_URL}/query",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Aggregator API returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Aggregator API unavailable at {AGGREGATOR_API_URL}: {exc}") from exc


def _has_signal_errors(result: dict[str, Any]) -> bool:
    return any(
        (result.get(signal) or {}).get("error") is not None
        for signal in ("metrics", "logs", "traces")
    )


def _summarize_metrics(
    metrics: dict[str, Any],
    *,
    focus_terms: list[str] | None = None,
    focus_time: datetime | None = None,
) -> list[dict[str, Any]]:
    series = [series for series in (metrics.get("series") or []) if isinstance(series, dict)]
    terms = _normalized_terms(focus_terms or [])
    if focus_time is not None:
        series = [
            item for item in series
            if not _is_error_metric_series(item)
            or (_max_sample_near(item, focus_time) or 0) > 0
        ]

    def score(series: dict[str, Any]) -> tuple[int, float]:
        labels = series.get("labels", {})
        labels = labels if isinstance(labels, dict) else {}
        name = str(series.get("name") or "")
        status = str(labels.get("status") or "")
        text = f"{name} {' '.join(str(v) for v in labels.values())}"

        priority = 0
        if terms and _text_matches_any_term(text, terms):
            priority += 100
        if status.startswith("5"):
            priority += 50
        if name == "http_error_rate":
            priority += 40
        if name == "http_latency_p99":
            priority += 15
        if name == "http_requests_per_second":
            priority += 5

        value = _max_sample_near(series, focus_time) if focus_time is not None else None
        if value is None:
            try:
                value = float(series.get("peak_value") or series.get("latest_value") or 0)
            except (TypeError, ValueError):
                value = 0.0
        return priority, value

    series = sorted(series, key=score, reverse=True)
    return [
        {
            "name": series.get("name"),
            "labels": series.get("labels", {}),
            "latest_value": series.get("latest_value"),
            "peak_value": series.get("peak_value"),
            "sample_count": len(series.get("samples") or []),
        }
        for series in series[:10]
    ]


def _summarize_logs(
    logs: dict[str, Any],
    *,
    severity: str | None = None,
    focus_terms: list[str] | None = None,
    focus_time: datetime | None = None,
) -> list[dict[str, Any]]:
    lines = [line for line in logs.get("lines", []) if isinstance(line, dict)]
    if severity:
        lines = [line for line in lines if line.get("severity") == severity]

    important = [
        line
        for line in lines
        if line.get("severity") in {"error", "critical", "warn"}
    ]

    selected = important if important else lines
    focused = _filter_lines_by_focus(
        selected,
        focus_terms or [],
        focus_time=focus_time,
    )
    if focused or focus_time is not None:
        selected = focused

    return [
        {
            "timestamp": line.get("timestamp"),
            "severity": line.get("severity"),
            "message": str(line.get("message", ""))[:500],
            "labels": line.get("labels", {}),
        }
        for line in selected[-80:]
    ]


def _filter_lines_by_focus(
    lines: list[dict[str, Any]],
    focus_terms: list[str],
    *,
    focus_time: datetime | None = None,
) -> list[dict[str, Any]]:
    terms = _normalized_terms(focus_terms)
    if not terms and focus_time is None:
        return []
    if terms:
        return [
            line
            for line in lines
            if _text_matches_any_term(str(line.get("message", "")), terms)
        ]
    return [
        line
        for line in lines
        if _timestamp_near_focus(line.get("timestamp"), focus_time)
    ]


def _incident_focus(result: dict[str, Any]) -> dict[str, Any]:
    trace_terms, trace_time = _trace_error_focus_terms(result.get("traces", {}))
    slow_terms, slow_time = _trace_latency_focus_terms(result.get("traces", {}))
    metric_terms, metric_time = _metric_error_focus_terms(result.get("metrics", {}))
    latency_metric_terms, latency_metric_time = _metric_latency_focus_terms(result.get("metrics", {}))

    candidates = [
        {"terms": trace_terms, "timestamp": trace_time, "source": "error_trace", "family": "errors", "priority": 40},
        {"terms": slow_terms, "timestamp": slow_time, "source": "slow_trace", "family": "latency", "priority": 30},
        {"terms": metric_terms, "timestamp": metric_time, "source": "error_metric", "family": "errors", "priority": 20},
        {
            "terms": latency_metric_terms,
            "timestamp": latency_metric_time,
            "source": "latency_metric",
            "family": "latency",
            "priority": 10,
        },
    ]
    candidates = [candidate for candidate in candidates if candidate["terms"]]
    if not candidates:
        return {"terms": [], "timestamp": None, "source": None, "family": None}

    dated = [candidate for candidate in candidates if candidate["timestamp"] is not None]
    if dated:
        return max(dated, key=lambda candidate: (candidate["timestamp"], candidate["priority"]))
    return max(candidates, key=lambda candidate: candidate["priority"])


def _incident_focus_terms(result: dict[str, Any]) -> list[str]:
    return _incident_focus(result)["terms"]


def _trace_error_focus_terms(traces_signal: dict[str, Any]) -> tuple[list[str], datetime | None]:
    candidates: list[tuple[datetime | None, list[str]]] = []
    for trace in traces_signal.get("traces", []) or []:
        if not isinstance(trace, dict) or not trace.get("has_errors"):
            continue
        for span in trace.get("spans", []) or []:
            if not isinstance(span, dict):
                continue
            if not (span.get("is_error") or _span_has_error_status(span)):
                continue
            terms: list[str] = []
            _collect_focus_from_text(terms, span.get("operation_name"))
            span_time = _parse_optional_datetime(span.get("start_time"))
            tags = span.get("tags") or {}
            if isinstance(tags, dict):
                for key in (
                    "http.route",
                    "http.target",
                    "http.url",
                    "otel.status_description",
                    "exception.type",
                    "exception.message",
                ):
                    _collect_focus_from_text(terms, tags.get(key))
            if terms:
                candidates.append((span_time, terms))

    if not candidates:
        return [], None

    dated = [candidate for candidate in candidates if candidate[0] is not None]
    if dated:
        latest = max(timestamp for timestamp, _ in dated if timestamp is not None)
        terms = [
            term
            for timestamp, candidate_terms in dated
            if timestamp == latest
            for term in candidate_terms
        ]
        return _dedupe_terms(terms), latest

    terms = [term for _, candidate_terms in candidates for term in candidate_terms]
    return _dedupe_terms(terms), None


def _trace_latency_focus_terms(traces_signal: dict[str, Any]) -> tuple[list[str], datetime | None]:
    candidates: list[tuple[datetime | None, list[str]]] = []
    for trace in traces_signal.get("traces", []) or []:
        if not isinstance(trace, dict):
            continue
        trace_duration = _float_or_zero(trace.get("duration_ms"))
        spans = [span for span in trace.get("spans", []) or [] if isinstance(span, dict)]
        slow_spans = [
            span
            for span in spans
            if _float_or_zero(span.get("duration_ms")) >= LATENCY_THRESHOLD_MS
        ]
        if trace_duration < LATENCY_THRESHOLD_MS and not slow_spans:
            continue

        selected_spans = slow_spans or spans[:1]
        for span in selected_spans:
            terms: list[str] = []
            _collect_focus_from_text(terms, span.get("operation_name"))
            tags = span.get("tags") or {}
            if isinstance(tags, dict):
                for key in ("http.route", "http.target", "http.url"):
                    _collect_focus_from_text(terms, tags.get(key))
            if terms:
                candidates.append((_parse_optional_datetime(span.get("start_time")), terms))

    if not candidates:
        return [], None

    dated = [candidate for candidate in candidates if candidate[0] is not None]
    if dated:
        latest = max(timestamp for timestamp, _ in dated if timestamp is not None)
        terms = [
            term
            for timestamp, candidate_terms in dated
            if timestamp == latest
            for term in candidate_terms
        ]
        return _dedupe_terms(terms), latest

    terms = [term for _, candidate_terms in candidates for term in candidate_terms]
    return _dedupe_terms(terms), None


def _span_has_error_status(span: dict[str, Any]) -> bool:
    tags = span.get("tags") or {}
    if not isinstance(tags, dict):
        return False
    status = str(tags.get("http.status_code") or "")
    return status.startswith("5") or str(tags.get("otel.status_code") or "").upper() == "ERROR"


def _metric_error_focus_terms(metrics: dict[str, Any]) -> tuple[list[str], datetime | None]:
    candidates: list[tuple[datetime | None, float, list[str]]] = []
    for series in metrics.get("series", []) or []:
        if not isinstance(series, dict):
            continue
        labels = series.get("labels") or {}
        if not isinstance(labels, dict):
            continue

        name = str(series.get("name") or "")
        status = str(labels.get("status") or "")
        has_error_signal = name == "http_error_rate" or status.startswith("5")
        if not has_error_signal:
            continue

        try:
            value = float(series.get("peak_value") or series.get("latest_value") or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value <= 0:
            continue

        terms: list[str] = []
        _collect_focus_from_text(terms, labels.get("handler"))
        if not terms:
            continue
        sample_time = _latest_nonzero_sample_time(series)
        candidates.append((sample_time, value, terms))

    if not candidates:
        return [], None

    dated = [candidate for candidate in candidates if candidate[0] is not None]
    if dated:
        latest = max(timestamp for timestamp, _, _ in dated if timestamp is not None)
        terms = [
            term
            for timestamp, _, candidate_terms in dated
            if timestamp == latest
            for term in candidate_terms
        ]
        return _dedupe_terms(terms), latest

    _, _, terms = max(candidates, key=lambda candidate: candidate[1])
    return _dedupe_terms(terms), None


def _metric_latency_focus_terms(metrics: dict[str, Any]) -> tuple[list[str], datetime | None]:
    candidates: list[tuple[datetime | None, float, list[str]]] = []
    for series in metrics.get("series", []) or []:
        if not isinstance(series, dict) or series.get("name") != "http_latency_p99":
            continue
        labels = series.get("labels") or {}
        if not isinstance(labels, dict):
            continue
        try:
            value = float(series.get("peak_value") or series.get("latest_value") or 0)
        except (TypeError, ValueError):
            value = 0.0
        # Prometheus histograms are seconds; traces are milliseconds.
        if value < (LATENCY_THRESHOLD_MS / 1000):
            continue
        terms: list[str] = []
        _collect_focus_from_text(terms, labels.get("handler"))
        if not terms:
            continue
        sample_time = _latest_sample_time_at_or_above(series, LATENCY_THRESHOLD_MS / 1000)
        candidates.append((sample_time, value, terms))

    if not candidates:
        return [], None
    dated = [candidate for candidate in candidates if candidate[0] is not None]
    if dated:
        latest = max(timestamp for timestamp, _, _ in dated if timestamp is not None)
        terms = [
            term
            for timestamp, _, candidate_terms in dated
            if timestamp == latest
            for term in candidate_terms
        ]
        return _dedupe_terms(terms), latest
    _, _, terms = max(candidates, key=lambda candidate: candidate[1])
    return _dedupe_terms(terms), None


def _latest_nonzero_sample_time(series: dict[str, Any]) -> datetime | None:
    latest: datetime | None = None
    for sample in series.get("samples", []) or []:
        if not isinstance(sample, dict):
            continue
        try:
            value = float(sample.get("value") or 0)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        timestamp = _parse_optional_datetime(sample.get("timestamp"))
        if timestamp is not None and (latest is None or timestamp > latest):
            latest = timestamp
    return latest


def _latest_sample_time_at_or_above(series: dict[str, Any], threshold: float) -> datetime | None:
    latest: datetime | None = None
    for sample in series.get("samples", []) or []:
        if not isinstance(sample, dict):
            continue
        try:
            value = float(sample.get("value") or 0)
        except (TypeError, ValueError):
            continue
        if value < threshold:
            continue
        timestamp = _parse_optional_datetime(sample.get("timestamp"))
        if timestamp is not None and (latest is None or timestamp > latest):
            latest = timestamp
    return latest


def _max_sample_near(series: dict[str, Any], focus_time: datetime | None) -> float | None:
    if focus_time is None:
        return None
    values: list[float] = []
    for sample in series.get("samples", []) or []:
        if not isinstance(sample, dict):
            continue
        if not _timestamp_near_focus(sample.get("timestamp"), focus_time):
            continue
        try:
            values.append(float(sample.get("value") or 0))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


def _is_error_metric_series(series: dict[str, Any]) -> bool:
    labels = series.get("labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    status = str(labels.get("status") or "")
    return series.get("name") == "http_error_rate" or status.startswith("5")


def _collect_focus_from_text(terms: list[str], value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        return
    text = value.strip()

    for route in _ROUTE_RE.findall(text):
        cleaned = route.rstrip(".,;:)")
        if cleaned:
            terms.append(cleaned)
            leaf = cleaned.rstrip("/").rsplit("/", 1)[-1]
            if leaf and leaf not in {"api", "v1", "v2"}:
                terms.append(leaf)

    # Operation/function and exception tokens are often the only bridge between
    # traces and traceback logs, e.g. GET /crash -> "in crash" and
    # _process_payment -> payment_processor errors.
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{3,}", text):
        if token.lower() in {"http", "https", "none", "true", "false", "error"}:
            continue
        terms.append(token)


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for term in terms:
        cleaned = str(term).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def _normalized_terms(terms: list[str]) -> list[str]:
    normalized: list[str] = []
    for term in terms:
        cleaned = str(term).strip().lower()
        if len(cleaned) >= 4:
            normalized.append(cleaned)
    return normalized


def _text_matches_any_term(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _timestamp_near_focus(value: Any, focus_time: datetime | None) -> bool:
    if focus_time is None:
        return False
    timestamp = _parse_optional_datetime(value)
    if timestamp is None:
        return False
    return abs((timestamp - focus_time).total_seconds()) <= FOCUS_TIME_TOLERANCE_SECONDS


def _summarize_correlations(
    correlations: list[Any],
    *,
    focus_time: datetime | None,
    focus_family: str | None,
) -> list[dict[str, Any]]:
    events = [event for event in correlations if isinstance(event, dict)]
    if focus_time is not None:
        dated = [
            event
            for event in events
            if _timestamp_near_focus(event.get("timestamp"), focus_time)
        ]
        if dated:
            return dated[:12]

    if focus_family == "latency":
        return [
            event
            for event in events
            if "latency" in str(event.get("kind") or "")
        ][:12]
    if focus_family == "errors":
        return [
            event
            for event in events
            if any(token in str(event.get("kind") or "") for token in ("error", "log"))
        ][:12]
    return events[:12]


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_optional_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _summarize_traces(
    traces_signal: dict[str, Any],
    *,
    errors_only: bool = False,
    focus_terms: list[str] | None = None,
    focus_time: datetime | None = None,
) -> list[dict[str, Any]]:
    traces = [trace for trace in traces_signal.get("traces", []) if isinstance(trace, dict)]
    if errors_only:
        traces = [trace for trace in traces if trace.get("has_errors")]
    terms = _normalized_terms(focus_terms or [])
    if terms or focus_time is not None:
        focused = [
            trace for trace in traces
            if _trace_matches_focus(trace, terms, focus_time)
        ]
        if focused:
            traces = focused

    def score(trace: dict[str, Any]) -> tuple[int, float]:
        spans = [span for span in trace.get("spans", []) or [] if isinstance(span, dict)]
        text_parts: list[str] = []
        for span in spans:
            text_parts.append(str(span.get("operation_name") or ""))
            tags = span.get("tags") or {}
            if isinstance(tags, dict):
                text_parts.extend(str(value) for value in tags.values())
        text = " ".join(text_parts)
        priority = 100 if terms and _text_matches_any_term(text, terms) else 0
        if trace.get("has_errors"):
            priority += 50
        try:
            duration = float(trace.get("duration_ms") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        return priority, duration

    traces = sorted(
        traces,
        key=score,
        reverse=True,
    )
    return [
        {
            "trace_id": trace.get("trace_id"),
            "root_service": trace.get("root_service"),
            "duration_ms": trace.get("duration_ms"),
            "has_errors": trace.get("has_errors"),
            "spans": [
                {
                    "service_name": span.get("service_name"),
                    "operation_name": span.get("operation_name"),
                    "duration_ms": span.get("duration_ms"),
                    "is_error": span.get("is_error"),
                    "tags": dict(list((span.get("tags") or {}).items())[:8]),
                }
                for span in (trace.get("spans") or [])[:8]
                if isinstance(span, dict)
            ],
        }
        for trace in traces[:12]
    ]


def _trace_matches_focus(
    trace: dict[str, Any],
    terms: list[str],
    focus_time: datetime | None,
) -> bool:
    spans = [span for span in trace.get("spans", []) or [] if isinstance(span, dict)]
    text_parts: list[str] = []
    for span in spans:
        if _timestamp_near_focus(span.get("start_time"), focus_time):
            return True
        text_parts.append(str(span.get("operation_name") or ""))
        tags = span.get("tags") or {}
        if isinstance(tags, dict):
            text_parts.extend(str(value) for value in tags.values())
    return bool(terms and _text_matches_any_term(" ".join(text_parts), terms))


def _result_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _write_response(response: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _compact_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(text) > MAX_CONTENT_CHARS:
        return text[:MAX_CONTENT_CHARS] + "...[truncated]"
    return text


def _required_text(args: dict[str, Any], key: str) -> str:
    value = _text(args.get(key))
    if not value:
        raise ValueError(f"Missing required argument: {key}")
    return value


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _time_scope(args: dict[str, Any]) -> dict[str, Any]:
    start = _text(args.get("start"))
    end = _text(args.get("end"))
    if bool(start) != bool(end):
        raise ValueError("Both start and end must be provided together")
    if start and end:
        start_dt = _parse_optional_datetime(start)
        end_dt = _parse_optional_datetime(end)
        if start_dt is None:
            raise ValueError(f"Invalid start timestamp: {start}")
        if end_dt is None:
            raise ValueError(f"Invalid end timestamp: {end}")
        if start_dt >= end_dt:
            raise ValueError(
                f"Invalid time scope: start must be before end "
                f"(start={start} end={end})"
            )
        return {"start": start, "end": end}

    if args.get("lookback_minutes") is None:
        raise ValueError(
            "Missing time scope: provide start and end for scoped RCA, or explicit "
            "lookback_minutes for an ad hoc query"
        )
    lookback = _int(args.get("lookback_minutes"), DEFAULT_LOOKBACK_MINUTES)
    return {"lookback_minutes": max(1, lookback)}


def _scope_summary(args: dict[str, Any]) -> str:
    start = _text(args.get("start"))
    end = _text(args.get("end"))
    if start and end:
        return f"start={start} end={end}"

    if args.get("lookback_minutes") is None:
        return "missing"
    lookback = _int(args.get("lookback_minutes"), DEFAULT_LOOKBACK_MINUTES)
    return f"lookback={max(1, lookback)}m"


def _normalize_severity(value: Any) -> str | None:
    severity = _text(value)
    if severity == "warning":
        return "warn"
    return severity


if __name__ == "__main__":
    main()
