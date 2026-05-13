from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from aggregator import mcp_observability_server as server


def test_initialize_response_advertises_tools() -> None:
    response = server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        }
    )

    assert response is not None
    assert response["result"]["capabilities"] == {"tools": {}}
    assert response["result"]["serverInfo"]["name"] == "k8s-observability-signal-aggregator"


def test_tools_list_exposes_observability_tools() -> None:
    response = server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
    )

    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == {
        "get_aggregate",
        "get_metrics",
        "get_logs",
        "get_traces",
        "get_correlations",
    }
    aggregate_schema = next(
        tool["inputSchema"]
        for tool in response["result"]["tools"]
        if tool["name"] == "get_aggregate"
    )
    assert {"required": ["start", "end"]} in aggregate_schema["anyOf"]
    assert {"required": ["lookback_minutes"]} in aggregate_schema["anyOf"]


def test_get_aggregate_calls_aggregator_query_api() -> None:
    with _mock_urlopen(_sample_result()) as calls:
        result = server._call_tool(
            "get_aggregate",
            {
                "target": "service-a",
                "namespace": "default",
                "lookback_minutes": 15,
            },
        )

    request = calls[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "http://localhost:8080/query"
    assert payload["target"] == "service-a"
    assert payload["namespace"] == "default"
    assert payload["lookback_minutes"] == 15
    assert payload["include_rca"] is False
    assert result["ok"] is True
    assert result["tool"] == "get_aggregate"
    assert result["counts"]["metric_series"] == 1
    assert result["counts"]["error_log_lines"] == 1
    assert result["counts"]["error_traces"] == 1
    assert result["aggregate"]["metrics"][0]["name"] == "http_error_rate"
    assert result["aggregate"]["logs"][0]["message"] == "error line"
    assert result["aggregate"]["traces"][0]["trace_id"] == "trace-1"


def test_get_aggregate_reports_not_ok_when_signal_errors_are_serialized() -> None:
    payload = _sample_result()
    payload["metrics"]["error"] = "Prometheus timed out"

    with _mock_urlopen(payload):
        result = server._call_tool(
            "get_aggregate",
            {"target": "service-a", "lookback_minutes": 30},
        )

    assert result["ok"] is False
    assert result["signal_errors"]["metrics"] == "Prometheus timed out"


def test_tools_call_logs_name_and_lookback_scope(caplog) -> None:
    with _mock_urlopen(_sample_result()):
        with caplog.at_level("INFO"):
            response = server._handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "get_aggregate",
                        "arguments": {
                            "target": "service-a",
                            "namespace": "default",
                            "lookback_minutes": 15,
                        },
                    },
                }
            )

    assert response["result"]["isError"] is False
    assert "MCP tools/call name=get_aggregate target=service-a namespace=default scope=lookback=15m" in caplog.text


def test_tools_call_logs_exact_window_scope(caplog) -> None:
    with _mock_urlopen(_sample_result()):
        with caplog.at_level("INFO"):
            response = server._handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "get_metrics",
                        "arguments": {
                            "target": "service-a",
                            "namespace": "default",
                            "start": "2026-05-10T00:00:00Z",
                            "end": "2026-05-10T00:30:00Z",
                        },
                    },
                }
            )

    assert response["result"]["isError"] is False
    assert (
        "MCP tools/call name=get_metrics target=service-a namespace=default "
        "scope=start=2026-05-10T00:00:00Z end=2026-05-10T00:30:00Z"
    ) in caplog.text


