"""
Hermes-backed RCA agent.

This adapter treats Hermes as an external OpenAI-compatible agent runtime.
The aggregator still owns signal collection and normalization; Hermes receives
an incident dossier and returns the same RCAResult shape as the one-shot RCA path.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from aggregator.clients.jaeger import JaegerClient
from aggregator.clients.loki import LokiClient
from aggregator.clients.prometheus import PrometheusClient
from aggregator.core.correlator import Correlator
from aggregator.core.rca_gate import should_run_rca
from aggregator.core.suspicious_absence import (
    SuspiciousAbsenceDetector,
    is_suspicious_absence_event,
)
from aggregator.models.rca import LogEvidence, RCAResult, RecommendedAction
from aggregator.models.result import CorrelationEvent, QueryMeta, UnifiedResult
from aggregator.models.signals import (
    LogLine,
    LogsSignal,
    MetricsSignal,
    Severity,
    Trace,
    TracesSignal,
)

logger = logging.getLogger(__name__)

MAX_CORRELATIONS = 12
MAX_METRIC_SERIES = 10
MAX_LOG_LINES = 25
MAX_TRACES = 8
MAX_SPANS_PER_TRACE = 8
TOOL_LOG_LIMIT = 80
TOOL_TRACE_LIMIT = 12
HERMES_CONTENT_PREVIEW_CHARS = 300
LATENCY_ANOMALY_THRESHOLD_S = 1.0
LATENCY_ANOMALY_THRESHOLD_MS = 1_000
ERROR_RATE_ANOMALY_THRESHOLD = 0.01
RCA_SCOPE_CLUSTER_SECONDS = 2 * 60
RCA_SCOPE_SINGLE_EVENT_PAD_SECONDS = 15
MIN_TOOLS_FIRST_CONFIDENCE = 0.7
MIN_TOOLS_FIRST_CONCRETE_EVIDENCE = 2
_LATENCY_KEYWORDS = ("latency", "duration", "p99", "p95")
_NO_LOCAL_FAULT_PHRASES = (
    "no local fault",
    "no local failure",
    "no application failure",
    "no internal fault",
    "does not support a service",
    "does not support service",
    "does not support an application",
    "does not support local",
    "doesn't support a service",
    "doesn't support service",
    "doesn't support an application",
    "no service fault",
    "no service failure",
    "originated outside",
    "outside service",
)
_LATENCY_ANOMALY_PHRASES = (
    "high latency",
    "latency spike",
    "latency anomaly",
    "elevated latency",
    "slow request",
    "slow trace",
    "slow span",
    "slowdown",
    "slowness",
    "timeout",
)
_LOW_LATENCY_CLAIM_PHRASES = (
    "no high latency",
    "no latency spike",
    "no latency anomaly",
    "latency stayed low",
    "latency remains low",
    "latency was low",
    "latency is low",
    "traces were fast",
    "traces are fast",
    "traces stayed fast",
    "trace latency stayed low",
    "trace latency was low",
    "low trace latency",
    "observed trace latency stayed low",
)
_NO_CORRELATION_PHRASES = (
    "no cross-signal correlation",
    "no cross signal correlation",
    "no metric/log/trace correlation",
    "no linked incidents",
    "correlation query returned no",
)
_RAW_EVIDENCE_MARKERS = (
    "get_aggregate:",
    "get_metrics:",
    "get_logs:",
    "get_traces:",
    "get_correlations:",
    "mcp_k8s_obs_get_aggregate:",
    "mcp_k8s_obs_get_metrics:",
    "mcp_k8s_obs_get_logs:",
    "mcp_k8s_obs_get_traces:",
    "mcp_k8s_obs_get_correlations:",
    "latest_value=",
    "peak_value=",
    "sample_count=",
    "total_lines=",
    "error_trace_count=",
    '"tool":',
    "'tool':",
)
REQUIRED_TOOLS_FIRST_TOOL = "get_aggregate"

Message = dict[str, Any]


class HermesRCAAgent:
    """
    Calls a Hermes OpenAI-compatible API server for bounded RCA investigation.

    Hermes should be started separately and exposed via HERMES_API_URL. Run
    Hermes with a locked-down profile if you do not want its built-in tools.
    """

    def __init__(
        self,
        api_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 90.0,
        prometheus: PrometheusClient | None = None,
        loki: LokiClient | None = None,
        jaeger: JaegerClient | None = None,
        correlator: Correlator | None = None,
        tools_enabled: bool = True,
        investigation_mode: str = "tools_first",
        max_tool_rounds: int = 4,
        max_tool_calls: int = 8,
        tool_lookback_max_minutes: int = 120,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout_seconds)
        self._prometheus = prometheus
        self._loki = loki
        self._jaeger = jaeger
        self._correlator = correlator or Correlator()
        self._suspicious_absence_detector = SuspiciousAbsenceDetector()
        self._tools_enabled = tools_enabled
        self._investigation_mode = (
            investigation_mode
            if investigation_mode in {"dossier", "tools_first"}
            else "tools_first"
        )
        self._max_tool_rounds = max(0, max_tool_rounds)
        self._max_tool_calls = max(0, max_tool_calls)
        self._tool_lookback_max_minutes = max(1, tool_lookback_max_minutes)

    async def analyze(self, result: UnifiedResult) -> RCAResult:
        """
        Return a structured RCAResult from Hermes.

        If there are no incident signals, RCA is skipped. Transport or parsing
        errors are surfaced as RCAResult(error=...) so the caller can fall back.
        """
        if not self._should_run(result):
            return RCAResult(performed=False)

        messages: list[Message] = [{"role": "user", "content": self._build_prompt(result)}]
        try:
            if self._investigation_mode == "tools_first":
                raw = await self._run_tools_first_conversation(messages, result)
            else:
                raw = await self._run_conversation(messages, result)
            rca = self._parse_response(raw)
            rca.performed = True
            return rca
        except Exception as exc:
            logger.error("Hermes RCA failed: %s", exc)
            return RCAResult(performed=False, error=f"Hermes RCA failed: {exc}")

    async def close(self) -> None:
        await self._client.aclose()

    def _should_run(self, result: UnifiedResult) -> bool:
        return should_run_rca(result)

    def _build_prompt(self, result: UnifiedResult) -> str:
        if self._investigation_mode == "tools_first":
            return self._build_tools_first_prompt(result)
        return self._build_dossier_prompt(result)

    def _build_dossier_prompt(self, result: UnifiedResult) -> str:
        dossier = _build_incident_dossier(result)
        return (
            "You are a bounded autonomous root-cause-analysis agent for a local "
            "observability dashboard. Investigate only from the incident dossier "
            "provided below. Do not claim to have run shell commands, edited code, "
            "restarted services, or queried systems outside this dossier.\n\n"
            "You may call the provided read-only observability tools when the dossier "
            "does not contain enough evidence. Use tools sparingly, and only to inspect "
            "metrics, logs, traces, or correlations.\n\n"
            "Your task:\n"
            "1. Identify the most likely root cause.\n"
            "2. Separate root cause from downstream impact.\n"
            "3. Cite concrete metrics, logs, traces, or correlations in plain language.\n"
            "4. Recommend safe next actions. Do not recommend code edits unless the "
            "evidence clearly supports them.\n\n"
            "Use the concrete signal values provided in the dossier or read-only tool "
            "results. If evidence is weak or indirect, say so instead of inventing data.\n\n"
            "Telemetry gaps are investigative evidence, not proof of health. If logs, "
            "metrics, or traces are unavailable, empty despite observed activity, or "
            "listed under suspicious_absence_events, distinguish an application failure "
            "from an observability blind spot and lower confidence when evidence is "
            "partial.\n\n"
            "Clean logs, zero 5xxs, and successful 2xx traffic do not rule out a "
            "latency-only incident. When trace p99 or http_latency_p99 exceeds the "
            "incident threshold, treat it as incident evidence and explain the slow "
            "service, endpoint, or span instead of concluding there is no local fault "
            "only because error signals are absent.\n\n"
            "For supporting_evidence, write human-readable evidence, not raw tool or metric "
            "dumps. Do not output prefixes like get_aggregate:, get_metrics:, get_logs:, "
            "get_traces:, or get_correlations:, and do not expose internal fields like "
            "latest_value=, peak_value=, sample_count=, total_lines=, or "
            "error_trace_count=. Each item "
            "should be one concise claim, optionally followed by a second explanatory "
            "sentence after ' — '. Example: "
            '"http_error_rate for /crash peaked at 0.1404 req/s — 100% of requests to '
            'that handler failed".\n\n'
            "For log_evidence, copy only log lines that appear in the incident dossier "
            "or read-only log tool results. Do not invent, paraphrase, or cite log lines "
            "that were not provided. Use an empty array when logs are not relevant.\n\n"
            "Respond ONLY with a valid JSON object and no markdown fences. Use this schema:\n"
            "{\n"
            '  "summary": "<one sentence>",\n'
            '  "root_cause": "<technical explanation>",\n'
            '  "confidence": <float 0.0-1.0>,\n'
            '  "supporting_evidence": ['
            '"<plain-English evidence claim — optional short explanation>", "..."],\n'
            '  "log_evidence": [\n'
            '    {"timestamp": "<ISO timestamp or null>", "severity": "<level>", '
            '"message": "<exact provided log excerpt>", "relevance": "<why it matters>", '
            '"labels": {"<key>": "<value>"}}\n'
            "  ],\n"
            '  "recommended_actions": [\n'
            '    {"priority": <1|2|3>, "action": "<action>", "rationale": "<why>"}\n'
            "  ],\n"
            '  "github_search_terms": ["<specific function/error/message terms only>"]\n'
            "}\n\n"
            f"Incident dossier:\n{json.dumps(dossier, indent=2, default=str)}"
        )

    def _build_tools_first_prompt(self, result: UnifiedResult) -> str:
        meta = result.meta
        scope_start, scope_end = _rca_scope(result)
        return (
            "You are a bounded autonomous root-cause-analysis agent for a local "
            "observability dashboard. Investigate only through the registered read-only "
            "Hermes MCP tools and the scoped target metadata below. Do not claim to have "
            "run shell commands, edited code, restarted services, or queried systems "
            "outside those tools.\n\n"
            "Before returning a final RCA, you must first call the aggregator overview "
            "tool mcp_k8s_obs_get_aggregate for the scoped target. After reviewing that "
            "aggregate result, call mcp_k8s_obs_get_metrics, mcp_k8s_obs_get_logs, "
            "mcp_k8s_obs_get_traces, or mcp_k8s_obs_get_correlations only if you still "
            "need deeper evidence to make or verify the decision. These are native "
            "Hermes MCP tools registered from the k8s_obs server, so use them internally "
            "and then return the final RCA in this chat response's content as JSON.\n\n"
            "Scoped investigation target:\n"
            f"- target: {meta.target}\n"
            f"- namespace: {meta.namespace}\n"
            f"- window_start: {scope_start.isoformat()}\n"
            f"- window_end: {scope_end.isoformat()}\n"
            f"- source_query_window_start: {meta.window_start.isoformat()}\n"
            f"- source_query_window_end: {meta.window_end.isoformat()}\n\n"
            f"{_tools_first_exact_window_instruction(result)}\n\n"
            "The source_query_window_* values are the broader event collection range "
            "and are shown only for audit/debugging; do not use them as MCP tool "
            "start/end for this RCA.\n\n"
            "Use only concrete values returned by the read-only tools. If evidence is weak "
            "or indirect, say so instead of inventing data. For log_evidence, copy only "
            "log lines returned by get_logs and use an empty array when logs are not "
            "relevant.\n\n"
            "Telemetry gaps are investigative evidence, not proof of health. If logs, "
            "metrics, or traces are unavailable, empty despite observed activity, or "
            "listed as suspicious absence events, distinguish an application failure from "
            "an observability blind spot and lower confidence when evidence is partial.\n\n"
            "Clean logs, zero 5xxs, and successful 2xx traffic do not rule out a "
            "latency-only incident. When trace p99 or http_latency_p99 exceeds the "
            "incident threshold, treat it as incident evidence and explain the slow "
            "service, endpoint, or span instead of concluding there is no local fault "
            "only because error signals are absent.\n\n"
            "For supporting_evidence, write human-readable evidence, not raw tool or metric "
            "dumps. Do not output prefixes like get_aggregate:, get_metrics:, get_logs:, "
            "get_traces:, or get_correlations:, and do not expose internal fields like "
            "latest_value=, peak_value=, sample_count=, total_lines=, or "
            "error_trace_count=. Each item "
            "should be one concise claim, optionally followed by a second explanatory "
            "sentence after ' — '. Example: "
            '"http_error_rate for /crash peaked at 0.1404 req/s — 100% of requests to '
            'that handler failed".\n\n'
            "Respond ONLY with a valid JSON object and no markdown fences. Use this schema:\n"
            "{\n"
            '  "summary": "<one sentence>",\n'
            '  "root_cause": "<technical explanation>",\n'
            '  "confidence": <float 0.0-1.0>,\n'
            '  "supporting_evidence": ['
            '"<plain-English evidence claim — optional short explanation>", "..."],\n'
            '  "log_evidence": [\n'
            '    {"timestamp": "<ISO timestamp or null>", "severity": "<level>", '
            '"message": "<exact provided log excerpt>", "relevance": "<why it matters>", '
            '"labels": {"<key>": "<value>"}}\n'
            "  ],\n"
            '  "recommended_actions": [\n'
            '    {"priority": <1|2|3>, "action": "<action>", "rationale": "<why>"}\n'
            "  ],\n"
            '  "github_search_terms": ["<specific function/error/message terms only>"]\n'
            "}"
        )

    async def _run_conversation(self, messages: list[Message], result: UnifiedResult) -> str:
        if not self._tools_enabled:
            message = await self._call_hermes(messages, include_tools=False)
            return _message_text(message)

        tool_calls_used = 0
        for round_idx in range(self._max_tool_rounds + 1):
            message = await self._call_hermes(messages, include_tools=True)
            tool_calls = _extract_tool_calls(message)
            if not tool_calls:
                return _message_text(message)

            if round_idx >= self._max_tool_rounds:
                break

            messages.append(message)
            for tool_call in tool_calls:
                tool_calls_used += 1
                if tool_calls_used > self._max_tool_calls:
                    raise RuntimeError("Hermes exceeded max tool call limit")

                tool_name = _tool_name(tool_call)
                t0 = time.monotonic()
                try:
                    args = _tool_args(tool_call)
                    tool_result = await self._run_tool(tool_name, args, result)
                except Exception as exc:
                    tool_result = {"ok": False, "tool": tool_name, "error": str(exc)}
                duration_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    "Hermes tool call name=%s target=%s namespace=%s ok=%s duration_ms=%.0f",
                    tool_name,
                    tool_result.get("target"),
                    tool_result.get("namespace"),
                    tool_result.get("ok"),
                    duration_ms,
                )
                messages.append(_tool_result_message(tool_call, tool_result))

        messages.append(
            {
                "role": "user",
                "content": (
                    "You have reached the tool-call limit. Return the final RCA JSON now "
                    "using only the incident dossier and tool results already provided."
                ),
            }
        )
        final_message = await self._call_hermes(messages, include_tools=False)
        return _message_text(final_message)

    async def _run_tools_first_conversation(
        self,
        messages: list[Message],
        result: UnifiedResult,
    ) -> str:
        if not self._tools_enabled:
            raise RuntimeError("Hermes tools-first mode requires tools to be enabled")

        message = await self._call_hermes(messages, include_tools=False)
        try:
            text = _message_text(message)
            rca = self._parse_response(text)
        except ValueError as exc:
            logger.info(
                "Hermes native MCP chat content was not usable; forcing aggregator "
                "observability evidence target=%s namespace=%s error=%s",
                result.meta.target,
                result.meta.namespace,
                exc,
            )
            return await self._run_tools_first_forced_evidence(
                messages,
                result,
                previous_content=_message_text_or_empty(message),
                reason_prompt=(
                    "The previous response was not valid final RCA JSON. The aggregator "
                    "will provide the required aggregate observability overview as JSON. "
                    "Use only that aggregate overview and the scoped target metadata to "
                    "return the final RCA JSON."
                ),
            )

        weaknesses = _tools_first_rca_weaknesses(rca, result)
        if not weaknesses:
            return text

        related_services = _candidate_related_services(result)
        logger.info(
            "Hermes tools-first retrying weak native RCA target=%s namespace=%s "
            "weaknesses=%s candidate_related_services=%s",
            result.meta.target,
            result.meta.namespace,
            weaknesses,
            related_services,
        )
        messages.append({"role": "assistant", "content": text})
        messages.append(
            {
                "role": "user",
                "content": _build_tools_first_critique_prompt(
                    result,
                    weaknesses=weaknesses,
                    related_services=related_services,
                ),
            }
        )

        retry_message = await self._call_hermes(messages, include_tools=False)
        try:
            retry_text = _message_text(retry_message)
            retry_rca = self._parse_response(retry_text)
        except ValueError as exc:
            logger.info(
                "Hermes tools-first critique response was not usable; forcing aggregator "
                "observability evidence target=%s namespace=%s error=%s "
                "candidate_related_services=%s",
                result.meta.target,
                result.meta.namespace,
                exc,
                related_services,
            )
            return await self._run_tools_first_forced_evidence(
                messages,
                result,
                previous_content=_message_text_or_empty(retry_message),
                reason_prompt=(
                    "The previous response was not usable final RCA JSON. The aggregator "
                    "will provide the required aggregate observability overview as JSON. "
                    "Use only that aggregate overview and the scoped target metadata to "
                    "return the final RCA JSON."
                ),
            )

        retry_weaknesses = _tools_first_rca_weaknesses(retry_rca, result)
        if not retry_weaknesses:
            return retry_text

        logger.info(
            "Hermes tools-first critique response remained weak; forcing aggregator "
            "observability evidence target=%s namespace=%s weaknesses=%s "
            "candidate_related_services=%s",
            result.meta.target,
            result.meta.namespace,
            retry_weaknesses,
            related_services,
        )
        return await self._run_tools_first_forced_evidence(
            messages,
            result,
            previous_content=retry_text,
            reason_prompt=(
                "The previous response was still too weak to accept as final RCA JSON. "
                "The aggregator will provide the required aggregate observability "
                "overview as JSON. Use only that aggregate overview and the scoped "
                "target metadata to return the final RCA JSON."
            ),
        )

    async def _run_tools_first_forced_evidence(
        self,
        messages: list[Message],
        result: UnifiedResult,
        *,
        previous_content: str,
        reason_prompt: str,
    ) -> str:
        messages.append(
            {
                "role": "assistant",
                "content": previous_content,
            }
        )
        messages.append(
            {
                "role": "user",
                "content": reason_prompt,
            }
        )

        aggregate_result: dict[str, Any] | None = None
        aggregate_missing = True
        if self._max_tool_calls > 0:
            t0 = time.monotonic()
            aggregate_result = await self._run_tool(REQUIRED_TOOLS_FIRST_TOOL, {}, result)
            duration_ms = (time.monotonic() - t0) * 1000
            aggregate_missing = False
            logger.info(
                "Hermes tools-first forced tool call name=%s target=%s namespace=%s ok=%s "
                "duration_ms=%.0f",
                REQUIRED_TOOLS_FIRST_TOOL,
                aggregate_result.get("target", result.meta.target),
                aggregate_result.get("namespace", result.meta.namespace),
                aggregate_result.get("ok"),
                duration_ms,
            )
            messages.append(_forced_aggregate_result_message(aggregate_result))
        messages.append(
            {
                "role": "user",
                "content": (
                    "The required aggregate observability overview has been provided above "
                    "as concrete JSON. Return the final RCA JSON now using that aggregate "
                    "overview and "
                    "the scoped target metadata. Do not say the tool outputs are missing "
                    "when the JSON contains ok=true aggregate evidence. "
                    f"Required aggregate tool still missing: {aggregate_missing}."
                ),
            }
        )
        final_message = await self._call_hermes(messages, include_tools=False)
        return _message_text(final_message)

    async def _call_hermes(
        self,
        messages: list[Message],
        include_tools: bool,
    ) -> Message:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False,
        }
        if include_tools:
            payload["tools"] = _tool_schemas()
            payload["tool_choice"] = "auto"

        resp = await self._client.post(
            f"{self._api_url}/chat/completions",
            headers=headers,
            json=payload,
        )

        if resp.status_code >= 400:
            raise RuntimeError(f"Hermes API {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Unexpected Hermes response shape: {data}") from exc

        if not isinstance(message, dict):
            raise ValueError(f"Unexpected Hermes message shape: {message}")
        _log_hermes_response_diagnostic(
            include_tools=include_tools,
            tools_sent="tools" in payload,
            tool_choice=payload.get("tool_choice"),
            message=message,
            finish_reason=choice.get("finish_reason") if isinstance(choice, dict) else None,
            api_key=self._api_key,
        )
        return message

    async def _run_tool(
        self,
        name: str,
        args: dict[str, Any],
        result: UnifiedResult,
    ) -> dict[str, Any]:
        try:
            target = _clean_name(args.get("target")) or result.meta.target
            namespace = _clean_name(args.get("namespace")) or result.meta.namespace
            lookback = _clamp_int(
                args.get("lookback_minutes"),
                default=_window_minutes(result),
                minimum=1,
                maximum=self._tool_lookback_max_minutes,
            )
            start, end = _tool_window_from_args(result, args, lookback)

            if name == "get_aggregate":
                return await self._tool_aggregate(target, namespace, start, end, result)
            if name == "get_metrics":
                return await self._tool_metrics(target, namespace, start, end)
            if name == "get_logs":
                severity = _clean_name(args.get("severity"))
                return await self._tool_logs(target, namespace, start, end, severity)
            if name == "get_traces":
                errors_only = bool(args.get("errors_only", False))
                return await self._tool_traces(target, namespace, start, end, errors_only)
            if name == "get_correlations":
                return await self._tool_correlations(target, namespace, start, end)

            return {"ok": False, "error": f"Unknown tool: {name}"}
        except Exception as exc:
            return {"ok": False, "tool": name, "error": str(exc)}

    async def _tool_aggregate(
        self,
        target: str,
        namespace: str,
        start: datetime,
        end: datetime,
        result: UnifiedResult,
    ) -> dict[str, Any]:
        if (
            target != result.meta.target
            or namespace != result.meta.namespace
            or start != result.meta.window_start
            or end != result.meta.window_end
        ):
            if not (self._prometheus and self._loki and self._jaeger):
                return {
                    "ok": False,
                    "tool": "get_aggregate",
                    "target": target,
                    "namespace": namespace,
                    "error": "A signal client is unavailable",
                }
            metrics, logs, traces = await _gather_signals(
                self._prometheus,
                self._loki,
                self._jaeger,
                target,
                namespace,
                start,
                end,
            )
            correlations = _combined_correlations(
                self._correlator,
                self._suspicious_absence_detector,
                metrics,
                logs,
                traces,
            )
            scoped_result = UnifiedResult(
                meta=QueryMeta(
                    target=target,
                    namespace=namespace,
                    window_start=start,
                    window_end=end,
                ),
                metrics=metrics,
                logs=logs,
                traces=traces,
                correlations=correlations,
            )
        else:
            scoped_result = result

        return {
            "ok": not scoped_result.has_any_errors,
            "tool": "get_aggregate",
            "target": target,
            "namespace": namespace,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "signal_errors": {
                "metrics": scoped_result.metrics.error,
                "logs": scoped_result.logs.error,
                "traces": scoped_result.traces.error,
            },
            "counts": {
                "metric_series": len(scoped_result.metrics.series),
                "total_log_lines": scoped_result.logs.total_lines,
                "error_log_lines": scoped_result.logs.error_count,
                "warn_log_lines": scoped_result.logs.warn_count,
                "traces": len(scoped_result.traces.traces),
                "error_traces": scoped_result.traces.error_trace_count,
                "correlations": len(scoped_result.correlations),
            },
            "aggregate": _build_incident_dossier(scoped_result),
        }

    async def _tool_metrics(
        self,
        target: str,
        namespace: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        if not self._prometheus:
            return {"ok": False, "tool": "get_metrics", "error": "Prometheus client unavailable"}
        metrics = await self._prometheus.query_metrics(target, namespace, start, end)
        return {
            "ok": metrics.error is None,
            "tool": "get_metrics",
            "target": target,
            "namespace": namespace,
            "error": metrics.error,
            "metrics": _summarize_metrics(metrics),
        }

    async def _tool_logs(
        self,
        target: str,
        namespace: str,
        start: datetime,
        end: datetime,
        severity: str | None,
    ) -> dict[str, Any]:
        if not self._loki:
            return {"ok": False, "tool": "get_logs", "error": "Loki client unavailable"}
        logs = await self._loki.query_logs(target, namespace, start, end, limit=TOOL_LOG_LIMIT)
        lines = logs.lines
        if severity:
            requested = _severity_value(severity)
            lines = [line for line in lines if line.severity.value == requested]
        return {
            "ok": logs.error is None,
            "tool": "get_logs",
            "target": target,
            "namespace": namespace,
            "error": logs.error,
            "total_lines": logs.total_lines,
            "error_count": logs.error_count,
            "warn_count": logs.warn_count,
            "logs": _summarize_logs(lines),
        }

    async def _tool_traces(
        self,
        target: str,
        namespace: str,
        start: datetime,
        end: datetime,
        errors_only: bool,
    ) -> dict[str, Any]:
        if not self._jaeger:
            return {"ok": False, "tool": "get_traces", "error": "Jaeger client unavailable"}
        traces = await self._jaeger.query_traces(
            target,
            namespace,
            start,
            end,
            limit=TOOL_TRACE_LIMIT,
        )
        selected = (
            [trace for trace in traces.traces if trace.has_errors]
            if errors_only
            else traces.traces
        )
        return {
            "ok": traces.error is None,
            "tool": "get_traces",
            "target": target,
            "namespace": namespace,
            "error": traces.error,
            "error_trace_count": traces.error_trace_count,
            "p99_duration_ms": traces.p99_duration_ms,
            "traces": _summarize_traces(selected),
        }

    async def _tool_correlations(
        self,
        target: str,
        namespace: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        if not (self._prometheus and self._loki and self._jaeger):
            return {
                "ok": False,
                "tool": "get_correlations",
                "error": "A signal client is unavailable",
            }
        metrics, logs, traces = await _gather_signals(
            self._prometheus,
            self._loki,
            self._jaeger,
            target,
            namespace,
            start,
            end,
        )
        events = _combined_correlations(
            self._correlator,
            self._suspicious_absence_detector,
            metrics,
            logs,
            traces,
        )
        return {
            "ok": not (metrics.error or logs.error or traces.error),
            "tool": "get_correlations",
            "target": target,
            "namespace": namespace,
            "signal_errors": {
                "metrics": metrics.error,
                "logs": logs.error,
                "traces": traces.error,
            },
            "correlations": [
                {
                    "kind": event.kind,
                    "severity": event.severity,
                    "description": event.description,
                    "confidence": event.confidence,
                    "related_metric": event.related_metric,
                    "related_trace_id": event.related_trace_id,
                }
                for event in events[:MAX_CORRELATIONS]
            ],
        }

    def _parse_response(self, raw: str) -> RCAResult:
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Hermes returned invalid JSON: {exc}\n---\n{raw[:500]}") from exc
        if not isinstance(data, dict):
            raise ValueError("Hermes returned JSON that was not an object")

        actions = [
            RecommendedAction(
                priority=max(1, min(3, int(a.get("priority", 2)))),
                action=str(a.get("action", "")),
                rationale=str(a.get("rationale", "")),
            )
            for a in data.get("recommended_actions", [])
            if isinstance(a, dict)
        ]

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        supporting_evidence = [
            str(item).strip()
            for item in data.get("supporting_evidence", [])
            if str(item).strip()
        ]

        rca_data: dict[str, Any] = {
            "summary": str(data.get("summary", "")),
            "root_cause": str(data.get("root_cause", "")),
            "confidence": confidence,
            "supporting_evidence": supporting_evidence,
            "recommended_actions": actions,
            "github_search_terms": [
                str(item) for item in data.get("github_search_terms", [])
            ],
        }
        if "log_evidence" in data:
            rca_data["log_evidence"] = _parse_log_evidence(data["log_evidence"])

        return RCAResult(**rca_data)


def _tools_first_rca_weaknesses(
    rca: RCAResult,
    result: UnifiedResult | None = None,
) -> list[str]:
    weaknesses: list[str] = []
    if not rca.summary.strip():
        weaknesses.append("summary is empty")
    if not rca.root_cause.strip():
        weaknesses.append("root_cause is empty")
    if rca.confidence < MIN_TOOLS_FIRST_CONFIDENCE:
        weaknesses.append(
            f"confidence {rca.confidence:.3f} below {MIN_TOOLS_FIRST_CONFIDENCE:.3f}"
        )

    evidence = [item.strip() for item in rca.supporting_evidence if item.strip()]
    concrete_evidence = [
        item
        for item in evidence
        if len(item) >= 16 and not _looks_like_raw_tool_dump(item)
    ]
    if len(concrete_evidence) < MIN_TOOLS_FIRST_CONCRETE_EVIDENCE:
        weaknesses.append(
            "supporting_evidence has fewer than "
            f"{MIN_TOOLS_FIRST_CONCRETE_EVIDENCE} concrete claims"
        )
    if any(_looks_like_raw_tool_dump(item) for item in evidence):
        weaknesses.append("supporting_evidence still looks like raw tool dumps")
    if result is not None:
        weaknesses.extend(_rca_signal_consistency_weaknesses(rca, result))
    return weaknesses


def _rca_signal_consistency_weaknesses(
    rca: RCAResult,
    result: UnifiedResult,
) -> list[str]:
    latency_findings = _latency_findings(result)
    if not latency_findings:
        return []

    weaknesses: list[str] = []
    text = _rca_text(rca)
    trace_p99_ms = result.traces.p99_duration_ms
    has_slow_trace_p99 = (
        trace_p99_ms is not None
        and trace_p99_ms > 1000
    )

    if _denies_local_fault(text) and not _acknowledges_latency_anomaly(text):
        weaknesses.append(
            "RCA denies a local service issue even though scoped latency evidence exists: "
            f"{'; '.join(latency_findings[:2])}"
        )
    if has_slow_trace_p99 and _claims_latency_low(text):
        weaknesses.append(
            "RCA says traces were fast or low-latency even though scoped trace p99 "
            f"is {trace_p99_ms:.0f} ms"
        )
    if _has_latency_correlation(result) and _claims_no_correlations(text):
        weaknesses.append(
            "RCA says there were no correlations even though scoped latency correlation "
            "events are present"
        )
    return weaknesses


def _latency_findings(result: UnifiedResult) -> list[str]:
    findings: list[str] = []
    p99_ms = result.traces.p99_duration_ms
    if p99_ms is not None and p99_ms > 1000:
        findings.append(f"trace p99 latency {p99_ms:.0f} ms exceeds 1000 ms")

    for series in result.metrics.series:
        if not any(keyword in series.name.lower() for keyword in _LATENCY_KEYWORDS):
            continue
        if series.peak_value is None or series.peak_value <= LATENCY_ANOMALY_THRESHOLD_S:
            continue

        labels = _important_metric_labels(series.labels)
        label_text = f" for {labels}" if labels else ""
        findings.append(
            f"{series.name}{label_text} peaked at {series.peak_value:.3g} s "
            f"above {LATENCY_ANOMALY_THRESHOLD_S:.0f} s"
        )

    for event in result.correlations:
        if _is_latency_correlation(event.kind, event.description):
            findings.append(event.description)

    return findings


def _important_metric_labels(labels: dict[str, str]) -> str:
    parts = [
        f"{key}={labels[key]}"
        for key in ("handler", "route", "path", "job", "service")
        if labels.get(key)
    ]
    return ", ".join(parts)


def _rca_text(rca: RCAResult) -> str:
    parts = [rca.summary, rca.root_cause, *rca.supporting_evidence]
    parts.extend(
        f"{action.action} {action.rationale}"
        for action in rca.recommended_actions
    )
    return " ".join(part for part in parts if part).lower()


def _denies_local_fault(text: str) -> bool:
    return any(phrase in text for phrase in _NO_LOCAL_FAULT_PHRASES)


def _acknowledges_latency_anomaly(text: str) -> bool:
    if any(phrase in text for phrase in _LATENCY_ANOMALY_PHRASES):
        return True
    if "p99 latency" in text or "p95 latency" in text:
        return True
    return bool(
        re.search(
            (
                r"p9[59].{0,40}(?:above|exceed|greater|high|elevated|slow|"
                r"[1-9]\d{3,}\s*ms|[1-9](?:\.\d+)?\s*s)"
            ),
            text,
        )
    )


def _claims_latency_low(text: str) -> bool:
    return any(phrase in text for phrase in _LOW_LATENCY_CLAIM_PHRASES)


def _claims_no_correlations(text: str) -> bool:
    return any(phrase in text for phrase in _NO_CORRELATION_PHRASES)


def _has_latency_correlation(result: UnifiedResult) -> bool:
    return any(
        _is_latency_correlation(event.kind, event.description)
        for event in result.correlations
    )


def _is_latency_correlation(kind: str, description: str) -> bool:
    return "latency" in kind.lower() or "latency" in description.lower()


def _looks_like_raw_tool_dump(value: str) -> bool:
    text = value.strip()
    lowered = text.lower()
    return (
        any(marker in lowered for marker in _RAW_EVIDENCE_MARKERS)
        or text.startswith(("{", "["))
    )


def _candidate_related_services(result: UnifiedResult) -> list[str]:
    dossier = _build_incident_dossier(result)
    target = result.meta.target
    services: list[str] = []
    for trace in dossier.get("traces", []):
        if not isinstance(trace, dict):
            continue
        _append_candidate_service(services, trace.get("root_service"), target=target)
        spans = trace.get("spans", [])
        if not isinstance(spans, list):
            continue
        for span in spans:
            if isinstance(span, dict):
                _append_candidate_service(
                    services,
                    span.get("service_name"),
                    target=target,
                )
            if len(services) >= 2:
                return services[:2]
    return services[:2]


def _append_candidate_service(
    services: list[str],
    value: Any,
    *,
    target: str,
) -> None:
    if not isinstance(value, str):
        return
    service = value.strip()
    if not service or service == target or service in services:
        return
    services.append(service)


def _build_tools_first_critique_prompt(
    result: UnifiedResult,
    *,
    weaknesses: list[str],
    related_services: list[str],
) -> str:
    meta = result.meta
    scope_start, scope_end = _rca_scope(result)
    service_hint = (
        ", ".join(related_services)
        if related_services
        else "none surfaced in the incident dossier"
    )
    return (
        "The previous RCA JSON was parseable, but it is not strong enough to accept. "
        f"Weaknesses: {'; '.join(weaknesses)}.\n\n"
        "Do exactly one bounded self-critique pass. Re-check the conclusion using only "
        "the registered read-only Hermes MCP observability tools and the scoped target "
        "metadata below. Stay in the same namespace and incident window.\n\n"
        "Scoped investigation target:\n"
        f"- target: {meta.target}\n"
        f"- namespace: {meta.namespace}\n"
        f"- window_start: {scope_start.isoformat()}\n"
        f"- window_end: {scope_end.isoformat()}\n"
        f"- source_query_window_start: {meta.window_start.isoformat()}\n"
        f"- source_query_window_end: {meta.window_end.isoformat()}\n"
        "- candidate related services already visible in the incident dossier "
        f"(trace.root_service or span.service_name, at most two): {service_hint}\n\n"
        f"{_tools_first_exact_window_instruction(result)} If you inspect a candidate "
        "related service, use that service as target but keep the same namespace, "
        "start, and end. The source_query_window_* values are only the broader event "
        "collection range; do not use them as MCP tool start/end.\n\n"
        "You may widen scope only to those candidate related services, and only within "
        "the same namespace and incident window. Do not inspect other services, "
        "namespaces, time windows, shell commands, code, deployments, or external "
        "systems.\n\n"
        "Return ONLY the final RCA JSON using the existing schema. Strengthen "
        "supporting_evidence into concrete human-readable claims and do not include raw "
        "tool dumps or internal fields."
    )


def _tools_first_exact_window_instruction(result: UnifiedResult) -> str:
    scope_start, scope_end = _rca_scope(result)
    return (
        "For every mcp_k8s_obs_get_aggregate, mcp_k8s_obs_get_metrics, "
        "mcp_k8s_obs_get_logs, mcp_k8s_obs_get_traces, and "
        "mcp_k8s_obs_get_correlations call, pass target, "
        "namespace, start, and end. "
        f"Set start exactly to {scope_start.isoformat()} and end exactly to "
        f"{scope_end.isoformat()}. Do not use lookback_minutes for this scoped "
        "RCA when window_start and window_end are present."
    )


def _build_incident_dossier(result: UnifiedResult) -> dict[str, object]:
    """Create a compact, bounded RCA input from the full UnifiedResult."""
    meta = result.meta
    return {
        "target": meta.target,
        "namespace": meta.namespace,
        "window_start": meta.window_start.isoformat(),
        "window_end": meta.window_end.isoformat(),
        "signal_errors": {
            "metrics": result.metrics.error,
            "logs": result.logs.error,
            "traces": result.traces.error,
        },
        "signal_counts": {
            "metric_series": len(result.metrics.series),
            "total_log_lines": result.logs.total_lines,
            "error_log_lines": result.logs.error_count,
            "warn_log_lines": result.logs.warn_count,
            "traces": len(result.traces.traces),
            "error_traces": result.traces.error_trace_count,
            "correlations": len(result.correlations),
        },
        "suspicious_absence_events": [
            {
                "kind": event.kind,
                "severity": event.severity,
                "description": event.description,
                "related_metric": event.related_metric,
                "confidence": event.confidence,
            }
            for event in result.correlations
            if is_suspicious_absence_event(event)
        ],
        "correlations": [
            {
                "kind": event.kind,
                "severity": event.severity,
                "description": event.description,
                "related_metric": event.related_metric,
                "related_trace_id": event.related_trace_id,
                "related_log_sample": event.related_log_sample,
                "confidence": event.confidence,
            }
            for event in result.correlations[:MAX_CORRELATIONS]
        ],
        "metrics": [
            {
                "name": series.name,
                "labels": series.labels,
                "latest_value": series.latest_value,
                "peak_value": series.peak_value,
                "sample_count": len(series.samples),
                "first_timestamp": (
                    min(series.samples, key=lambda s: s.timestamp).timestamp.isoformat()
                    if series.samples
                    else None
                ),
                "last_timestamp": (
                    max(series.samples, key=lambda s: s.timestamp).timestamp.isoformat()
                    if series.samples
                    else None
                ),
            }
            for series in result.metrics.series[:MAX_METRIC_SERIES]
        ],
        "logs": [
            {
                "timestamp": line.timestamp.isoformat(),
                "severity": line.severity.value,
                "message": line.message[:500],
                "labels": line.labels,
            }
            for line in _select_log_lines(result)
        ],
        "traces": [
            {
                "trace_id": trace.trace_id,
                "root_service": trace.root_service,
                "duration_ms": trace.duration_ms,
                "has_errors": trace.has_errors,
                "spans": [
                    {
                        "service_name": span.service_name,
                        "operation_name": span.operation_name,
                        "duration_ms": span.duration_ms,
                        "is_error": span.is_error,
                        "tags": dict(list(span.tags.items())[:8]),
                    }
                    for span in trace.spans[:MAX_SPANS_PER_TRACE]
                ],
            }
            for trace in _select_traces(result)
        ],
    }


def _select_log_lines(result: UnifiedResult) -> list[LogLine]:
    return _select_relevant_log_lines(result.logs.lines, MAX_LOG_LINES)


def _select_relevant_log_lines(lines: list[LogLine], limit: int) -> list[LogLine]:
    important = [
        line
        for line in lines
        if line.severity in (Severity.ERROR, Severity.CRITICAL, Severity.WARN)
    ]
    selected = important if important else lines
    return selected[-limit:]


def _select_traces(result: UnifiedResult) -> list[Trace]:
    traces = sorted(
        result.traces.traces,
        key=lambda trace: (trace.has_errors, trace.duration_ms),
        reverse=True,
    )
    return traces[:MAX_TRACES]


def _combined_correlations(
    correlator: Correlator,
    suspicious_absence_detector: SuspiciousAbsenceDetector,
    metrics: MetricsSignal,
    logs: LogsSignal,
    traces: TracesSignal,
) -> list[CorrelationEvent]:
    events = [
        *correlator.correlate(metrics, logs, traces),
        *suspicious_absence_detector.detect(metrics, logs, traces),
    ]
    return sorted(events, key=lambda event: _severity_rank(event.severity), reverse=True)


async def _gather_signals(
    prometheus: PrometheusClient,
    loki: LokiClient,
    jaeger: JaegerClient,
    target: str,
    namespace: str,
    start: datetime,
    end: datetime,
) -> tuple[MetricsSignal, LogsSignal, TracesSignal]:
    metrics = await prometheus.query_metrics(target, namespace, start, end)
    logs = await loki.query_logs(target, namespace, start, end, limit=TOOL_LOG_LIMIT)
    traces = await jaeger.query_traces(target, namespace, start, end, limit=TOOL_TRACE_LIMIT)
    return metrics, logs, traces


def _severity_rank(severity: str) -> int:
    return {"error": 3, "warn": 2, "info": 1}.get(severity, 0)


def _tool_schemas() -> list[dict[str, Any]]:
    shared = {
        "target": {
            "type": "string",
            "description": "Service name to inspect. Omit to use the RCA target.",
        },
        "namespace": {
            "type": "string",
            "description": "Namespace to inspect. Omit to use the RCA namespace.",
        },
        "lookback_minutes": {
            "type": "integer",
            "minimum": 1,
            "description": "Lookback window. Values are clamped by the aggregator.",
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
            "type": "function",
            "function": {
                "name": "get_aggregate",
                "description": (
                    "Fetch the aggregator's compact cross-signal incident overview. "
                    "Call this before drilling into individual signal tools."
                ),
                "parameters": {
                    "type": "object",
                    "properties": shared,
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_metrics",
                "description": "Fetch compact Prometheus metric summaries for a service.",
                "parameters": {
                    "type": "object",
                    "properties": shared,
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_logs",
                "description": "Fetch recent Loki log lines for a service.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        **shared,
                        "severity": {
                            "type": "string",
                            "enum": ["debug", "info", "warn", "warning", "error", "critical"],
                            "description": "Optional severity filter.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_traces",
                "description": "Fetch compact Jaeger trace summaries for a service.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        **shared,
                        "errors_only": {
                            "type": "boolean",
                            "description": "Only return traces that contain error spans.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_correlations",
                "description": "Fetch all three signals and rerun correlation rules.",
                "parameters": {
                    "type": "object",
                    "properties": shared,
                    "additionalProperties": False,
                },
            },
        },
    ]


def _extract_tool_calls(message: Message) -> list[dict[str, Any]]:
    calls = message.get("tool_calls") or []
    return [call for call in calls if isinstance(call, dict)]


def _tool_name(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function") or {}
    return str(function.get("name", ""))


def _tool_args(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function") or {}
    raw = function.get("arguments", "{}")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid tool arguments JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return parsed


def _tool_result_message(tool_call: dict[str, Any], tool_result: dict[str, Any]) -> Message:
    return {
        "role": "tool",
        "tool_call_id": str(tool_call.get("id", "")),
        "name": _tool_name(tool_call),
        "content": json.dumps(tool_result, default=str),
    }


def _forced_aggregate_result_message(tool_result: dict[str, Any]) -> Message:
    return {
        "role": "user",
        "content": (
            "The aggregator executed the required aggregate observability overview "
            "because the previous assistant responses did not produce usable final RCA "
            "JSON after the required first tool. Treat this JSON as concrete "
            "observability evidence for the scoped RCA; cite specific values from it "
            "and do not report that tool output is unavailable unless ok=false or the "
            "aggregate result is empty:\n"
            f"{json.dumps(tool_result, indent=2, default=str)}"
        ),
    }


def _log_hermes_response_diagnostic(
    *,
    include_tools: bool,
    tools_sent: bool,
    tool_choice: Any,
    message: Message,
    finish_reason: Any,
    api_key: str | None,
) -> None:
    tool_call_count = len(_extract_tool_calls(message))
    diagnostic = {
        "include_tools": include_tools,
        "tools_sent": tools_sent,
        "tool_choice": tool_choice,
        "message_keys": sorted(str(key) for key in message.keys()),
        "tool_call_count": tool_call_count,
        "content_present": _message_has_content(message),
        "finish_reason": finish_reason,
    }
    if tool_call_count == 0:
        diagnostic["content_preview"] = _redacted_content_preview(
            message.get("content"),
            api_key=api_key,
        )

    if include_tools and tool_call_count == 0:
        logger.warning("Hermes returned no tool calls: %s", diagnostic)
    else:
        logger.info("Hermes response diagnostic: %s", diagnostic)


def _message_has_content(message: Message) -> bool:
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(
            isinstance(item, dict) and bool(str(item.get("text", "")).strip())
            for item in content
        )
    return content is not None


def _redacted_content_preview(content: Any, *, api_key: str | None) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        )
    elif content is None:
        text = ""
    else:
        text = str(content)

    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,}\]]+",
        r"\1[REDACTED]",
        text,
    )
    text = " ".join(text.split())
    return text[:HERMES_CONTENT_PREVIEW_CHARS]


def _message_text(message: Message) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        ]
        text = "\n".join(part for part in parts if part)
        if text:
            return text
    raise ValueError("Hermes response did not contain final text")


def _message_text_or_empty(message: Message) -> str:
    try:
        return _message_text(message)
    except ValueError:
        return ""


def _clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _window_minutes(result: UnifiedResult) -> int:
    start, end = _rca_scope(result)
    seconds = (end - start).total_seconds()
    return max(1, int(seconds // 60) or 1)


def _tool_window(result: UnifiedResult, lookback_minutes: int) -> tuple[datetime, datetime]:
    scope_start, end = _rca_scope(result)
    requested_start = end - timedelta(minutes=lookback_minutes)
    start = max(scope_start, requested_start)
    return start, end


def _tool_window_from_args(
    result: UnifiedResult,
    args: dict[str, Any],
    lookback_minutes: int,
) -> tuple[datetime, datetime]:
    raw_start = _clean_name(args.get("start"))
    raw_end = _clean_name(args.get("end"))
    if bool(raw_start) != bool(raw_end):
        raise ValueError("Both start and end must be provided together")
    if raw_start and raw_end:
        start = _parse_required_datetime(raw_start, "start")
        end = _parse_required_datetime(raw_end, "end")
        if start >= end:
            raise ValueError("start must be before end")
        return start, end
    return _tool_window(result, lookback_minutes)


def _rca_scope(result: UnifiedResult) -> tuple[datetime, datetime]:
    query_start = _as_utc_datetime(result.meta.window_start)
    query_end = _as_utc_datetime(result.meta.window_end)
    if query_start >= query_end:
        return query_start, query_end

    timestamps = _incident_timestamps(result)
    if not timestamps:
        return query_start, query_end

    cluster = _latest_timestamp_cluster(timestamps)
    start = min(cluster) - timedelta(seconds=RCA_SCOPE_SINGLE_EVENT_PAD_SECONDS)
    end = max(cluster) + timedelta(seconds=RCA_SCOPE_SINGLE_EVENT_PAD_SECONDS)

    start = max(query_start, start)
    end = min(query_end, end)
    if start >= end:
        return query_start, query_end
    return start, end


def _incident_timestamps(result: UnifiedResult) -> list[datetime]:
    timestamps: list[datetime] = []

    if result.timeline.events:
        timestamps.extend(_as_utc_datetime(event.timestamp) for event in result.timeline.events)

    for series in result.metrics.series:
        timestamps.extend(_metric_incident_timestamps(series))

    timestamps.extend(
        _as_utc_datetime(line.timestamp)
        for line in result.logs.lines
        if line.severity in (Severity.ERROR, Severity.CRITICAL, Severity.WARN)
    )

    for trace in result.traces.traces:
        if trace.has_errors or trace.duration_ms >= LATENCY_ANOMALY_THRESHOLD_MS:
            timestamps.extend(_as_utc_datetime(span.start_time) for span in trace.spans)

    return sorted(set(timestamps))


def _metric_incident_timestamps(series: Any) -> list[datetime]:
    samples = [
        sample
        for sample in getattr(series, "samples", [])
        if sample.value is not None
    ]
    if not samples:
        return []

    name = str(getattr(series, "name", "")).lower()
    values = [float(sample.value) for sample in samples]
    peak = max(values)
    threshold: float | None = None
    if "error" in name:
        threshold = ERROR_RATE_ANOMALY_THRESHOLD
    elif any(keyword in name for keyword in _LATENCY_KEYWORDS):
        threshold = LATENCY_ANOMALY_THRESHOLD_S
    elif "restart" in name:
        threshold = 0.0

    if threshold is not None:
        candidates = [
            sample
            for sample in samples
            if sample.value is not None and float(sample.value) > threshold
        ]
    else:
        candidates = [
            sample
            for sample in samples
            if sample.value is not None and float(sample.value) == peak and peak > 0
        ]
    return [_as_utc_datetime(sample.timestamp) for sample in candidates]


def _latest_timestamp_cluster(timestamps: list[datetime]) -> list[datetime]:
    if not timestamps:
        return []
    ordered = sorted(_as_utc_datetime(timestamp) for timestamp in timestamps)
    cluster = [ordered[-1]]
    for timestamp in reversed(ordered[:-1]):
        if (cluster[0] - timestamp).total_seconds() > RCA_SCOPE_CLUSTER_SECONDS:
            break
        cluster.insert(0, timestamp)
    return cluster


def _as_utc_datetime(value: datetime) -> datetime:
    if value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_required_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name} timestamp: {value}") from exc
    return _as_utc_datetime(parsed)


def _severity_value(value: str) -> str:
    normalized = value.lower().strip()
    if normalized == "warning":
        return Severity.WARN.value
    return normalized


def _parse_log_evidence(value: Any) -> list[LogEvidence]:
    if not isinstance(value, list):
        return []

    evidence: list[LogEvidence] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        message = str(item.get("message", "")).strip()
        if not message:
            continue

        raw_labels = item.get("labels", {})
        labels = (
            {str(key): str(val) for key, val in raw_labels.items()}
            if isinstance(raw_labels, dict)
            else {}
        )

        evidence.append(
            LogEvidence(
                timestamp=_parse_optional_datetime(item.get("timestamp")),
                severity=str(item.get("severity", "")),
                message=message,
                relevance=str(item.get("relevance", "")),
                labels=labels,
            )
        )
    return evidence


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _summarize_metrics(metrics: MetricsSignal) -> list[dict[str, Any]]:
    return [
        {
            "name": series.name,
            "labels": series.labels,
            "latest_value": series.latest_value,
            "peak_value": series.peak_value,
            "sample_count": len(series.samples),
        }
        for series in metrics.series[:MAX_METRIC_SERIES]
    ]


def _has_latency_metric_anomaly(metrics: MetricsSignal) -> bool:
    return any(
        any(keyword in series.name.lower() for keyword in _LATENCY_KEYWORDS)
        and series.peak_value is not None
        and series.peak_value > LATENCY_ANOMALY_THRESHOLD_S
        for series in metrics.series
    )


def _summarize_logs(lines: list[LogLine], limit: int = TOOL_LOG_LIMIT) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": line.timestamp.isoformat(),
            "severity": line.severity.value,
            "message": line.message[:500],
            "labels": line.labels,
        }
        for line in _select_relevant_log_lines(lines, limit)
    ]


def _summarize_traces(
    traces: list[Trace],
    limit: int = TOOL_TRACE_LIMIT,
) -> list[dict[str, Any]]:
    return [
        {
            "trace_id": trace.trace_id,
            "root_service": trace.root_service,
            "duration_ms": trace.duration_ms,
            "has_errors": trace.has_errors,
            "spans": [
                {
                    "service_name": span.service_name,
                    "operation_name": span.operation_name,
                    "duration_ms": span.duration_ms,
                    "is_error": span.is_error,
                    "tags": dict(list(span.tags.items())[:8]),
                }
                for span in trace.spans[:MAX_SPANS_PER_TRACE]
            ],
        }
        for trace in traces[:limit]
    ]
