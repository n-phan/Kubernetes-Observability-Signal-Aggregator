"""
RCA scenario tests.

Each test represents a realistic failure mode that the demo services
can actually produce. All Anthropic and GitHub API calls are mocked,
so these run without any credentials or network access.

Scenarios covered:
  1. Database connection pool exhaustion (service-b failing)
  2. Cascading latency (service-b slow → service-a timeout)
  3. Container crash loop (OOM / restart)
  4. Upstream error propagation (service-a 502s due to service-b 500s)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from aggregator.core.rca_analyzer import RCAAnalyzer, extract_stack_frames
from aggregator.clients.github import GitHubLinker, _blob_url, _normalise_path
from aggregator.models.result import QueryMeta, UnifiedResult
from aggregator.models.rca import RCAResult
from aggregator.models.signals import (
    LogLine,
    LogsSignal,
    MetricSample,
    MetricSeries,
    MetricsSignal,
    Severity,
    Span,
    Trace,
    TracesSignal,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

NOW = datetime.now(tz=timezone.utc)
REPO = "my-org/payment-service"
BRANCH = "main"


def _meta(target: str = "service-a") -> QueryMeta:
    return QueryMeta(
        target=target,
        namespace="default",
        window_start=NOW,
        window_end=NOW,
    )


def _metrics(*series: MetricSeries) -> MetricsSignal:
    return MetricsSignal(series=list(series))


def _series(name: str, value: float) -> MetricSeries:
    return MetricSeries(
        name=name,
        samples=[MetricSample(timestamp=NOW, value=value)],
    )


def _logs(*messages: tuple[str, Severity]) -> LogsSignal:
    lines = [LogLine(timestamp=NOW, message=msg, severity=sev) for msg, sev in messages]
    signal = LogsSignal(lines=lines)
    signal.compute_counts()
    return signal


def _error_log(msg: str) -> tuple[str, Severity]:
    return (msg, Severity.ERROR)


def _info_log(msg: str) -> tuple[str, Severity]:
    return (msg, Severity.INFO)


def _traces_with_error(operation: str, duration_ms: float = 200.0) -> TracesSignal:
    span = Span(
        trace_id="abc123",
        span_id="span001",
        operation_name=operation,
        service_name="service-b",
        start_time=NOW,
        duration_us=int(duration_ms * 1000),
        is_error=True,
        tags={"error": "true", "error.message": "connection refused"},
    )
    signal = TracesSignal(traces=[Trace(trace_id="abc123", spans=[span])])
    signal.compute_stats()
    return signal


def _make_result(
    target: str = "service-a",
    metrics: MetricsSignal | None = None,
    logs: LogsSignal | None = None,
    traces: TracesSignal | None = None,
) -> UnifiedResult:
    return UnifiedResult(
        meta=_meta(target),
        metrics=metrics or MetricsSignal(),
        logs=logs or LogsSignal(),
        traces=traces or TracesSignal(),
    )


def _llm_response(**kwargs) -> str:
    """Build a minimal valid LLM JSON response with overridable fields."""
    base = {
        "summary": "Test summary",
        "root_cause": "Test root cause",
        "confidence": 0.8,
        "supporting_evidence": ["Evidence A"],
        "recommended_actions": [
            {"priority": 1, "action": "Fix it", "rationale": "It is broken"}
        ],
        "github_search_terms": ["some_function"],
    }
    base.update(kwargs)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# Scenario 1: Database connection pool exhaustion
#
# service-b returns 500 with "connection pool exhausted" log messages.
# RCA should identify the pool as the root cause and point to the
# connection management code in GitHub.
# ---------------------------------------------------------------------------

class TestScenario1_ConnectionPoolExhaustion:
    """service-b pool exhausted → service-a 502s → aggregator detects error cascade."""

    def _build_result(self) -> UnifiedResult:
        return _make_result(
            metrics=_metrics(
                _series("http_error_rate", 0.45),
                _series("restart_count", 0.0),
            ),
            logs=_logs(
                _error_log(
                    'DatabaseConnectionError: connection pool exhausted after 30s timeout\n'
                    '  File "service_b/db/pool.py", line 87, in acquire\n'
                    '  File "service_b/db/pool.py", line 112, in execute_query'
                ),
                _error_log("SQLSTATE[08006]: connection to server lost"),
                _info_log("GET /data 500"),
                _error_log(
                    'DatabaseConnectionError: connection pool exhausted after 30s timeout\n'
                    '  File "service_b/db/pool.py", line 87, in acquire'
                ),
                *[_info_log(f"GET /data 200 request {i}") for i in range(6)],
            ),
            traces=_traces_with_error("db.query", duration_ms=31_000),
        )

    @pytest.mark.asyncio
    async def test_rca_identifies_pool_exhaustion(self):
        result = self._build_result()
        analyzer = RCAAnalyzer(api_key="test-key", repo=REPO)
        mock_response = _llm_response(
            summary="Database connection pool exhausted under load",
            root_cause=(
                "service-b's connection pool is too small for the current request volume. "
                "Connections are not being released promptly, causing new requests to wait "
                "30 seconds before failing."
            ),
            confidence=0.92,
            supporting_evidence=[
                "http_error_rate peaked at 0.45 (threshold 0.01)",
                "4 ERROR log lines contain 'connection pool exhausted'",
                "Jaeger span db.query took 31,000 ms (30s = pool wait timeout)",
            ],
            recommended_actions=[
                {
                    "priority": 1,
                    "action": "Increase DB_POOL_SIZE environment variable in service-b",
                    "rationale": "More connections reduces queuing under load",
                },
                {
                    "priority": 1,
                    "action": "Ensure connections are released in finally blocks",
                    "rationale": "Leaked connections deplete the pool permanently",
                },
                {
                    "priority": 2,
                    "action": "Add pool utilisation metric to Prometheus",
                    "rationale": "Pool saturation should alert before total exhaustion",
                },
            ],
            github_search_terms=[
                "connection pool exhausted",
                "DB_POOL_SIZE",
                "acquire",
                "pool.py",
            ],
        )

        with patch.object(analyzer, "_call_llm", new=AsyncMock(return_value=mock_response)):
            rca = await analyzer.analyze(result)

        assert rca.performed
        assert rca.confidence == 0.92
        assert any("pool" in e.lower() for e in rca.supporting_evidence)
        assert any(a.priority == 1 for a in rca.recommended_actions)
        assert "pool.py" in rca.github_search_terms

    @pytest.mark.asyncio
    async def test_stack_traces_produce_github_links(self):
        result = self._build_result()
        frames = extract_stack_frames(result.logs.lines)

        assert len(frames) >= 2
        paths = [f[0] for f in frames]
        assert any("pool.py" in p for p in paths)

        linker = GitHubLinker(token="test", repo=REPO, default_branch=BRANCH)
        enriched = linker._link_stack_frames(result.logs)

        assert any("pool.py" in ref.path for ref in enriched)
        url = [r.url for r in enriched if "pool.py" in r.path][0]
        assert REPO in url
        assert "blob/main" in url

    @pytest.mark.asyncio
    async def test_github_search_called_with_pool_terms(self):
        linker = GitHubLinker(token="test", repo=REPO)
        rca = RCAResult(
            performed=True,
            github_search_terms=["DB_POOL_SIZE", "connection pool exhausted"],
        )

        mock_items = [
            {"path": "service_b/db/pool.py", "text_matches": [{"fragment": "MAX_POOL_SIZE = 5"}]},
            {"path": "service_b/db/connection.py", "text_matches": []},
        ]

        async def fake_search(term):
            return [
                type("Ref", (), {
                    "url": _blob_url(REPO, BRANCH, item["path"]),
                    "path": item["path"],
                    "repo": REPO,
                    "line_number": None,
                    "snippet": (item["text_matches"] or [{}])[0].get("fragment"),
                    "relevance": f"Matched: {term}",
                })()
                for item in mock_items
            ]

        with patch.object(linker, "_search_one", side_effect=fake_search):
            enriched = await linker.enrich(rca, LogsSignal())

        assert any("pool.py" in ref.path for ref in enriched.code_references)


# ---------------------------------------------------------------------------
# Scenario 2: Cascading latency — service-b slow, service-a times out
#
# service-b's /slow endpoint adds 2.5 s per call. service-a has a 5 s
# timeout, so requests succeed but p99 latency spikes. RCA should
# identify slow database queries as the cause.
# ---------------------------------------------------------------------------

class TestScenario2_CascadingLatency:

    def _build_result(self) -> UnifiedResult:
        slow_span = Span(
            trace_id="trace-slow",
            span_id="span-slow",
            operation_name="GET /slow",
            service_name="service-b",
            start_time=NOW,
            duration_us=2_500_000,
            is_error=False,
            tags={"db.statement": "SELECT * FROM orders WHERE user_id = ?"},
        )
        outer_span = Span(
            trace_id="trace-slow",
            span_id="span-outer",
            operation_name="GET /api/slow",
            service_name="service-a",
            start_time=NOW,
            duration_us=2_510_000,
            is_error=False,
        )
        trace = Trace(trace_id="trace-slow", spans=[outer_span, slow_span])
        traces = TracesSignal(traces=[trace])
        traces.compute_stats()

        return _make_result(
            metrics=_metrics(
                _series("http_latency_p99", 2.51),
                _series("http_error_rate", 0.0),
            ),
            logs=_logs(
                _info_log("slow query completed: table scan on orders (no index on created_at)"),
                _info_log("slow query completed: table scan on orders (no index on created_at)"),
                _info_log("GET /api/slow 200 in 2512ms"),
            ),
            traces=traces,
        )

    @pytest.mark.asyncio
    async def test_rca_identifies_missing_index(self):
        result = self._build_result()
        analyzer = RCAAnalyzer(api_key="test-key", repo=REPO)
        mock_response = _llm_response(
            summary="p99 latency at 2.5 s caused by full table scan on orders table",
            root_cause=(
                "service-b performs a SELECT on the orders table filtered by user_id "
                "without an index on that column. This causes a full table scan on every "
                "request, taking 2.5 s per call."
            ),
            confidence=0.87,
            supporting_evidence=[
                "http_latency_p99 = 2.51 s (threshold 1.0 s)",
                "Jaeger span GET /slow: 2,500,000 µs",
                "Log: 'table scan on orders (no index on created_at)'",
            ],
            recommended_actions=[
                {
                    "priority": 1,
                    "action": "Add index: CREATE INDEX idx_orders_user_id ON orders(user_id)",
                    "rationale": "Converts full table scan to O(log n) index lookup",
                },
                {
                    "priority": 2,
                    "action": "Enable slow query logging in service-b",
                    "rationale": "Catch future regressions before they reach production",
                },
            ],
            github_search_terms=["table scan", "orders", "created_at", "slow_query"],
        )

        with patch.object(analyzer, "_call_llm", new=AsyncMock(return_value=mock_response)):
            rca = await analyzer.analyze(result)

        assert rca.performed
        assert rca.confidence > 0.8
        assert any("index" in a.action.lower() for a in rca.recommended_actions)
        assert "orders" in rca.github_search_terms

    @pytest.mark.asyncio
    async def test_rca_skips_when_no_error_signals(self):
        """RCA should not run when no error or latency signal is present."""
        result = self._build_result()
        # Override: no error logs, no error traces, no latency or error metric spike.
        result.logs = _logs(*[_info_log(f"ok {i}") for i in range(5)])
        result.metrics = _metrics(_series("http_error_rate", 0.0))
        result.traces = _traces_with_error("GET /api", duration_ms=100.0)
        result.traces.traces[0].spans[0].is_error = False
        result.traces.compute_stats()

        analyzer = RCAAnalyzer(api_key="test-key")
        rca = await analyzer.analyze(result)

        assert not rca.performed
        assert rca.error is None  # skipped cleanly, not an error


# ---------------------------------------------------------------------------
# Scenario 3: Container crash loop — OOM
#
# service-b's /oom endpoint leaks memory. Prometheus sees restart_count
# climbing. Logs contain Python MemoryError tracebacks. RCA should
# identify the leak and link to the oom endpoint code.
# ---------------------------------------------------------------------------

class TestScenario3_OOMCrashLoop:

    def _build_result(self) -> UnifiedResult:
        return _make_result(
            metrics=_metrics(
                _series("restart_count", 3.0),
                _series("memory_bytes", 980 * 1024 * 1024),  # 980 MB
                _series("http_error_rate", 0.12),
            ),
            logs=_logs(
                _error_log(
                    "MemoryError: unable to allocate 10485760 bytes\n"
                    '  File "service_b/app.py", line 51, in oom\n'
                    '    chunk = b"x" * (10 * 1024 * 1024)'
                ),
                _error_log("Container killed by OOM killer (exit code 137)"),
                _info_log("allocated 10 MB, total leaked: 980 MB"),
                _error_log(
                    "MemoryError: unable to allocate 10485760 bytes\n"
                    '  File "service_b/app.py", line 51, in oom'
                ),
                *[_info_log(f"GET /oom 200 leaked {i*10} MB") for i in range(8)],
            ),
        )

    @pytest.mark.asyncio
    async def test_rca_identifies_memory_leak(self):
        result = self._build_result()
        analyzer = RCAAnalyzer(api_key="test-key", repo=REPO)
        mock_response = _llm_response(
            summary="Container OOM crash loop caused by unbounded memory leak in /oom endpoint",
            root_cause=(
                "The /oom endpoint in service-b appends 10 MB byte objects to a module-level "
                "list (_leak_store) without ever releasing them. After ~100 calls the process "
                "exceeds its memory limit and is killed by the OOM killer (exit 137)."
            ),
            confidence=0.97,
            supporting_evidence=[
                "restart_count = 3 in window",
                "memory_bytes = 980 MB (near container limit)",
                "MemoryError traceback points to service_b/app.py line 51",
                "Log: 'Container killed by OOM killer (exit code 137)'",
            ],
            recommended_actions=[
                {
                    "priority": 1,
                    "action": "Remove or gate the /oom endpoint behind an env flag",
                    "rationale": "Leak endpoint should never be enabled in production",
                },
                {
                    "priority": 1,
                    "action": "Clear _leak_store or use a bounded deque",
                    "rationale": "Prevents unbounded growth even in non-production environments",
                },
                {
                    "priority": 2,
                    "action": "Set memory limits in docker-compose and Kubernetes PodSpec",
                    "rationale": "Container limits make OOM kills predictable and fast",
                },
            ],
            github_search_terms=["_leak_store", "oom", "OOM_ENDPOINT", "app.py"],
        )

        with patch.object(analyzer, "_call_llm", new=AsyncMock(return_value=mock_response)):
            rca = await analyzer.analyze(result)

        assert rca.performed
        assert rca.confidence > 0.95
        p1_actions = [a for a in rca.recommended_actions if a.priority == 1]
        assert len(p1_actions) >= 2
        assert "app.py" in rca.github_search_terms

    @pytest.mark.asyncio
    async def test_stack_trace_links_to_oom_line(self):
        result = self._build_result()
        frames = extract_stack_frames(result.logs.lines)

        # Should have extracted line 51 in app.py from the MemoryError traceback
        app_frames = [(p, ln) for p, ln in frames if "app.py" in p]
        assert len(app_frames) >= 1

        path, lineno = app_frames[0]
        assert lineno == 51

        url = _blob_url(REPO, BRANCH, path, lineno)
        assert "#L51" in url

    @pytest.mark.asyncio
    async def test_restart_count_triggers_rca(self):
        """Even with no error logs, a non-zero restart_count should trigger RCA via correlator."""
        from aggregator.core.correlator import Correlator
        result = self._build_result()
        correlator = Correlator()
        events = correlator.correlate(result.metrics, result.logs, result.traces)
        kinds = {e.kind for e in events}
        assert "container_restart" in kinds


# ---------------------------------------------------------------------------
# Scenario 4: Upstream error propagation
#
# service-a is healthy internally but returns 502 because service-b
# is failing. Without a circuit breaker, every request fans into a
# failing downstream. RCA should flag service-b as the culprit and
# suggest enabling the circuit breaker.
# ---------------------------------------------------------------------------

class TestScenario4_UpstreamErrorPropagation:

    def _build_result(self) -> UnifiedResult:
        return _make_result(
            metrics=_metrics(
                _series("http_error_rate", 0.55),   # service-a returning 502s
                _series("http_latency_p99", 5.1),   # retry backoff inflating p99
            ),
            logs=_logs(
                _error_log("service-b returned 500 on attempt 1/1"),
                _error_log("service-b returned 500 on attempt 1/1"),
                _error_log("service-b returned 500 on attempt 1/1"),
                _info_log("circuit_breaker_enabled=false retry_enabled=false"),
                _error_log("service-b returned 500 on attempt 1/1"),
                _error_log("service-b returned 500 on attempt 1/1"),
                *[_info_log(f"GET /api/data 502 request {i}") for i in range(5)],
            ),
            traces=_traces_with_error("GET /data", duration_ms=5100),
        )

    @pytest.mark.asyncio
    async def test_rca_blames_downstream_not_upstream(self):
        result = self._build_result()
        analyzer = RCAAnalyzer(api_key="test-key", repo=REPO)
        mock_response = _llm_response(
            summary="service-a 502 errors caused by service-b returning 500 on all requests",
            root_cause=(
                "service-b is returning HTTP 500 for all requests to GET /data. "
                "service-a has no circuit breaker enabled, so it passes every 502 "
                "through to clients without protecting against the failing downstream."
            ),
            confidence=0.91,
            supporting_evidence=[
                "http_error_rate = 0.55 (55% of requests failing)",
                "All ERROR log lines reference 'service-b returned 500'",
                "Log confirms circuit_breaker_enabled=false",
                "Jaeger span GET /data: is_error=true, 5100 ms",
            ],
            recommended_actions=[
                {
                    "priority": 1,
                    "action": "Set ENABLE_CIRCUIT_BREAKER=true in service-a",
                    "rationale": "Stops hammering a failing downstream and returns fast failures",
                },
                {
                    "priority": 1,
                    "action": "Investigate root cause of service-b 500 errors",
                    "rationale": "service-a errors are a symptom — service-b is the root cause",
                },
                {
                    "priority": 2,
                    "action": "Set ENABLE_RETRY=true with exponential backoff",
                    "rationale": "Retries recover from transient failures without manual intervention",
                },
            ],
            github_search_terms=[
                "ENABLE_CIRCUIT_BREAKER",
                "circuit_open",
                "_record_error",
                "app.py",
            ],
        )

        with patch.object(analyzer, "_call_llm", new=AsyncMock(return_value=mock_response)):
            rca = await analyzer.analyze(result)

        assert rca.performed
        # Root cause should implicate service-b, not service-a
        assert "service-b" in rca.root_cause.lower()
        # Circuit breaker should be the top recommendation
        cb_actions = [a for a in rca.recommended_actions if "circuit" in a.action.lower()]
        assert len(cb_actions) >= 1
        assert cb_actions[0].priority == 1

    @pytest.mark.asyncio
    async def test_cross_correlation_fires(self):
        """The correlator should detect the error rate + log error burst together."""
        from aggregator.core.correlator import Correlator
        result = self._build_result()
        correlator = Correlator()
        events = correlator.correlate(result.metrics, result.logs, result.traces)
        kinds = {e.kind for e in events}
        assert "error_spike" in kinds
        assert "error_metric_log_correlation" in kinds

    @pytest.mark.asyncio
    async def test_github_links_to_circuit_breaker_code(self):
        """GitHub linker should find the circuit breaker implementation."""
        linker = GitHubLinker(token="test", repo=REPO)
        rca = RCAResult(
            performed=True,
            github_search_terms=["ENABLE_CIRCUIT_BREAKER", "_record_error"],
        )

        mock_items = [
            {"path": "demo/service-a/app.py", "text_matches": [
                {"fragment": "ENABLE_CIRCUIT_BREAKER = os.getenv('ENABLE_CIRCUIT_BREAKER')"}
            ]},
        ]

        async def fake_search(term):
            return [
                type("Ref", (), {
                    "url": _blob_url(REPO, BRANCH, item["path"]),
                    "path": item["path"],
                    "repo": REPO,
                    "line_number": None,
                    "snippet": (item["text_matches"] or [{}])[0].get("fragment"),
                    "relevance": f"Matched: {term}",
                })()
                for item in mock_items
            ]

        with patch.object(linker, "_search_one", side_effect=fake_search):
            enriched = await linker.enrich(rca, LogsSignal())

        assert any("service-a" in ref.path for ref in enriched.code_references)
        service_a_refs = [r for r in enriched.code_references if "service-a" in r.path]
        assert "ENABLE_CIRCUIT_BREAKER" in service_a_refs[0].snippet


# ---------------------------------------------------------------------------
# Unit tests for helper utilities
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_normalise_path_strips_workspace_prefix(self):
        assert _normalise_path("/workspace/service_b/db/pool.py") == "service_b/db/pool.py"
        assert _normalise_path("/app/handler.py") == "handler.py"

    def test_blob_url_with_line_number(self):
        url = _blob_url("org/repo", "main", "src/app.py", 42)
        assert url == "https://github.com/org/repo/blob/main/src/app.py#L42"

    def test_blob_url_without_line_number(self):
        url = _blob_url("org/repo", "main", "src/app.py")
        assert url == "https://github.com/org/repo/blob/main/src/app.py"

    def test_extract_python_stack_frames(self):
        lines = [
            LogLine(
                timestamp=NOW,
                message=(
                    'Traceback (most recent call last):\n'
                    '  File "service_b/db/pool.py", line 87, in acquire\n'
                    '  File "service_b/handlers/request.py", line 34, in handle'
                ),
                severity=Severity.ERROR,
            )
        ]
        frames = extract_stack_frames(lines)
        paths = {p for p, _ in frames}
        line_numbers = {ln for _, ln in frames}
        assert "service_b/db/pool.py" in paths
        assert "service_b/handlers/request.py" in paths
        assert 87 in line_numbers
        assert 34 in line_numbers

    def test_extract_deduplicates_repeated_frames(self):
        msg = 'File "app.py", line 51, in oom'
        lines = [
            LogLine(timestamp=NOW, message=msg, severity=Severity.ERROR),
            LogLine(timestamp=NOW, message=msg, severity=Severity.ERROR),
        ]
        frames = extract_stack_frames(lines)
        assert len(frames) == 1

    def test_rca_parse_strips_markdown_fences(self):
        analyzer = RCAAnalyzer(api_key="test")
        raw = "```json\n" + _llm_response(summary="Fence test") + "\n```"
        result = analyzer._parse_response(raw)
        assert result.summary == "Fence test"

    def test_rca_parse_accepts_log_evidence(self):
        analyzer = RCAAnalyzer(api_key="test")
        raw = _llm_response(
            log_evidence=[
                {
                    "timestamp": "2026-05-02T19:12:34Z",
                    "severity": "error",
                    "message": "DatabaseConnectionError: connection pool exhausted",
                    "relevance": "The log names the exhausted resource",
                    "labels": {"service": "service-b"},
                }
            ]
        )
        result = analyzer._parse_response(raw)

        assert (
            result.log_evidence[0].message
            == "DatabaseConnectionError: connection pool exhausted"
        )
        assert result.log_evidence[0].labels["service"] == "service-b"
        assert "log_evidence" in result.model_fields_set

    def test_rca_parse_preserves_explicit_empty_log_evidence(self):
        analyzer = RCAAnalyzer(api_key="test")
        raw = _llm_response(log_evidence=[])
        result = analyzer._parse_response(raw)

        assert result.log_evidence == []
        assert "log_evidence" in result.model_fields_set

    def test_rca_parse_handles_missing_optional_fields(self):
        analyzer = RCAAnalyzer(api_key="test")
        minimal = json.dumps({
            "summary": "Minimal",
            "root_cause": "Unknown",
            "confidence": 0.5,
        })
        result = analyzer._parse_response(minimal)
        assert result.summary == "Minimal"
        assert result.supporting_evidence == []
        assert result.log_evidence == []
        assert "log_evidence" not in result.model_fields_set
        assert result.github_search_terms == []