def test_get_metrics_calls_aggregator_query_api() -> None:
    with _mock_urlopen(_sample_result()) as calls:
        result = server._call_tool(
            "get_metrics",
            {
                "target": "service-a",
                "namespace": "default",
                "lookback_minutes": 15,
            },
        )

    request = calls[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "http://localhost:8080/query"
    assert payload["target"] == "service-a"
    assert payload["namespace"] == "default"
    assert payload["lookback_minutes"] == 15
    assert payload["include_rca"] is False
    assert result["ok"] is True
    assert result["metrics"][0]["name"] == "http_error_rate"
    assert result["metrics"][0]["sample_count"] == 1


def test_exact_window_omits_lookback_when_calling_aggregator_query_api() -> None:
    with _mock_urlopen(_sample_result()) as calls:
        result = server._call_tool(
            "get_metrics",
            {
                "target": "service-a",
                "namespace": "default",
                "lookback_minutes": 15,
                "start": "2026-05-10T00:00:00Z",
                "end": "2026-05-10T00:30:00Z",
            },
        )

    payload = json.loads(calls[0].data.decode("utf-8"))
    assert payload["start"] == "2026-05-10T00:00:00Z"
    assert payload["end"] == "2026-05-10T00:30:00Z"
    assert "lookback_minutes" not in payload
    assert payload["include_rca"] is False
    assert result["ok"] is True


def test_get_correlations_reports_not_ok_when_signal_errors_are_serialized() -> None:
    payload = _sample_result()
    payload["metrics"]["error"] = "Prometheus timed out"

    with _mock_urlopen(payload):
        result = server._call_tool(
            "get_correlations",
            {"target": "service-a", "lookback_minutes": 30},
        )

    assert result["ok"] is False
    assert result["signal_errors"]["metrics"] == "Prometheus timed out"
    assert result["correlations"][0]["kind"] == "error_spike"


def test_partial_exact_window_returns_tool_error() -> None:
    response = server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "get_metrics",
                "arguments": {
                    "target": "service-a",
                    "start": "2026-05-10T00:00:00Z",
                },
            },
        }
    )

    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["text"] == (
        "Both start and end must be provided together"
    )


def test_missing_time_scope_returns_tool_error() -> None:
    response = server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "get_metrics",
                "arguments": {
                    "target": "service-a",
                },
            },
        }
    )

    assert response["result"]["isError"] is True
    assert "Missing time scope" in response["result"]["content"][0]["text"]


def test_reversed_exact_window_returns_tool_error() -> None:
    response = server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "get_traces",
                "arguments": {
                    "target": "service-a",
                    "start": "2026-05-10T00:30:00Z",
                    "end": "2026-05-10T00:30:00Z",
                },
            },
        }
    )

    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["text"] == (
        "Invalid time scope: start must be before end "
        "(start=2026-05-10T00:30:00Z end=2026-05-10T00:30:00Z)"
    )


def test_get_logs_filters_warning_alias() -> None:
    with _mock_urlopen(_sample_result()):
        result = server._call_tool(
            "get_logs",
            {
                "target": "service-a",
                "lookback_minutes": 30,
                "severity": "warning",
            },
        )

    assert result["logs"] == [
        {
            "timestamp": "2026-05-10T00:00:01Z",
            "severity": "warn",
            "message": "warning line",
            "labels": {"pod": "service-a-1"},
        }
    ]


def test_get_aggregate_focuses_logs_on_active_error_route() -> None:
    payload = _sample_result()
    payload["metrics"]["series"] = [
        {
            "name": "http_error_rate",
            "labels": {"handler": "/crash", "job": "service-b", "status": "5xx"},
            "latest_value": 0.1,
            "peak_value": 0.2,
            "samples": [{"timestamp": "2026-05-10T00:00:10Z", "value": 0.2}],
        },
        {
            "name": "http_error_rate",
            "labels": {"handler": "/data", "job": "service-b", "status": "5xx"},
            "latest_value": 0.0,
            "peak_value": 0.0,
            "samples": [{"timestamp": "2026-05-10T00:00:10Z", "value": 0.0}],
        },
    ]
    payload["logs"]["lines"] = [
        {
            "timestamp": "2026-05-10T00:00:00Z",
            "severity": "error",
            "message": "DatabaseConnectionError: connection pool exhausted after 30s timeout",
            "labels": {"pod": "service-b-1"},
        },
        {
            "timestamp": "2026-05-10T00:00:10Z",
            "severity": "error",
            "message": (
                "Unhandled exception in payment processor:\n"
                "  File \"/app/app.py\", line 142, in crash\n"
                "ValueError: payment_processor.charge() received None for amount"
            ),
            "labels": {"pod": "service-b-1"},
        },
    ]

    with _mock_urlopen(payload):
        result = server._call_tool(
            "get_aggregate",
            {"target": "service-b", "lookback_minutes": 30},
        )

    assert "/crash" in result["focus_terms"]
    assert [line["message"] for line in result["aggregate"]["logs"]] == [
        payload["logs"]["lines"][1]["message"]
    ]
    assert result["aggregate"]["metrics"][0]["labels"]["handler"] == "/crash"


