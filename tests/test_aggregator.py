"""
Unit tests for SignalAggregator, Correlator, and related helpers.

Uses pytest-asyncio for async tests and unittest.mock to stub clients.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console

from aggregator.clients.loki import _group_multiline, _severity_from_message
from aggregator.core.aggregator import SignalAggregator
from aggregator.core.correlator import Correlator
from aggregator.clients.prometheus import _parse_sample_value
from aggregator.core.hermes_rca_agent import (
    HermesRCAAgent,
    _build_incident_dossier,
    _summarize_logs,
    _tool_schemas,
)
from aggregator.core.rca_analyzer import RCAAnalyzer
from aggregator.core.rca_followup import RcaFollowUpAssistant, _build_followup_messages
from aggregator.core.suspicious_absence import SuspiciousAbsenceDetector
from aggregator.output.formatter import RichFormatter
from aggregator.models.followup import FollowUpMessage, FollowUpRequest
from aggregator.models.query import QueryRequest, TimeWindow
from aggregator.models.rca import RCAResult
from aggregator.models.result import CorrelationEvent, QueryMeta, UnifiedResult
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
# Fixtures
# ---------------------------------------------------------------------------


def make_window() -> TimeWindow:
    return TimeWindow.from_lookback(30)


def make_metrics(error_rate: float = 0.0, has_restart: bool = False) -> MetricsSignal:
    now = datetime.now(tz=timezone.utc)
    series = [
        MetricSeries(
            name="http_error_rate",
            samples=[MetricSample(timestamp=now, value=error_rate)],
        )
    ]
    if has_restart:
        series.append(
            MetricSeries(
                name="restart_count",
                samples=[MetricSample(timestamp=now, value=1.0)],
            )
        )
    return MetricsSignal(series=series)


def make_traffic_metrics(request_rate: float = 1.0) -> MetricsSignal:
    now = datetime.now(tz=timezone.utc)
    return MetricsSignal(
        series=[
            MetricSeries(
                name="http_requests_per_second",
                samples=[MetricSample(timestamp=now, value=request_rate)],
            )
        ]
    )


def make_logs(error_count: int = 0, total: int = 10) -> LogsSignal:
    lines: list[LogLine] = []
    for i in range(total):
        severity = Severity.ERROR if i < error_count else Severity.INFO
        lines.append(
            LogLine(
                timestamp=datetime.now(tz=timezone.utc),
                message=f"log line {i}",
                severity=severity,
            )
        )
    signal = LogsSignal(lines=lines)
    signal.compute_counts()
    return signal


def make_traces(count: int = 1, has_errors: bool = False, duration_ms: float = 100.0) -> TracesSignal:
    traces = []
    for i in range(count):
        span = Span(
            trace_id=f"trace{i:04d}",
            span_id=f"span{i:04d}",
            operation_name="GET /api",
            service_name="my-api",
            start_time=datetime.now(tz=timezone.utc),
            duration_us=int(duration_ms * 1000),
            is_error=has_errors,
        )
        traces.append(Trace(trace_id=f"trace{i:04d}", spans=[span]))
    signal = TracesSignal(traces=traces)
    signal.compute_stats()
    return signal


# ---------------------------------------------------------------------------
# Correlator unit tests
# ---------------------------------------------------------------------------


class TestCorrelator:
    def setup_method(self) -> None:
        self.correlator = Correlator()

    def test_no_events_when_healthy(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(error_rate=0.0),
            logs=make_logs(error_count=0),
            traces=make_traces(count=1, duration_ms=100.0),
        )
        assert events == []

    def test_detects_error_rate_spike(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(error_rate=0.05),
            logs=make_logs(),
            traces=make_traces(),
        )
        kinds = {e.kind for e in events}
        assert "error_spike" in kinds

    def test_detects_restart(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(has_restart=True),
            logs=make_logs(),
            traces=make_traces(),
        )
        kinds = {e.kind for e in events}
        assert "container_restart" in kinds

    def test_detects_log_error_burst(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(),
            logs=make_logs(error_count=8, total=10),
            traces=make_traces(),
        )
        kinds = {e.kind for e in events}
        assert "log_error_burst" in kinds

    def test_cross_correlation_errors_and_logs(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(error_rate=0.05),
            logs=make_logs(error_count=5, total=10),
            traces=make_traces(),
        )
        kinds = {e.kind for e in events}
        assert "error_metric_log_correlation" in kinds

    def test_events_sorted_by_severity(self) -> None:
        events = self.correlator.correlate(
            metrics=make_metrics(error_rate=0.05),
            logs=make_logs(error_count=9, total=10),
            traces=make_traces(duration_ms=2000.0),
        )
        severities = [e.severity for e in events]
        # All "error" events must come before any "warn" events
        error_positions = [i for i, s in enumerate(severities) if s == "error"]
        warn_positions = [i for i, s in enumerate(severities) if s == "warn"]
        if error_positions and warn_positions:
            assert max(error_positions) < min(warn_positions)


class TestSuspiciousAbsenceDetector:
    def setup_method(self) -> None:
        self.detector = SuspiciousAbsenceDetector()

    def test_detects_unavailable_backends(self) -> None:
        events = self.detector.detect(
            MetricsSignal(error="Prometheus down"),
            LogsSignal(error="Loki down"),
            TracesSignal(error="Jaeger down"),
        )

        assert {event.kind for event in events} == {
            "metrics_unavailable",
            "logs_unavailable",
            "traces_unavailable",
        }

    def test_detects_traffic_without_logs_or_traces(self) -> None:
        events = self.detector.detect(
            make_traffic_metrics(),
            LogsSignal(),
            TracesSignal(),
        )

        assert {event.kind for event in events} == {
            "traffic_without_logs",
            "traffic_without_traces",
        }

    def test_detects_activity_without_metrics(self) -> None:
        events = self.detector.detect(
            MetricsSignal(),
            make_logs(error_count=0, total=3),
            TracesSignal(),
        )

        assert {event.kind for event in events} == {"activity_without_metrics"}

    def test_disabled_signals_do_not_create_gap_events(self) -> None:
        events = self.detector.detect(
            make_traffic_metrics(),
            LogsSignal(),
            TracesSignal(),
            include_logs=False,
            include_traces=False,
        )

        assert events == []


# ---------------------------------------------------------------------------
# SignalAggregator integration tests (with mocked clients)
# ---------------------------------------------------------------------------


class TestSignalAggregator:
    def _make_aggregator(
        self,
        metrics: MetricsSignal | None = None,
        logs: LogsSignal | None = None,
        traces: TracesSignal | None = None,
    ) -> SignalAggregator:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=metrics or make_metrics())
        prometheus.close = AsyncMock()

        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=logs or make_logs())
        loki.close = AsyncMock()

        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(return_value=traces or make_traces())
        jaeger.close = AsyncMock()

        return SignalAggregator(prometheus=prometheus, loki=loki, jaeger=jaeger)

    @pytest.mark.asyncio
    async def test_basic_query_returns_unified_result(self) -> None:
        agg = self._make_aggregator()
        request = QueryRequest(target="my-api", namespace="default", lookback_minutes=30)
        result = await agg.query(request)

        assert result.meta.target == "my-api"
        assert result.meta.namespace == "default"
        assert result.metrics.series
        assert result.logs.lines
        assert result.traces.traces

    @pytest.mark.asyncio
    async def test_backend_error_does_not_abort_query(self) -> None:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(side_effect=RuntimeError("Prometheus down"))
        prometheus.close = AsyncMock()

        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs())
        loki.close = AsyncMock()

        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(return_value=make_traces())
        jaeger.close = AsyncMock()

        agg = SignalAggregator(prometheus=prometheus, loki=loki, jaeger=jaeger)
        request = QueryRequest(target="my-api")
        result = await agg.query(request)

        assert result.metrics.error is not None
        assert "Prometheus down" in result.metrics.error
        assert result.logs.lines  # logs still present

    @pytest.mark.asyncio
    async def test_include_flags_skip_backends(self) -> None:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=make_metrics())
        prometheus.close = AsyncMock()

        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs())
        loki.close = AsyncMock()

        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(return_value=make_traces())
        jaeger.close = AsyncMock()

        agg = SignalAggregator(prometheus=prometheus, loki=loki, jaeger=jaeger)
        request = QueryRequest(target="my-api", include_metrics=False, include_traces=False)
        await agg.query(request)

        prometheus.query_metrics.assert_not_called()
        jaeger.query_traces.assert_not_called()
        loki.query_logs.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_adds_suspicious_absence_correlations(self) -> None:
        agg = self._make_aggregator(
            metrics=make_traffic_metrics(),
            logs=LogsSignal(),
            traces=TracesSignal(),
        )
        request = QueryRequest(target="my-api", namespace="default", lookback_minutes=30)
        result = await agg.query(request)

        kinds = {event.kind for event in result.correlations}
        assert "traffic_without_logs" in kinds
        assert "traffic_without_traces" in kinds

    @pytest.mark.asyncio
    async def test_disabled_signal_does_not_create_absence_correlation(self) -> None:
        agg = self._make_aggregator(
            metrics=make_traffic_metrics(),
            logs=make_logs(error_count=0, total=2),
            traces=TracesSignal(),
        )
        request = QueryRequest(
            target="my-api",
            namespace="default",
            lookback_minutes=30,
            include_traces=False,
        )
        result = await agg.query(request)

        assert "traffic_without_traces" not in {event.kind for event in result.correlations}

    @pytest.mark.asyncio
    async def test_rca_falls_back_when_primary_agent_fails(self) -> None:
        primary = MagicMock()
        primary.analyze = AsyncMock(
            return_value=RCAResult(performed=False, error="Hermes unavailable")
        )
        primary.close = AsyncMock()

        fallback = MagicMock()
        fallback.analyze = AsyncMock(
            return_value=RCAResult(
                performed=True,
                summary="Fallback RCA",
                root_cause="Fallback root cause",
                confidence=0.7,
            )
        )
        fallback.close = AsyncMock()

        agg = self._make_aggregator(logs=make_logs(error_count=5, total=10))
        agg._rca_analyzer = primary
        agg._fallback_rca_analyzer = fallback

        request = QueryRequest(target="my-api", include_rca=True)
        result = await agg.query(request)

        assert result.rca.performed
        assert result.rca.summary == "Fallback RCA"
        assert result.rca.log_evidence
        assert result.rca.log_evidence[0].severity == "error"
        primary.analyze.assert_awaited_once()
        fallback.analyze.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rca_keeps_primary_error_when_fallback_skips(self) -> None:
        primary = MagicMock()
        primary.analyze = AsyncMock(
            return_value=RCAResult(performed=False, error="Hermes unavailable")
        )
        primary.close = AsyncMock()

        fallback = MagicMock()
        fallback.analyze = AsyncMock(return_value=RCAResult(performed=False))
        fallback.close = AsyncMock()

        agg = self._make_aggregator(logs=make_logs(error_count=5, total=10))
        agg._rca_analyzer = primary
        agg._fallback_rca_analyzer = fallback

        request = QueryRequest(target="my-api", include_rca=True)
        result = await agg.query(request)

        assert not result.rca.performed
        assert result.rca.error == "Hermes unavailable"
        primary.analyze.assert_awaited_once()
        fallback.analyze.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_preserves_explicit_empty_log_evidence(self) -> None:
        analyzer = MagicMock()
        analyzer.analyze = AsyncMock(
            return_value=RCAResult(
                performed=True,
                summary="Trace-only RCA",
                root_cause="A trace span exceeded the latency threshold.",
                confidence=0.76,
                log_evidence=[],
            )
        )
        analyzer.close = AsyncMock()

        agg = self._make_aggregator(logs=make_logs(error_count=5, total=10))
        agg._rca_analyzer = analyzer

        request = QueryRequest(target="my-api", include_rca=True)
        result = await agg.query(request)

        assert result.rca.performed
        assert result.rca.log_evidence == []
        assert "log_evidence" in result.rca.model_fields_set

    @pytest.mark.asyncio
    async def test_legacy_missing_log_evidence_ignores_info_logs(self) -> None:
        analyzer = MagicMock()
        analyzer.analyze = AsyncMock(
            return_value=RCAResult(
                performed=True,
                summary="Metric-only RCA",
                root_cause="A metric exceeded the incident threshold.",
                confidence=0.72,
            )
        )
        analyzer.close = AsyncMock()

        agg = self._make_aggregator(logs=make_logs(error_count=0, total=10))
        agg._rca_analyzer = analyzer

        request = QueryRequest(target="my-api", include_rca=True)
        result = await agg.query(request)

        assert result.rca.performed
        assert result.rca.log_evidence == []

    @pytest.mark.asyncio
    async def test_enrichment_and_log_backfill_apply_to_final_hermes_result(self) -> None:
        logs = make_logs(error_count=5, total=10)
        github_linker = MagicMock()
        github_linker.close = AsyncMock()

        def _enrich(rca: RCAResult, _: LogsSignal) -> RCAResult:
            enriched = rca.model_copy(deep=True)
            enriched.summary = f"{rca.summary} + enriched"
            return enriched

        github_linker.enrich = AsyncMock(side_effect=_enrich)

        agg = self._make_aggregator(logs=logs)
        agg._github_linker = github_linker
        agent = HermesRCAAgent(
            api_url="http://hermes.test/v1",
            model="hermes-agent",
            prometheus=agg._prometheus,
            loki=agg._loki,
            jaeger=agg._jaeger,
        )
        rca = RCAResult(
            performed=True,
            summary="Hermes RCA",
            root_cause="Hermes root cause",
            confidence=0.91,
        )
        agent.analyze = AsyncMock(return_value=rca)
        agg._rca_analyzer = agent

        request = QueryRequest(target="my-api", include_rca=True)
        result = await agg.query(request)
        await agent.close()

        assert result.rca.summary == "Hermes RCA + enriched"
        assert result.rca.log_evidence
        assert github_linker.enrich.await_count == 1
        assert github_linker.enrich.await_args.args[0].summary == "Hermes RCA"

    @pytest.mark.asyncio
    async def test_follow_up_requires_completed_rca(self) -> None:
        agg = self._make_aggregator()
        incident = UnifiedResult(
            meta=QueryMeta(
                target="my-api",
                namespace="default",
                window_start=datetime.now(tz=timezone.utc) - timedelta(minutes=30),
                window_end=datetime.now(tz=timezone.utc),
            ),
            metrics=make_metrics(),
            logs=make_logs(),
            traces=make_traces(),
        )

        with pytest.raises(ValueError, match="RCA must be performed"):
            await agg.follow_up(incident=incident, question="What now?", history=[])


# ---------------------------------------------------------------------------
# RCA follow-up assistant tests
# ---------------------------------------------------------------------------


class TestRcaFollowUpAssistant:
    def _incident(self) -> UnifiedResult:
        now = datetime.now(tz=timezone.utc)
        logs = make_logs(error_count=2, total=4)
        metrics = make_metrics(error_rate=0.2)
        traces = make_traces(count=1, has_errors=True, duration_ms=1400)
        incident = UnifiedResult(
            meta=QueryMeta(
                target="service-b",
                namespace="default",
                window_start=now - timedelta(minutes=30),
                window_end=now,
            ),
            metrics=metrics,
            logs=logs,
            traces=traces,
            correlations=Correlator().correlate(metrics, logs, traces),
            rca=RCAResult(
                performed=True,
                summary="service-b is returning errors",
                root_cause="service-b raised RuntimeError during request handling",
                confidence=0.82,
                supporting_evidence=["error logs and traces point at service-b"],
            ),
        )
        return incident

    def _assistant(self) -> tuple[RcaFollowUpAssistant, HermesRCAAgent]:
        agent = HermesRCAAgent(api_url="http://hermes.test/v1", model="hermes-agent")
        assistant = RcaFollowUpAssistant(hermes=agent, anthropic_api_key="anthropic-key")
        return assistant, agent

    @pytest.mark.asyncio
    async def test_hermes_success_does_not_call_anthropic(self) -> None:
        assistant, agent = self._assistant()
        agent._call_hermes = AsyncMock(
            return_value={"role": "assistant", "content": "Check service-b callers first."}
        )
        assistant._client.post = AsyncMock()

        response = await assistant.answer(
            incident=self._incident(),
            question="What should I check first?",
            history=[],
        )
        await assistant.close()

        assert response.provider == "hermes"
        assert response.answer == "Check service-b callers first."
        assert not response.fallback_used
        assert agent._call_hermes.await_count == 1
        assert agent._call_hermes.await_args.kwargs["include_tools"] is False
        assistant._client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_unrelated_followup_returns_scope_reminder_without_model_calls(self) -> None:
        assistant, agent = self._assistant()
        agent._call_hermes = AsyncMock()
        assistant._client.post = AsyncMock()

        response = await assistant.answer(
            incident=self._incident(),
            question="Write me a limerick about penguins.",
            history=[],
        )
        await assistant.close()

        assert response.provider is None
        assert response.fallback_used is False
        assert "only for questions about the current RCA and incident" in response.answer
        agent._call_hermes.assert_not_called()
        assistant._client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_hermes_empty_answer_falls_back_to_anthropic(self) -> None:
        assistant, agent = self._assistant()
        agent._call_hermes = AsyncMock(return_value={"role": "assistant", "content": "  "})
        anthropic_response = MagicMock()
        anthropic_response.status_code = 200
        anthropic_response.json.return_value = {
            "content": [{"text": "Blast radius is limited to service-b dependents."}]
        }
        assistant._client.post = AsyncMock(return_value=anthropic_response)

        response = await assistant.answer(
            incident=self._incident(),
            question="What's the blast radius?",
            history=[],
        )
        await assistant.close()

        assert response.provider == "anthropic"
        assert response.fallback_used
        assert "service-b dependents" in response.answer
        assert response.error == "Hermes returned an empty answer"

    @pytest.mark.asyncio
    async def test_hermes_failure_without_anthropic_key_returns_clear_error(self) -> None:
        agent = HermesRCAAgent(api_url="http://hermes.test/v1", model="hermes-agent")
        agent._call_hermes = AsyncMock(side_effect=RuntimeError("Hermes unavailable"))
        assistant = RcaFollowUpAssistant(hermes=agent, anthropic_api_key=None)

        response = await assistant.answer(
            incident=self._incident(),
            question="What should I check first?",
            history=[],
        )
        await assistant.close()

        assert response.provider is None
        assert response.fallback_used
        assert "Hermes unavailable" in (response.error or "")
        assert "Anthropic fallback is not configured" in (response.error or "")

    def test_blank_followup_question_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="question must not be blank"):
            FollowUpRequest(incident=self._incident(), question="   ")

    def test_followup_prompt_includes_rca_window_correlations_and_history(self) -> None:
        incident = self._incident()
        messages = _build_followup_messages(
            incident=incident,
            question="What evidence supports this?",
            history=[FollowUpMessage(role="user", content="Prior question")],
            prompt_mode="hermes_native",
        )
        prompt_text = "\n".join(str(message.get("content", "")) for message in messages)

        assert "service-b is returning errors" in prompt_text
        assert incident.meta.window_start.isoformat() in prompt_text
        assert "correlations" in prompt_text
        assert "Prior question" in prompt_text
        assert "What evidence supports this?" in prompt_text
        assert "registered read-only Hermes MCP observability tools" in prompt_text

    def test_followup_prompt_context_only_forbids_tool_claims(self) -> None:
        messages = _build_followup_messages(
            incident=self._incident(),
            question="What should I check first?",
            history=[],
            prompt_mode="context_only",
        )
        prompt_text = "\n".join(str(message.get("content", "")) for message in messages)

        assert "You cannot call tools in this path" in prompt_text
        assert "registered read-only Hermes MCP observability tools" not in prompt_text
        assert "If the developer asks something unrelated to this RCA" in prompt_text

    def test_followup_request_accepts_round_tripped_null_metric_samples(self) -> None:
        incident_payload = self._incident().model_dump(mode="json")
        incident_payload["metrics"]["series"][0]["samples"][0]["value"] = None

        request = FollowUpRequest(
            incident=incident_payload,
            question="What should I check first?",
            history=[],
        )

        assert request.incident.metrics.series[0].samples[0].value is None


def test_parse_sample_value_maps_nan_to_none() -> None:
    assert _parse_sample_value("NaN") is None
    assert _parse_sample_value("+Inf") is None
    assert _parse_sample_value("0.25") == 0.25


# ---------------------------------------------------------------------------
# Hermes RCA agent tests
# ---------------------------------------------------------------------------


class TestHermesRCAAgent:
    def _result_with_incident(self) -> UnifiedResult:
        return UnifiedResult(
            meta=QueryMeta(
                target="service-b",
                namespace="default",
                window_start=datetime.now(tz=timezone.utc),
                window_end=datetime.now(tz=timezone.utc),
            ),
            metrics=make_metrics(error_rate=0.2),
            logs=make_logs(error_count=3, total=6),
            traces=make_traces(count=1, has_errors=True, duration_ms=1500),
            correlations=Correlator().correlate(
                metrics=make_metrics(error_rate=0.2),
                logs=make_logs(error_count=3, total=6),
                traces=make_traces(count=1, has_errors=True, duration_ms=1500),
            ),
        )

    def _result_with_high_latency(self) -> UnifiedResult:
        now = datetime.now(tz=timezone.utc)
        metrics = MetricsSignal(
            series=[
                MetricSeries(
                    name="http_latency_p99",
                    labels={"job": "service-b", "handler": "/data"},
                    samples=[MetricSample(timestamp=now, value=2.51)],
                ),
                MetricSeries(
                    name="http_error_rate",
                    labels={"job": "service-b", "handler": "/data"},
                    samples=[MetricSample(timestamp=now, value=0.0)],
                ),
            ]
        )
        logs = make_logs(error_count=0, total=5)
        traces = make_traces(count=3, has_errors=False, duration_ms=2510)
        correlations = Correlator().correlate(metrics, logs, traces)
        return UnifiedResult(
            meta=QueryMeta(
                target="service-b",
                namespace="default",
                window_start=now - timedelta(minutes=30),
                window_end=now,
            ),
            metrics=metrics,
            logs=logs,
            traces=traces,
            correlations=correlations,
        )

    def _agent(
        self,
        *,
        prometheus=None,
        loki=None,
        jaeger=None,
        max_tool_rounds: int = 4,
        max_tool_calls: int = 8,
        tools_enabled: bool = True,
        investigation_mode: str = "tools_first",
    ) -> HermesRCAAgent:
        return HermesRCAAgent(
            api_url="http://hermes.test/v1",
            model="hermes-agent",
            prometheus=prometheus,
            loki=loki,
            jaeger=jaeger,
            tools_enabled=tools_enabled,
            investigation_mode=investigation_mode,
            max_tool_rounds=max_tool_rounds,
            max_tool_calls=max_tool_calls,
        )

    @pytest.mark.asyncio
    async def test_call_hermes_logs_tool_call_diagnostic(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        agent = self._agent()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-metrics",
                                "type": "function",
                                "function": {"name": "get_metrics", "arguments": "{}"},
                            }
                        ],
                    },
                }
            ]
        }
        agent._client.post = AsyncMock(return_value=response)

        with caplog.at_level("INFO"):
            message = await agent._call_hermes(
                [{"role": "user", "content": "investigate"}],
                include_tools=True,
            )
        await agent.close()

        assert message["tool_calls"][0]["function"]["name"] == "get_metrics"
        payload = agent._client.post.await_args.kwargs["json"]
        assert payload["tool_choice"] == "auto"
        assert "tools" in payload
        assert "Hermes response diagnostic" in caplog.text
        assert "'include_tools': True" in caplog.text
        assert "'tools_sent': True" in caplog.text
        assert "'tool_choice': 'auto'" in caplog.text
        assert "'tool_call_count': 1" in caplog.text
        assert "'finish_reason': 'tool_calls'" in caplog.text

    @pytest.mark.asyncio
    async def test_call_hermes_warns_when_tools_enabled_without_tool_calls(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        agent = self._agent()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": '{"summary": "answered without tools"}',
                    },
                }
            ]
        }
        agent._client.post = AsyncMock(return_value=response)

        with caplog.at_level("WARNING"):
            message = await agent._call_hermes(
                [{"role": "user", "content": "investigate"}],
                include_tools=True,
            )
        await agent.close()

        assert message["content"] == '{"summary": "answered without tools"}'
        assert "Hermes returned no tool calls" in caplog.text
        assert "'tool_call_count': 0" in caplog.text
        assert "'content_present': True" in caplog.text
        assert "'content_preview': '{\"summary\": \"answered without tools\"}'" in caplog.text

    @pytest.mark.asyncio
    async def test_call_hermes_truncates_and_redacts_content_preview(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        api_key = "secret-hermes-key"
        agent = HermesRCAAgent(
            api_url="http://hermes.test/v1",
            api_key=api_key,
            model="hermes-agent",
        )
        sensitive_content = (
            f"authorization: Bearer {api_key} "
            f"api_key={api_key} "
            + ("x" * 400)
        )
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": sensitive_content,
                    },
                }
            ]
        }
        agent._client.post = AsyncMock(return_value=response)

        with caplog.at_level("WARNING"):
            await agent._call_hermes(
                [{"role": "user", "content": "investigate"}],
                include_tools=True,
            )
        await agent.close()

        assert api_key not in caplog.text
        assert "authorization: Bearer [REDACTED]" in caplog.text
        assert "'content_preview':" in caplog.text
        preview = caplog.text.split("'content_preview': '", 1)[1].split("'", 1)[0]
        assert len(preview) == 300

    def test_builds_bounded_incident_dossier(self) -> None:
        result = self._result_with_incident()
        result.correlations.append(
            CorrelationEvent(
                kind="traffic_without_traces",
                description="Traffic exists but traces are absent",
                severity="warn",
                related_metric="http_requests_per_second",
            )
        )
        dossier = _build_incident_dossier(result)

        assert set(dossier) == {
            "target",
            "namespace",
            "window_start",
            "window_end",
            "signal_errors",
            "signal_counts",
            "suspicious_absence_events",
            "correlations",
            "metrics",
            "logs",
            "traces",
        }
        assert dossier["target"] == "service-b"
        assert dossier["namespace"] == "default"
        assert dossier["metrics"][0]["name"] == "http_error_rate"
        assert dossier["logs"][0]["message"] == "log line 0"
        assert dossier["traces"][0]["trace_id"].startswith("trace")
        assert dossier["correlations"][0]["kind"]
        assert dossier["signal_errors"] == {"metrics": None, "logs": None, "traces": None}
        assert dossier["signal_counts"]["metric_series"] == len(result.metrics.series)
        assert dossier["suspicious_absence_events"][0]["kind"] == "traffic_without_traces"

    @pytest.mark.asyncio
    async def test_parses_hermes_json_result(self) -> None:
        agent = self._agent()
        raw = """
        {
          "summary": "service-b is failing",
          "root_cause": "Injected errors in service-b are producing 500s.",
          "confidence": 0.82,
          "supporting_evidence": ["error logs", "error traces"],
          "log_evidence": [
            {
              "timestamp": "2026-05-02T19:12:34Z",
              "severity": "error",
              "message": "service-b raised RuntimeError",
              "relevance": "Confirms the failing service emitted an error log",
              "labels": {"service": "service-b"}
            }
          ],
          "recommended_actions": [
            {"priority": 1, "action": "Reset service-b", "rationale": "Stops injected failures"}
          ],
          "github_search_terms": ["FAILURE_RATE"]
        }
        """

        rca = agent._parse_response(raw)
        await agent.close()

        assert rca.summary == "service-b is failing"
        assert rca.confidence == 0.82
        assert rca.log_evidence[0].message == "service-b raised RuntimeError"
        assert rca.log_evidence[0].labels["service"] == "service-b"
        assert rca.recommended_actions[0].priority == 1
        assert "FAILURE_RATE" in rca.github_search_terms

    @pytest.mark.asyncio
    async def test_hermes_prompt_requires_structured_log_evidence(self) -> None:
        agent = self._agent(investigation_mode="dossier")
        prompt = agent._build_prompt(self._result_with_incident())
        await agent.close()

        assert "human-readable evidence" in prompt
        assert "not raw tool or metric dumps" in prompt
        assert "get_metrics:" in prompt
        assert "latest_value=" in prompt
        assert "sample_count=" in prompt
        assert "plain-English evidence claim" in prompt
        assert "plain language" in prompt
        assert "Do not invent" in prompt
        assert "exact provided log excerpt" in prompt
        assert "Telemetry gaps are investigative evidence" in prompt
        assert "observability blind spot" in prompt

    @pytest.mark.asyncio
    async def test_simple_rca_prompt_includes_telemetry_gap_context(self) -> None:
        now = datetime.now(tz=timezone.utc)
        result = UnifiedResult(
            meta=QueryMeta(
                target="service-b",
                namespace="default",
                window_start=now - timedelta(minutes=30),
                window_end=now,
            ),
            metrics=make_traffic_metrics(),
            logs=LogsSignal(),
            traces=TracesSignal(),
            correlations=[
                CorrelationEvent(
                    kind="traffic_without_traces",
                    description="Traffic exists but traces are absent",
                    severity="warn",
                )
            ],
        )
        analyzer = RCAAnalyzer(api_key="test-key")

        prompt = analyzer._build_prompt(result)
        await analyzer.close()

        assert "## Signal health" in prompt
        assert "## Telemetry gaps" in prompt
        assert "traffic_without_traces" in prompt
        assert "observability blind spot" in prompt

    def test_tool_schemas_include_observability_tools(self) -> None:
        names = {
            schema["function"]["name"]
            for schema in _tool_schemas()
        }

        assert names == {
            "get_aggregate",
            "get_metrics",
            "get_logs",
            "get_traces",
            "get_correlations",
        }

    @pytest.mark.asyncio
    async def test_tools_first_prompt_omits_incident_dossier_signals(self) -> None:
        agent = self._agent()
        result = self._result_with_incident()
        prompt = agent._build_prompt(result)
        await agent.close()

        assert "Scoped investigation target:" in prompt
        assert "- target: service-b" in prompt
        assert f"Set start exactly to {result.meta.window_start.isoformat()}" in prompt
        assert f"end exactly to {result.meta.window_end.isoformat()}" in prompt
        assert "pass target, namespace, start, and end" in prompt
        assert "Do not use lookback_minutes for this scoped RCA" in prompt
        assert "must first call the aggregator overview tool k8s_obs:get_aggregate" in prompt
        assert "After reviewing that aggregate result" in prompt
        assert "only if you still need deeper evidence" in prompt
        assert "k8s_obs:get_metrics" in prompt
        assert "k8s_obs:get_logs" in prompt
        assert "k8s_obs:get_traces" in prompt
        assert "k8s_obs:get_correlations" in prompt
        assert "native Hermes tools" in prompt
        assert "chat response's content as JSON" in prompt
        assert "human-readable evidence" in prompt
        assert "not raw tool or metric dumps" in prompt
        assert "get_aggregate:" in prompt
        assert "get_metrics:" in prompt
        assert "latest_value=" in prompt
        assert "sample_count=" in prompt
        assert "plain-English evidence claim" in prompt
        assert "Telemetry gaps are investigative evidence" in prompt
        assert "observability blind spot" in prompt
        assert "Incident dossier:" not in prompt
        assert "log line 0" not in prompt
        assert "trace0000" not in prompt
        assert '"correlations"' not in prompt.lower()

    @pytest.mark.asyncio
    async def test_tools_first_analyze_starts_without_incident_dossier(self) -> None:
        agent = self._agent()
        agent._call_hermes = AsyncMock(side_effect=RuntimeError("stop after first call"))

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        assert not rca.performed
        first_messages = agent._call_hermes.await_args_list[0].args[0]
        first_prompt = first_messages[0]["content"]
        assert "Scoped investigation target:" in first_prompt
        assert "Incident dossier:" not in first_prompt
        assert "log line 0" not in first_prompt
        assert agent._call_hermes.await_args_list[0].kwargs["include_tools"] is False

    @pytest.mark.asyncio
    async def test_tools_first_accepts_native_mcp_chat_content(self) -> None:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=make_metrics(error_rate=0.2))
        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs(error_count=2, total=4))
        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(
            return_value=make_traces(count=1, has_errors=True, duration_ms=1500)
        )
        agent = self._agent(prometheus=prometheus, loki=loki, jaeger=jaeger)
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "native MCP RCA",
                      "root_cause": "Hermes used registered MCP tools internally",
                      "confidence": 0.8,
                      "supporting_evidence": [
                        "http_error_rate for service-b stayed elevated during the incident window",
                        "error logs and failing traces both point at service-b request handling"
                      ],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                }
            ]
        )

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        assert rca.performed
        assert rca.summary == "native MCP RCA"
        assert agent._call_hermes.await_count == 1
        assert agent._call_hermes.await_args_list[0].kwargs["include_tools"] is False
        prometheus.query_metrics.assert_not_awaited()
        loki.query_logs.assert_not_awaited()
        jaeger.query_traces.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tools_first_retries_no_fault_rca_that_misses_latency(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        result = self._result_with_high_latency()
        agent = self._agent()
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "summary": (
                                "Service-b shows no local fault in the incident window: "
                                "traffic stayed successful, logs were clean, traces were "
                                "fast, and no cross-signal correlation appeared."
                            ),
                            "root_cause": (
                                "The available read-only observability evidence does not "
                                "support a service-b application failure as the incident root "
                                "cause. The strongest conclusion from this dataset is that "
                                "the user-visible issue, if any, originated outside service-b "
                                "or in a path not represented by these signals."
                            ),
                            "confidence": 0.79,
                            "supporting_evidence": [
                                (
                                    "service-b's /data handler showed only 2xx traffic in "
                                    "the window, with zero observed 5xx requests"
                                ),
                                (
                                    "No error or warning log lines were emitted by service-b "
                                    "in the incident window"
                                ),
                                (
                                    "No error traces were returned for service-b, and the "
                                    "observed trace latency stayed low at about 5.2 ms p99"
                                ),
                                (
                                    "The correlation query returned no linked incidents "
                                    "across metrics, logs, and traces"
                                ),
                            ],
                            "recommended_actions": [],
                            "github_search_terms": [],
                        }
                    ),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "summary": (
                                "service-b is the likely source of the high latency: "
                                "requests remained successful but p99 latency rose to "
                                "about 2.5 seconds."
                            ),
                            "root_cause": (
                                "The incident is a latency-only degradation in service-b "
                                "rather than an error-rate incident. The scoped traces and "
                                "latency metric show slow successful requests on service-b "
                                "/data, while logs and 5xx metrics stay clean."
                            ),
                            "confidence": 0.86,
                            "supporting_evidence": [
                                (
                                    "Trace p99 latency for service-b was about 2510 ms, "
                                    "above the 1000 ms incident threshold"
                                ),
                                (
                                    "http_latency_p99 for service-b /data peaked at 2.51 s "
                                    "while http_error_rate stayed at 0"
                                ),
                            ],
                            "recommended_actions": [],
                            "github_search_terms": [],
                        }
                    ),
                },
            ]
        )

        with caplog.at_level("INFO"):
            rca = await agent.analyze(result)
        await agent.close()

        assert rca.performed
        assert rca.summary.startswith("service-b is the likely source of the high latency")
        assert agent._call_hermes.await_count == 2
        retry_prompt = agent._call_hermes.await_args_list[1].args[0][-1]["content"]
        assert "Weaknesses:" in retry_prompt
        assert "RCA denies a local service issue" in retry_prompt
        assert "RCA says traces were fast or low-latency" in retry_prompt
        assert "RCA says there were no correlations" in retry_prompt
        assert "trace p99 latency 2510 ms exceeds 1000 ms" in retry_prompt
        assert "Hermes tools-first retrying weak native RCA" in caplog.text

    @pytest.mark.asyncio
    async def test_tools_first_retries_once_when_native_content_is_weak(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        result = self._result_with_incident()
        result.traces = TracesSignal(
            traces=[
                Trace(
                    trace_id="trace-related",
                    root_service="edge-gateway",
                    spans=[
                        Span(
                            trace_id="trace-related",
                            span_id="span-target",
                            operation_name="GET /api",
                            service_name="service-b",
                            start_time=now,
                            duration_us=900_000,
                            is_error=True,
                        ),
                        Span(
                            trace_id="trace-related",
                            span_id="span-checkout",
                            operation_name="POST /checkout",
                            service_name="checkout",
                            start_time=now,
                            duration_us=1_200_000,
                            is_error=True,
                        ),
                        Span(
                            trace_id="trace-related",
                            span_id="span-payments",
                            operation_name="POST /pay",
                            service_name="payments",
                            start_time=now,
                            duration_us=1_100_000,
                            is_error=False,
                        ),
                    ],
                )
            ]
        )
        result.traces.compute_stats()
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=make_metrics(error_rate=0.2))
        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs(error_count=2, total=4))
        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(return_value=result.traces)
        agent = self._agent(prometheus=prometheus, loki=loki, jaeger=jaeger)
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "weak RCA",
                      "root_cause": "service-b might be failing",
                      "confidence": 0.62,
                      "supporting_evidence": ["get_metrics: latest_value=0.2"],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "retried native MCP RCA",
                      "root_cause": "service-b request handling failed; checkout is downstream.",
                      "confidence": 0.84,
                      "supporting_evidence": [
                        "http_error_rate for service-b stayed elevated during the incident window",
                        "service-b error spans line up with checkout failures downstream"
                      ],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
            ]
        )

        with caplog.at_level("INFO"):
            rca = await agent.analyze(result)
        await agent.close()

        assert rca.performed
        assert rca.summary == "retried native MCP RCA"
        assert agent._call_hermes.await_count == 2
        second_messages = agent._call_hermes.await_args_list[1].args[0]
        retry_prompt = second_messages[-1]["content"]
        assert "Weaknesses:" in retry_prompt
        assert "confidence 0.620 below 0.700" in retry_prompt
        assert "supporting_evidence still looks like raw tool dumps" in retry_prompt
        assert "candidate related services already visible in the incident dossier" in retry_prompt
        assert "edge-gateway, checkout" in retry_prompt
        assert "payments" not in retry_prompt
        assert "namespace: default" in retry_prompt
        assert f"Set start exactly to {result.meta.window_start.isoformat()}" in retry_prompt
        assert f"end exactly to {result.meta.window_end.isoformat()}" in retry_prompt
        assert "pass target, namespace, start, and end" in retry_prompt
        assert "Do not use lookback_minutes for this scoped RCA" in retry_prompt
        assert (
            "use that service as target but keep the same namespace, start, and end"
            in retry_prompt
        )
        assert "Hermes tools-first retrying weak native RCA" in caplog.text
        assert "candidate_related_services=['edge-gateway', 'checkout']" in caplog.text
        prometheus.query_metrics.assert_not_awaited()
        loki.query_logs.assert_not_awaited()
        jaeger.query_traces.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tools_first_forces_aggregate_when_native_content_is_invalid(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=make_metrics(error_rate=0.2))
        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs(error_count=2, total=4))
        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(
            return_value=make_traces(count=1, has_errors=True, duration_ms=1500)
        )
        agent = self._agent(prometheus=prometheus, loki=loki, jaeger=jaeger)
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": "I could not inspect tools from this chat session.",
                },
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "forced tools RCA",
                      "root_cause": "required tool results were provided before RCA",
                      "confidence": 0.78,
                      "supporting_evidence": ["metrics", "logs", "traces"],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
            ]
        )

        with caplog.at_level("INFO"):
            rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        assert rca.performed
        assert rca.summary == "forced tools RCA"
        prometheus.query_metrics.assert_not_awaited()
        loki.query_logs.assert_not_awaited()
        jaeger.query_traces.assert_not_awaited()
        final_messages = agent._call_hermes.await_args_list[1].args[0]
        evidence_messages = [
            msg["content"]
            for msg in final_messages
            if msg.get("role") == "user"
            and "The aggregator executed the required aggregate observability overview" in msg.get("content", "")
        ]
        assert len(evidence_messages) == 1
        assert '"tool": "get_aggregate"' in evidence_messages[0]
        assert "http_error_rate" in evidence_messages[0]
        assert "log line 0" in evidence_messages[0]
        assert "trace0000" in evidence_messages[0]
        assert agent._call_hermes.await_args_list[0].kwargs["include_tools"] is False
        assert agent._call_hermes.await_args_list[1].kwargs["include_tools"] is False
        assert "Hermes native MCP chat content was not usable" in caplog.text
        assert "Hermes tools-first forced tool call name=get_aggregate" in caplog.text

    @pytest.mark.asyncio
    async def test_tools_first_forces_aggregate_when_native_content_is_missing(self) -> None:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=make_metrics(error_rate=0.2))
        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs(error_count=2, total=4))
        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(
            return_value=make_traces(count=1, has_errors=True, duration_ms=1500)
        )
        agent = self._agent(prometheus=prometheus, loki=loki, jaeger=jaeger)
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                },
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "forced after missing content",
                      "root_cause": "required evidence was injected after empty native content",
                      "confidence": 0.82,
                      "supporting_evidence": ["metrics", "logs", "traces"],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
            ]
        )

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        assert rca.performed
        assert rca.summary == "forced after missing content"
        assert agent._call_hermes.await_count == 2
        assert agent._call_hermes.await_args_list[0].kwargs["include_tools"] is False
        assert agent._call_hermes.await_args_list[1].kwargs["include_tools"] is False
        prometheus.query_metrics.assert_not_awaited()
        loki.query_logs.assert_not_awaited()
        jaeger.query_traces.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tools_first_forces_aggregate_not_optional_drilldowns(self) -> None:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=make_metrics(error_rate=0.2))
        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs(error_count=2, total=4))
        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(
            return_value=make_traces(count=1, has_errors=True, duration_ms=1500)
        )
        agent = self._agent(prometheus=prometheus, loki=loki, jaeger=jaeger)
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-metrics",
                            "type": "function",
                            "function": {"name": "get_metrics", "arguments": "{}"},
                        },
                        {
                            "id": "call-logs",
                            "type": "function",
                            "function": {"name": "get_logs", "arguments": "{}"},
                        },
                        {
                            "id": "call-traces",
                            "type": "function",
                            "function": {"name": "get_traces", "arguments": "{}"},
                        },
                        {
                            "id": "call-correlations",
                            "type": "function",
                            "function": {"name": "get_correlations", "arguments": "{}"},
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "correlations helped",
                      "root_cause": "optional correlations reinforced the required evidence",
                      "confidence": 0.86,
                      "supporting_evidence": ["metrics", "logs", "traces", "correlations"],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
            ]
        )

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        assert rca.performed
        assert rca.summary == "correlations helped"
        prometheus.query_metrics.assert_not_awaited()
        loki.query_logs.assert_not_awaited()
        jaeger.query_traces.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_aggregate_uses_existing_incident_result_without_requerying(self) -> None:
        result = self._result_with_incident()
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=make_metrics(error_rate=0.9))
        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs(error_count=4, total=4))
        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(
            return_value=make_traces(count=2, has_errors=True, duration_ms=2000)
        )
        agent = self._agent(prometheus=prometheus, loki=loki, jaeger=jaeger)

        tool_result = await agent._run_tool("get_aggregate", {}, result)
        await agent.close()

        assert tool_result["ok"] is True
        assert tool_result["tool"] == "get_aggregate"
        assert tool_result["target"] == "service-b"
        assert tool_result["namespace"] == "default"
        assert tool_result["counts"]["metric_series"] == len(result.metrics.series)
        assert tool_result["counts"]["error_log_lines"] == result.logs.error_count
        assert tool_result["counts"]["error_traces"] == result.traces.error_trace_count
        assert tool_result["aggregate"]["metrics"][0]["name"] == "http_error_rate"
        assert tool_result["aggregate"]["logs"][0]["message"] == "log line 0"
        assert tool_result["aggregate"]["traces"][0]["trace_id"].startswith("trace")
        prometheus.query_metrics.assert_not_awaited()
        loki.query_logs.assert_not_awaited()
        jaeger.query_traces.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tools_first_limit_forces_final_answer(self) -> None:
        agent = self._agent(max_tool_rounds=0)
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-metrics",
                            "type": "function",
                            "function": {"name": "get_metrics", "arguments": "{}"},
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "best effort after limit",
                      "root_cause": "tool limit prevented full inspection",
                      "confidence": 0.45,
                      "supporting_evidence": ["limited evidence"],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
            ]
        )

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        assert rca.performed
        assert rca.summary == "best effort after limit"
        assert agent._call_hermes.await_args_list[1].kwargs["include_tools"] is False

    @pytest.mark.asyncio
    async def test_should_run_for_latency_metric_anomaly(self) -> None:
        now = datetime.now(tz=timezone.utc)
        result = UnifiedResult(
            meta=QueryMeta(
                target="service-b",
                namespace="default",
                window_start=now - timedelta(minutes=30),
                window_end=now,
            ),
            metrics=MetricsSignal(
                series=[
                    MetricSeries(
                        name="http_latency_p99",
                        samples=[MetricSample(timestamp=now, value=1.25)],
                    )
                ]
            ),
            logs=make_logs(error_count=0, total=10),
            traces=make_traces(count=1, has_errors=False, duration_ms=100),
            correlations=[],
        )
        agent = self._agent()

        assert agent._should_run(result)
        await agent.close()

    @pytest.mark.asyncio
    async def test_should_run_for_suspicious_absence_event(self) -> None:
        now = datetime.now(tz=timezone.utc)
        result = UnifiedResult(
            meta=QueryMeta(
                target="service-b",
                namespace="default",
                window_start=now - timedelta(minutes=30),
                window_end=now,
            ),
            metrics=make_traffic_metrics(),
            logs=LogsSignal(),
            traces=TracesSignal(),
            correlations=[
                CorrelationEvent(
                    kind="traffic_without_traces",
                    description="Traffic exists but traces are absent",
                    severity="warn",
                )
            ],
        )
        hermes = self._agent()
        simple = RCAAnalyzer(api_key="test-key")

        assert hermes._should_run(result)
        assert simple._should_run(result)
        await hermes.close()
        await simple.close()

    @pytest.mark.asyncio
    async def test_should_not_run_for_normal_latency_metric(self) -> None:
        now = datetime.now(tz=timezone.utc)
        result = UnifiedResult(
            meta=QueryMeta(
                target="service-b",
                namespace="default",
                window_start=now - timedelta(minutes=30),
                window_end=now,
            ),
            metrics=MetricsSignal(
                series=[
                    MetricSeries(
                        name="http_latency_p99",
                        samples=[MetricSample(timestamp=now, value=1.0)],
                    )
                ]
            ),
            logs=make_logs(error_count=0, total=10),
            traces=make_traces(count=1, has_errors=False, duration_ms=100),
            correlations=[],
        )
        agent = self._agent()

        assert not agent._should_run(result)
        await agent.close()

    def test_log_tool_summary_prefers_newest_relevant_logs(self) -> None:
        start = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        lines = [
            LogLine(timestamp=start, message="old error", severity=Severity.ERROR),
            LogLine(timestamp=start + timedelta(seconds=1), message="middle error", severity=Severity.ERROR),
            LogLine(timestamp=start + timedelta(seconds=2), message="new info", severity=Severity.INFO),
            LogLine(timestamp=start + timedelta(seconds=3), message="newest error", severity=Severity.ERROR),
        ]

        summarized = _summarize_logs(lines, limit=2)

        assert [line["message"] for line in summarized] == ["middle error", "newest error"]

    def test_log_tool_summary_uses_newest_logs_when_no_errors_or_warnings(self) -> None:
        start = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        lines = [
            LogLine(timestamp=start + timedelta(seconds=i), message=f"info {i}", severity=Severity.INFO)
            for i in range(4)
        ]

        summarized = _summarize_logs(lines, limit=2)

        assert [line["message"] for line in summarized] == ["info 2", "info 3"]

    @pytest.mark.asyncio
    async def test_tool_calls_default_to_incident_window(self) -> None:
        start = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc)
        result = self._result_with_incident()
        result.meta.window_start = start
        result.meta.window_end = end
        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs(error_count=2, total=4))
        agent = self._agent(loki=loki)

        await agent._run_tool("get_logs", {}, result)
        await agent.close()

        _, _, actual_start, actual_end = loki.query_logs.await_args.args
        assert actual_start == start
        assert actual_end == end

    @pytest.mark.asyncio
    async def test_tool_calls_anchor_explicit_lookback_to_incident_end(self) -> None:
        window_start = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        window_end = datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc)
        result = self._result_with_incident()
        result.meta.window_start = window_start
        result.meta.window_end = window_end
        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs(error_count=2, total=4))
        agent = self._agent(loki=loki)

        await agent._run_tool("get_logs", {"lookback_minutes": 15}, result)
        await agent.close()

        _, _, actual_start, actual_end = loki.query_logs.await_args.args
        assert actual_start == window_end - timedelta(minutes=15)
        assert actual_end == window_end

    @pytest.mark.asyncio
    async def test_tool_calls_do_not_start_before_incident_window(self) -> None:
        start = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc)
        result = self._result_with_incident()
        result.meta.window_start = start
        result.meta.window_end = end
        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs(error_count=2, total=4))
        agent = self._agent(loki=loki)

        await agent._run_tool("get_logs", {"lookback_minutes": 9999}, result)
        await agent.close()

        _, _, actual_start, actual_end = loki.query_logs.await_args.args
        assert actual_start == start
        assert actual_end == end

    @pytest.mark.asyncio
    async def test_final_json_without_tool_calls_still_works(self) -> None:
        agent = self._agent(investigation_mode="dossier")
        agent._call_hermes = AsyncMock(
            return_value={
                "role": "assistant",
                "content": """
                {
                  "summary": "service-b failed",
                  "root_cause": "service-b emitted errors",
                  "confidence": 0.75,
                  "supporting_evidence": ["error logs"],
                  "recommended_actions": [],
                  "github_search_terms": []
                }
                """,
            }
        )

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        assert rca.performed
        assert rca.summary == "service-b failed"
        assert rca.log_evidence == []
        assert "log_evidence" not in rca.model_fields_set
        agent._call_hermes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_executes_log_tool_call_before_final_rca(self) -> None:
        loki = MagicMock()
        loki.query_logs = AsyncMock(return_value=make_logs(error_count=2, total=4))
        agent = self._agent(loki=loki, investigation_mode="dossier")
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "get_logs",
                                "arguments": '{"severity":"error"}',
                            },
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "logs confirm errors",
                      "root_cause": "service-b produced error logs",
                      "confidence": 0.8,
                      "supporting_evidence": ["get_logs returned errors"],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
            ]
        )

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        assert rca.performed
        assert rca.summary == "logs confirm errors"
        loki.query_logs.assert_awaited_once()
        assert agent._call_hermes.await_count == 2

    def test_parse_response_preserves_plain_supporting_evidence(self) -> None:
        agent = self._agent()
        raw = """
        {
          "summary": "service-b failed",
          "root_cause": "service-b emitted errors",
          "confidence": 0.75,
          "supporting_evidence": [
            "service-b raised RuntimeError",
            "plain supporting text"
          ],
          "log_evidence": [],
          "recommended_actions": [],
          "github_search_terms": []
        }
        """

        rca = agent._parse_response(raw)

        assert rca.supporting_evidence[0] == "service-b raised RuntimeError"
        assert rca.supporting_evidence[1] == "plain supporting text"
        assert rca.log_evidence == []
        assert "log_evidence" in rca.model_fields_set

    def test_rich_formatter_splits_human_evidence_detail(self) -> None:
        result = self._result_with_incident()
        result.rca = RCAResult(
            performed=True,
            summary="Test summary",
            root_cause="Test root cause",
            confidence=0.9,
            supporting_evidence=[
                "http_error_rate for /crash peaked at 0.1404 req/s — 100% of requests failed",
                "CPU and memory are within normal operating ranges",
            ],
        )
        console = Console(record=True, force_terminal=True, width=120)
        formatter = RichFormatter(con=console)

        formatter.render(result)
        output = console.export_text()

        assert "Supporting evidence:" in output
        assert "1." in output
        assert "2." in output
        assert "http_error_rate for /crash peaked at 0.1404 req/s" in output
        assert "100% of requests failed" in output
        assert "CPU and memory are within normal operating ranges" in output

    @pytest.mark.asyncio
    async def test_executes_multiple_tool_calls_in_one_round(self) -> None:
        prometheus = MagicMock()
        prometheus.query_metrics = AsyncMock(return_value=make_metrics(error_rate=0.2))
        jaeger = MagicMock()
        jaeger.query_traces = AsyncMock(
            return_value=make_traces(count=1, has_errors=True, duration_ms=1500)
        )
        agent = self._agent(prometheus=prometheus, jaeger=jaeger, investigation_mode="dossier")
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-metrics",
                            "type": "function",
                            "function": {"name": "get_metrics", "arguments": "{}"},
                        },
                        {
                            "id": "call-traces",
                            "type": "function",
                            "function": {
                                "name": "get_traces",
                                "arguments": '{"errors_only":true}',
                            },
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "metrics and traces confirm failure",
                      "root_cause": "service-b errors are visible in both signals",
                      "confidence": 0.81,
                      "supporting_evidence": ["metrics", "traces"],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
            ]
        )

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        assert rca.performed
        prometheus.query_metrics.assert_awaited_once()
        jaeger.query_traces.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_structured_error_context(self) -> None:
        agent = self._agent(investigation_mode="dossier")
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-bad",
                            "type": "function",
                            "function": {"name": "restart_service", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "unknown tool was ignored",
                      "root_cause": "available evidence still points to service-b",
                      "confidence": 0.6,
                      "supporting_evidence": ["tool error was returned"],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
            ]
        )

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        messages = agent._call_hermes.await_args_list[1].args[0]
        tool_message = [msg for msg in messages if msg.get("role") == "tool"][0]
        assert "Unknown tool" in tool_message["content"]
        assert rca.performed

    @pytest.mark.asyncio
    async def test_invalid_tool_args_return_structured_error_context(self) -> None:
        agent = self._agent(investigation_mode="dossier")
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-invalid",
                            "type": "function",
                            "function": {"name": "get_logs", "arguments": "{not-json"},
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "invalid args were handled",
                      "root_cause": "available evidence still points to service-b",
                      "confidence": 0.6,
                      "supporting_evidence": ["tool argument error was returned"],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
            ]
        )

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        messages = agent._call_hermes.await_args_list[1].args[0]
        tool_message = [msg for msg in messages if msg.get("role") == "tool"][0]
        assert "Invalid tool arguments JSON" in tool_message["content"]
        assert rca.performed

    @pytest.mark.asyncio
    async def test_max_rounds_forces_final_answer(self) -> None:
        agent = self._agent(max_tool_rounds=0)
        agent._call_hermes = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "get_logs", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": """
                    {
                      "summary": "final after limit",
                      "root_cause": "used initial dossier only",
                      "confidence": 0.55,
                      "supporting_evidence": ["initial dossier"],
                      "recommended_actions": [],
                      "github_search_terms": []
                    }
                    """,
                },
            ]
        )

        rca = await agent.analyze(self._result_with_incident())
        await agent.close()

        assert rca.performed
        assert rca.summary == "final after limit"
        assert agent._call_hermes.await_args_list[1].kwargs["include_tools"] is False


# ---------------------------------------------------------------------------
# TimeWindow validation tests
# ---------------------------------------------------------------------------


class TestTimeWindow:
    def test_from_lookback_produces_valid_window(self) -> None:
        window = TimeWindow.from_lookback(30)
        assert window.start < window.end
        assert abs(window.duration_seconds - 1800) < 5  # allow 5 s clock drift

    def test_validates_start_before_end(self) -> None:
        now = datetime.now(tz=timezone.utc)
        with pytest.raises(ValueError, match="start must be before end"):
            TimeWindow(start=now, end=now)

    def test_resolve_window_uses_explicit_range(self) -> None:
        req = QueryRequest(
            target="svc",
            start="2024-01-01T10:00:00",
            end="2024-01-01T11:00:00",
        )
        window = req.resolve_window()
        assert window.duration_seconds == 3600.0

    def test_resolve_window_falls_back_to_default_lookback(self) -> None:
        req = QueryRequest(target="svc")
        window = req.resolve_window(default_lookback_minutes=15)
        assert abs(window.duration_seconds - 900) < 5

    def test_lookback_and_range_mutually_exclusive(self) -> None:
        with pytest.raises(ValueError):
            QueryRequest(
                target="svc",
                lookback_minutes=30,
                start="2024-01-01T10:00:00",
                end="2024-01-01T11:00:00",
            )


# ---------------------------------------------------------------------------
# Loki helper tests — _group_multiline and _severity_from_message
# ---------------------------------------------------------------------------


class TestLokiHelpers:
    def _log(self, message: str, severity: Severity = Severity.ERROR) -> LogLine:
        return LogLine(
            timestamp=datetime.now(tz=timezone.utc),
            message=message,
            severity=severity,
        )

    def test_severity_from_message_extracts_level(self) -> None:
        sev, body = _severity_from_message("ERROR myservice connection refused")
        assert sev == Severity.ERROR
        assert "connection refused" in body

    def test_severity_from_message_unknown_passthrough(self) -> None:
        sev, body = _severity_from_message("something without a level prefix")
        assert sev == Severity.UNKNOWN
        assert body == "something without a level prefix"

    def test_group_multiline_merges_traceback_into_error(self) -> None:
        from datetime import timedelta
        now = datetime.now(tz=timezone.utc)
        lines = [
            LogLine(timestamp=now, message="ValueError: bad input", severity=Severity.ERROR),
            LogLine(timestamp=now + timedelta(milliseconds=50), message='  File "app.py", line 10, in handler', severity=Severity.UNKNOWN),
            LogLine(timestamp=now + timedelta(milliseconds=100), message="  x = int(val)", severity=Severity.UNKNOWN),
            LogLine(timestamp=now + timedelta(seconds=5), message="INFO: all good", severity=Severity.INFO),
        ]
        grouped = _group_multiline(lines)
        # First entry should have merged the three first lines; INFO stays separate
        assert len(grouped) == 2
        assert "File" in grouped[0].message
        assert grouped[1].severity == Severity.INFO

    def test_group_multiline_does_not_merge_distant_lines(self) -> None:
        from datetime import timedelta
        now = datetime.now(tz=timezone.utc)
        lines = [
            LogLine(timestamp=now, message="ValueError: bad input", severity=Severity.ERROR),
            # More than 1 second later — should NOT be merged
            LogLine(timestamp=now + timedelta(seconds=2), message='  File "app.py", line 10', severity=Severity.UNKNOWN),
        ]
        grouped = _group_multiline(lines)
        assert len(grouped) == 2

    def test_group_multiline_empty_input(self) -> None:
        assert _group_multiline([]) == []
