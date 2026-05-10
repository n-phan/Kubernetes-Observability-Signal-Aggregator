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
import sys
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

AGGREGATOR_API_URL = os.getenv("AGGREGATOR_API_URL", "http://localhost:8080").rstrip("/")
DEFAULT_LOOKBACK_MINUTES = int(os.getenv("K8S_OBS_MCP_LOOKBACK_MINUTES", "30"))
MAX_CONTENT_CHARS = int(os.getenv("K8S_OBS_MCP_MAX_CONTENT_CHARS", "16000"))

JSONRPC_VERSION = "2.0"
logger = logging.getLogger(__name__)


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
                "Rolling lookback window in minutes for ad hoc queries. "
                "For scoped incident RCA, prefer start and end."
            ),
            "default": DEFAULT_LOOKBACK_MINUTES,
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
        return {
            "ok": result.get("metrics", {}).get("error") is None,
            "tool": name,
            "target": target,
            "namespace": namespace,
            "error": result.get("metrics", {}).get("error"),
            "metrics": _summarize_metrics(result.get("metrics", {})),
        }
    if name == "get_logs":
        severity = _normalize_severity(args.get("severity"))
        logs = _summarize_logs(result.get("logs", {}), severity=severity)
        return {
            "ok": result.get("logs", {}).get("error") is None,
            "tool": name,
            "target": target,
            "namespace": namespace,
            "error": result.get("logs", {}).get("error"),
            "total_lines": result.get("logs", {}).get("total_lines", 0),
            "error_count": result.get("logs", {}).get("error_count", 0),
            "warn_count": result.get("logs", {}).get("warn_count", 0),
            "logs": logs,
        }
    if name == "get_traces":
        errors_only = bool(args.get("errors_only", False))
        return {
            "ok": result.get("traces", {}).get("error") is None,
            "tool": name,
            "target": target,
            "namespace": namespace,
            "error": result.get("traces", {}).get("error"),
            "error_trace_count": result.get("traces", {}).get("error_trace_count", 0),
            "p99_duration_ms": result.get("traces", {}).get("p99_duration_ms"),
            "traces": _summarize_traces(result.get("traces", {}), errors_only=errors_only),
        }
    if name == "get_correlations":
        return {
            "ok": not _has_signal_errors(result),
            "tool": name,
            "target": target,
            "namespace": namespace,
            "signal_errors": {
                "metrics": result.get("metrics", {}).get("error"),
                "logs": result.get("logs", {}).get("error"),
                "traces": result.get("traces", {}).get("error"),
            },
            "correlations": result.get("correlations", [])[:12],
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
    return {
        "ok": not _has_signal_errors(result),
        "tool": tool_name,
        "target": target,
        "namespace": namespace,
        "window_start": meta.get("window_start"),
        "window_end": meta.get("window_end"),
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
            "correlations": correlations[:12],
            "metrics": _summarize_metrics(metrics),
            "logs": _summarize_logs(logs),
            "traces": _summarize_traces(traces),
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


def _summarize_metrics(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": series.get("name"),
            "labels": series.get("labels", {}),
            "latest_value": series.get("latest_value"),
            "peak_value": series.get("peak_value"),
            "sample_count": len(series.get("samples") or []),
        }
        for series in (metrics.get("series") or [])[:10]
        if isinstance(series, dict)
    ]


def _summarize_logs(
    logs: dict[str, Any],
    *,
    severity: str | None = None,
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
    return [
        {
            "timestamp": line.get("timestamp"),
            "severity": line.get("severity"),
            "message": str(line.get("message", ""))[:500],
            "labels": line.get("labels", {}),
        }
        for line in selected[-80:]
    ]


def _summarize_traces(
    traces_signal: dict[str, Any],
    *,
    errors_only: bool = False,
) -> list[dict[str, Any]]:
    traces = [trace for trace in traces_signal.get("traces", []) if isinstance(trace, dict)]
    if errors_only:
        traces = [trace for trace in traces if trace.get("has_errors")]
    traces = sorted(
        traces,
        key=lambda trace: (bool(trace.get("has_errors")), float(trace.get("duration_ms") or 0)),
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
        return {"start": start, "end": end}

    lookback = _int(args.get("lookback_minutes"), DEFAULT_LOOKBACK_MINUTES)
    return {"lookback_minutes": max(1, lookback)}


def _scope_summary(args: dict[str, Any]) -> str:
    start = _text(args.get("start"))
    end = _text(args.get("end"))
    if start and end:
        return "start/end"

    lookback = _int(args.get("lookback_minutes"), DEFAULT_LOOKBACK_MINUTES)
    return f"lookback={max(1, lookback)}m"


def _normalize_severity(value: Any) -> str | None:
    severity = _text(value)
    if severity == "warning":
        return "warn"
    return severity


if __name__ == "__main__":
    main()