def test_get_logs_uses_trace_error_focus_terms_for_crash() -> None:
    payload = _sample_result()
    payload["metrics"]["series"] = []
    payload["logs"]["lines"] = [
        {
            "timestamp": "2026-05-10T00:00:00Z",
            "severity": "error",
            "message": "DatabaseConnectionError: connection pool exhausted after 30s timeout",
            "labels": {"pod": "service-b-1"},
        },
        {
            "timestamp": "2026-05-10T00:00:10Z",
            "severity": "error",
            "message": (
                "Unhandled exception in payment processor:\n"
                "  File \"/app/app.py\", line 142, in crash\n"
                "ValueError: payment_processor.charge() received None for amount"
            ),
            "labels": {"pod": "service-b-1"},
        },
    ]
    payload["traces"]["traces"] = [
        {
            "trace_id": "trace-crash",
            "root_service": "service-b",
            "duration_ms": 2,
            "has_errors": True,
            "spans": [
                {
                    "service_name": "service-b",
                    "operation_name": "GET /crash",
                    "duration_ms": 1,
                    "is_error": True,
                    "tags": {
                        "http.status_code": "500",
                        "otel.status_description": (
                            "ValueError: payment_processor.charge() received None for amount"
                        ),
                    },
                }
            ],
        }
    ]

    with _mock_urlopen(payload):
        result = server._call_tool(
            "get_logs",
            {"target": "service-b", "lookback_minutes": 30},
        )

    assert "/crash" in result["focus_terms"]
    assert "DatabaseConnectionError" not in json.dumps(result["logs"])
    assert "payment_processor.charge()" in result["logs"][0]["message"]


def test_latency_focus_excludes_stale_crash_logs_and_error_correlations() -> None:
    payload = _sample_result()
    payload["meta"]["target"] = "service-b"
    payload["meta"]["window_start"] = "2026-05-10T00:00:00Z"
    payload["meta"]["window_end"] = "2026-05-10T00:12:00Z"
    payload["metrics"]["series"] = [
        {
            "name": "http_error_rate",
            "labels": {"handler": "/crash", "job": "service-b", "status": "5xx"},
            "latest_value": 0,
            "peak_value": 0.4,
            "samples": [{"timestamp": "2026-05-10T00:00:00Z", "value": 0.4}],
        },
        {
            "name": "http_latency_p99",
            "labels": {"handler": "/data", "job": "service-b"},
            "latest_value": 2.0,
            "peak_value": 2.0,
            "samples": [{"timestamp": "2026-05-10T00:10:00Z", "value": 2.0}],
        },
    ]
    payload["logs"]["lines"] = [
        {
            "timestamp": "2026-05-10T00:00:00Z",
            "severity": "error",
            "message": (
                "Unhandled exception in payment processor:\n"
                "  File \"/app/app.py\", line 142, in crash\n"
                "ValueError: payment_processor.charge() received None for amount"
            ),
            "labels": {"pod": "service-b-1"},
        }
    ]
    payload["traces"] = {
        "error": None,
        "error_trace_count": 0,
        "p99_duration_ms": 2008,
        "traces": [
            {
                "trace_id": "trace-data-slow",
                "root_service": "service-b",
                "duration_ms": 2008,
                "has_errors": False,
                "spans": [
                    {
                        "service_name": "service-b",
                        "operation_name": "GET /data",
                        "duration_ms": 2008,
                        "is_error": False,
                        "start_time": "2026-05-10T00:10:00Z",
                        "tags": {"http.status_code": "200"},
                    }
                ],
            }
        ],
    }
    payload["correlations"] = [
        {
            "kind": "error_spike",
            "severity": "error",
            "description": "HTTP error rate peaked at 40%",
            "confidence": 0.9,
        },
        {
            "kind": "error_metric_log_correlation",
            "severity": "error",
            "description": "HTTP error spike and logs occurred together",
            "confidence": 0.85,
        },
        {
            "kind": "latency_spike",
            "severity": "warn",
            "description": "Trace p99 latency 2008 ms exceeds 1000 ms",
            "confidence": 0.64,
        },
    ]

    with _mock_urlopen(payload):
        result = server._call_tool(
            "get_aggregate",
            {
                "target": "service-b",
                "start": "2026-05-10T00:00:00Z",
                "end": "2026-05-10T00:12:00Z",
            },
        )

    assert result["focus_family"] == "latency"
    assert "/data" in result["focus_terms"]
    assert "payment_processor" not in json.dumps(result["aggregate"]["logs"])
    assert result["aggregate"]["logs"] == []
    assert all(
        "latency" in correlation["kind"]
        for correlation in result["aggregate"]["correlations"]
    )
    assert all(
        series["labels"].get("handler") != "/crash"
        for series in result["aggregate"]["metrics"]
    )


