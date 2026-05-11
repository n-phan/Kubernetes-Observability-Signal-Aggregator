"""
Core aggregator — the heart of the system.

Accepts a QueryRequest, fans out to all three backends concurrently,
collects results into a UnifiedResult, and runs correlation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path

import yaml

from aggregator import history
from aggregator.clients.github import GitHubLinker
from aggregator.clients.jaeger import JaegerClient
from aggregator.clients.loki import LokiClient
from aggregator.clients.prometheus import PrometheusClient
from aggregator.config import settings
from aggregator.core.correlator import Correlator
from aggregator.core.hermes_rca_agent import HermesRCAAgent
from aggregator.core.rca_analyzer import RCAAnalyzer
from aggregator.core.timeline import build_timeline
from aggregator.core.rca_followup import RcaFollowUpAssistant
from aggregator.core.suspicious_absence import SuspiciousAbsenceDetector
from aggregator.models.followup import FollowUpMessage, FollowUpResponse
from aggregator.core.timeline import build_timeline
from aggregator.models.query import QueryRequest
from aggregator.models.rca import LogEvidence, RCAResult
from aggregator.models.result import CorrelationEvent, QueryMeta, UnifiedResult
from aggregator.models.signals import LogsSignal, MetricsSignal, Severity, TracesSignal

logger = logging.getLogger(__name__)

MAX_RCA_LOG_EVIDENCE = 5


async def _skipped(
    signal: MetricsSignal | LogsSignal | TracesSignal,
) -> MetricsSignal | LogsSignal | TracesSignal:
    """Return a default signal when a backend is disabled via --no-* flag."""
    return signal


class SignalAggregator:
    """
    The main orchestrator.

    Usage:
        aggregator = SignalAggregator()
        result = await aggregator.query(request)
    """

    def __init__(
        self,
        prometheus: PrometheusClient | None = None,
        loki: LokiClient | None = None,
        jaeger: JaegerClient | None = None,
        correlator: Correlator | None = None,
        rca_analyzer: RCAAnalyzer | None = None,
        followup_assistant: RcaFollowUpAssistant | None = None,
        github_linker: GitHubLinker | None = None,
        suspicious_absence_detector: SuspiciousAbsenceDetector | None = None,
    ) -> None:
        # Clients are injected for testability; defaults use settings
        self._prometheus = prometheus or PrometheusClient()
        self._loki = loki or LokiClient()
        self._jaeger = jaeger or JaegerClient()
        self._correlator = correlator or Correlator()
        self._suspicious_absence_detector = (
            suspicious_absence_detector or SuspiciousAbsenceDetector()
        )
        simple_rca_analyzer = (
            RCAAnalyzer(
                api_key=settings.anthropic_api_key,
                repo=settings.github_repo,
            )
            if settings.rca_enabled
            else None
        )
        if rca_analyzer is not None:
            self._rca_analyzer = rca_analyzer
            self._fallback_rca_analyzer = None
        elif not settings.rca_enabled:
            self._rca_analyzer = None
            self._fallback_rca_analyzer = None
        elif settings.rca_mode == "hermes":
            self._rca_analyzer = HermesRCAAgent(
                api_url=settings.hermes_api_url,
                api_key=settings.hermes_api_key,
                model=settings.hermes_model,
                timeout_seconds=settings.hermes_timeout_seconds,
                prometheus=self._prometheus,
                loki=self._loki,
                jaeger=self._jaeger,
                correlator=self._correlator,
                tools_enabled=settings.hermes_tools_enabled,
                investigation_mode=settings.hermes_investigation_mode,
                max_tool_rounds=settings.hermes_max_tool_rounds,
                max_tool_calls=settings.hermes_max_tool_calls,
                tool_lookback_max_minutes=settings.hermes_tool_lookback_max_minutes,
            )
            self._fallback_rca_analyzer = simple_rca_analyzer
        else:
            self._rca_analyzer = simple_rca_analyzer
            self._fallback_rca_analyzer = None
        self._followup_assistant = followup_assistant
        self._github_linker = github_linker

    async def query(self, request: QueryRequest) -> UnifiedResult:
        """
        Execute a unified observability query.

        All three backends are queried concurrently via asyncio.gather.
        Individual backend failures are caught and represented as
        error fields in the respective signal — they do not abort
        the overall query.
        """
        window = request.resolve_window(settings.default_lookback_minutes)
        t0 = time.monotonic()

        logger.info(
            "Querying target=%s namespace=%s window=[%s → %s] rca=%s",
            request.target,
            request.namespace,
            window.start.isoformat(),
            window.end.isoformat(),
            request.include_rca,
        )

        # Fan out all three queries concurrently
        metrics_task = (
            self._safe_metrics(request.target, request.namespace, window.start, window.end)
            if request.include_metrics
            else _skipped(MetricsSignal())
        )
        logs_task = (
            self._safe_logs(request.target, request.namespace, window.start, window.end)
            if request.include_logs
            else _skipped(LogsSignal())
        )
        traces_task = (
            self._safe_traces(request.target, request.namespace, window.start, window.end)
            if request.include_traces
            else _skipped(TracesSignal())
        )

        metrics, logs, traces = await asyncio.gather(metrics_task, logs_task, traces_task)

        # Correlate obvious anomalies plus suspicious telemetry gaps.
        correlations = self._correlator.correlate(metrics, logs, traces)
        absence_events = self._suspicious_absence_detector.detect(
            metrics,
            logs,
            traces,
            include_metrics=request.include_metrics,
            include_logs=request.include_logs,
            include_traces=request.include_traces,
        )
        correlations = _sort_correlation_events([*correlations, *absence_events])

        # Build incident timeline (causal ordering of events)
        timeline = build_timeline(metrics, logs, traces)

        # Build preliminary result so RCA can read it
        total_ms = (time.monotonic() - t0) * 1000
        result = UnifiedResult(
            meta=QueryMeta(
                target=request.target,
                namespace=request.namespace,
                window_start=window.start,
                window_end=window.end,
                total_duration_ms=total_ms,
            ),
            metrics=metrics,
            logs=logs,
            traces=traces,
            correlations=correlations,
            timeline=timeline,
        )

        # RCA — only runs when explicitly requested and error signals exist
        if request.include_rca and self._rca_analyzer:
            rca = await self._rca_analyzer.analyze(result, request.llm)
            if (
                self._fallback_rca_analyzer
                and not rca.performed
                and rca.error
            ):
                logger.warning("Primary RCA failed; falling back to simple RCA: %s", rca.error)
                fallback_rca = await self._fallback_rca_analyzer.analyze(result)
                if fallback_rca.performed:
                    rca = fallback_rca
            if rca.performed:
                if self._github_linker:
                    rca = await self._github_linker.enrich(rca, logs)
                else:
                    rca = await self._enrich_with_github(rca, logs, request.target)
            if rca.performed and "log_evidence" not in rca.model_fields_set:
                rca.log_evidence = _default_log_evidence(logs)
            result.rca = rca

        # History — record notable queries and attach recurrence info
        # ("has this happened before?"). Best-effort; never fails the query.
        notable = (
            logs.error_count > 0
            or traces.error_trace_count > 0
            or bool(correlations)
            or result.rca.performed
        )
        if notable:
            result.history = await history.record(result)

        total_ms = (time.monotonic() - t0) * 1000
        result.meta.total_duration_ms = total_ms

        logger.info(
            "Query complete: %d metric series, %d log lines, %d traces, "
            "%d correlations, rca=%s in %.0f ms",
            len(metrics.series),
            logs.total_lines,
            len(traces.traces),
            len(correlations),
            result.rca.performed,
            total_ms,
        )

        return result

    async def follow_up(
        self,
        *,
        incident: UnifiedResult,
        question: str,
        history: list[FollowUpMessage],
    ) -> FollowUpResponse:
        if not incident.rca.performed:
            raise ValueError("RCA must be performed before asking follow-up questions")
        if self._followup_assistant is None and settings.rca_enabled:
            self._followup_assistant = self._make_followup_assistant()
        if not self._followup_assistant:
            return FollowUpResponse(
                answer="",
                provider=None,
                fallback_used=False,
                error="RCA follow-up assistant is disabled",
            )
        return await self._followup_assistant.answer(
            incident=incident,
            question=question,
            history=history,
        )

    def _make_followup_assistant(self) -> RcaFollowUpAssistant:
        return RcaFollowUpAssistant(
            hermes=HermesRCAAgent(
                api_url=settings.hermes_api_url,
                api_key=settings.hermes_api_key,
                model=settings.hermes_model,
                timeout_seconds=settings.hermes_timeout_seconds,
                prometheus=self._prometheus,
                loki=self._loki,
                jaeger=self._jaeger,
                correlator=self._correlator,
                tools_enabled=settings.hermes_tools_enabled,
                investigation_mode="dossier",
                max_tool_rounds=settings.hermes_max_tool_rounds,
                max_tool_calls=settings.hermes_max_tool_calls,
                tool_lookback_max_minutes=settings.hermes_tool_lookback_max_minutes,
            ),
            anthropic_api_key=settings.anthropic_api_key,
        )

    # ------------------------------------------------------------------
    # Safe wrappers — catch backend errors, return partial results
    # ------------------------------------------------------------------

    async def _safe_metrics(
        self, target: str, namespace: str, start: datetime, end: datetime
    ) -> MetricsSignal:
        try:
            return await self._prometheus.query_metrics(target, namespace, start, end)
        except Exception as exc:
            logger.error("Prometheus query failed: %s", exc)
            return MetricsSignal(error=str(exc))

    async def _safe_logs(
        self, target: str, namespace: str, start: datetime, end: datetime
    ) -> LogsSignal:
        try:
            return await self._loki.query_logs(target, namespace, start, end)
        except Exception as exc:
            logger.error("Loki query failed: %s", exc)
            return LogsSignal(error=str(exc))

    async def _safe_traces(
        self, target: str, namespace: str, start: datetime, end: datetime
    ) -> TracesSignal:
        try:
            return await self._jaeger.query_traces(target, namespace, start, end)
        except Exception as exc:
            logger.error("Jaeger query failed: %s", exc)
            return TracesSignal(error=str(exc))

    # ------------------------------------------------------------------
    # GitHub enrichment — per-query linker with per-service config
    # ------------------------------------------------------------------

    def _load_service_registry(self) -> dict:
        """
        Load infra/service-registry.yml.  Returns an empty dict on any
        read or parse error so callers never need to guard against it.
        """
        try:
            path = Path(settings.service_registry_path)
            with path.open() as fh:
                return yaml.safe_load(fh) or {}
        except Exception:
            return {}

    async def _enrich_with_github(
        self,
        rca: RCAResult,
        logs: LogsSignal,
        target: str,
    ) -> RCAResult:
        """
        Create a per-query GitHubLinker using the per-service config from
        service-registry.yml, then enrich *rca* with code references.

        Resolution order for each field:
          repo         — registry entry → settings.github_repo
          branch       — registry entry → settings.github_default_branch
          path_prefix  — registry entry (may be empty/None) →
                         "demo/{target}" when using the aggregator repo →
                         None when service has its own dedicated repo
        """
        registry = self._load_service_registry()
        entry: dict = registry.get("services", {}).get(target, {})

        repo   = entry.get("github_repo")   or settings.github_repo
        branch = entry.get("github_branch") or settings.github_default_branch

        # Decide path prefix:
        #   • Registry has an explicit value  → use it (empty string → no prefix)
        #   • Service uses the aggregator repo → fall back to "demo/{target}"
        #   • Service has its own repo         → no prefix (main.py is at root)
        if "github_path_prefix" in entry:
            path_prefix = entry["github_path_prefix"] or None
        elif repo == settings.github_repo:
            path_prefix = settings.github_path_prefix or f"demo/{target}"
        else:
            path_prefix = None  # dedicated repo — file lives at root

        linker = GitHubLinker(
            token=settings.github_token,
            repo=repo,
            default_branch=branch,
            path_prefix=path_prefix,
        )
        try:
            return await linker.enrich(rca, logs)
        finally:
            await linker.close()

    async def close(self) -> None:
        """Close all underlying HTTP clients."""
        closers = [
            self._prometheus.close(),
            self._loki.close(),
            self._jaeger.close(),
        ]
        if self._rca_analyzer:
            closers.append(self._rca_analyzer.close())
        if self._fallback_rca_analyzer:
            closers.append(self._fallback_rca_analyzer.close())
        if self._followup_assistant:
            closers.append(self._followup_assistant.close())
        if self._github_linker:
            closers.append(self._github_linker.close())
        await asyncio.gather(*closers)

    async def __aenter__(self) -> SignalAggregator:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def _default_log_evidence(logs: LogsSignal) -> list[LogEvidence]:
    """
    Populate RCA log evidence from collected Loki lines when the LLM omits it.

    Hermes is instructed to return log_evidence, but models can still produce an
    older schema. The frontend RCA report needs a structured field, so use the
    already-collected logs as a deterministic fallback.
    """
    important = [
        line
        for line in logs.lines
        if line.severity in (Severity.ERROR, Severity.CRITICAL, Severity.WARN)
    ]
    selected = important[-MAX_RCA_LOG_EVIDENCE:]

    evidence: list[LogEvidence] = []
    for line in selected:
        if line.severity in (Severity.ERROR, Severity.CRITICAL):
            relevance = "Error log collected during the RCA window."
        elif line.severity == Severity.WARN:
            relevance = "Warning log collected during the RCA window."
        else:
            relevance = "Recent log collected during the RCA window."

        evidence.append(
            LogEvidence(
                timestamp=line.timestamp,
                severity=line.severity.value,
                message=line.message,
                relevance=relevance,
                labels=line.labels,
            )
        )
    return evidence


def _sort_correlation_events(events: list[CorrelationEvent]) -> list[CorrelationEvent]:
    return sorted(events, key=lambda event: _severity_rank(event.severity), reverse=True)


def _severity_rank(severity: str) -> int:
    return {"error": 3, "warn": 2, "info": 1}.get(severity, 0)