def test_tools_call_returns_text_content() -> None:
    with _mock_urlopen(_sample_result()):
        response = server._handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "get_traces",
                    "arguments": {
                        "target": "service-a",
                        "lookback_minutes": 30,
                        "errors_only": True,
                    },
                },
            }
        )

    content = response["result"]["content"][0]
    parsed = json.loads(content["text"])
    assert content["type"] == "text"
    assert response["result"]["isError"] is False
    assert parsed["tool"] == "get_traces"
    assert parsed["traces"][0]["trace_id"] == "trace-1"
    assert parsed["traces"][0]["spans"][0]["service_name"] == "service-a"


@contextmanager
def _mock_urlopen(payload: dict) -> Iterator[list]:
    calls = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

    def _fake_urlopen(req, timeout):
        calls.append(req)
        assert timeout == 30
        return _Response()

    with patch.object(server.request, "urlopen", side_effect=_fake_urlopen):
        yield calls


def _sample_result() -> dict:
    return {
        "meta": {
            "target": "service-a",
            "namespace": "default",
            "window_start": "2026-05-10T00:00:00Z",
            "window_end": "2026-05-10T00:30:00Z",
        },
        "metrics": {
            "error": None,
            "series": [
                {
                    "name": "http_error_rate",
                    "labels": {"job": "service-a"},
                    "latest_value": 0.2,
                    "peak_value": 0.5,
                    "samples": [{"timestamp": "2026-05-10T00:00:00Z", "value": 0.2}],
                }
            ],
        },
        "logs": {
            "error": None,
            "total_lines": 2,
            "error_count": 1,
            "warn_count": 1,
            "lines": [
                {
                    "timestamp": "2026-05-10T00:00:00Z",
                    "severity": "error",
                    "message": "error line",
                    "labels": {"pod": "service-a-1"},
                },
                {
                    "timestamp": "2026-05-10T00:00:01Z",
                    "severity": "warn",
                    "message": "warning line",
                    "labels": {"pod": "service-a-1"},
                },
            ],
        },
        "traces": {
            "error": None,
            "error_trace_count": 1,
            "p99_duration_ms": 1500,
            "traces": [
                {
                    "trace_id": "trace-1",
                    "root_service": "service-a",
                    "duration_ms": 1500,
                    "has_errors": True,
                    "spans": [
                        {
                            "service_name": "service-a",
                            "operation_name": "GET /api/data",
                            "duration_ms": 120,
                            "is_error": True,
                            "tags": {"http.status_code": "502"},
                        }
                    ],
                }
            ],
        },
        "correlations": [
            {
                "kind": "error_spike",
                "severity": "error",
                "description": "Errors spiked",
                "confidence": 0.9,
            }
        ],
    }
