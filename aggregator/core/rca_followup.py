"""
Hermes-first conversational follow-up assistant for completed RCA results.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

import httpx

from aggregator.core.hermes_rca_agent import (
    HermesRCAAgent,
    Message,
    _message_text,
)
from aggregator.core.rca_analyzer import ANTHROPIC_API_URL, MODEL
from aggregator.models.followup import FollowUpMessage, FollowUpResponse
from aggregator.models.result import UnifiedResult

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 12
MAX_ACTIONS = 6
MAX_CORRELATIONS = 8
MAX_METRICS = 8
MAX_LOGS = 12
MAX_TRACES = 6
MAX_SPANS = 6
MAX_CONTEXTUAL_QUESTION_TOKENS = 8

OBSERVABILITY_KEYWORDS = {
    "affected",
    "alert",
    "blast",
    "breaker",
    "cause",
    "check",
    "confirm",
    "correlation",
    "dependency",
    "downstream",
    "evidence",
    "error",
    "errors",
    "fix",
    "health",
    "impact",
    "incident",
    "latency",
    "log",
    "logs",
    "metric",
    "metrics",
    "mitigate",
    "namespace",
    "rca",
    "remediation",
    "restart",
    "retry",
    "root",
    "scope",
    "service",
    "timeout",
    "trace",
    "traces",
    "upstream",
    "verify",
    "window",
}
CONTEXTUAL_SHORT_QUESTION_TOKENS = {
    "how",
    "what",
    "when",
    "where",
    "which",
    "why",
    "this",
    "that",
    "it",
    "they",
    "those",
}

UNRELATED_FOLLOWUP_REMINDER = (
    "This follow-up chat is only for questions about the current RCA and incident. "
    "Ask about blast radius, supporting evidence, first checks, affected services, "
    "logs, traces, metrics, or how to confirm the fix."
)

PromptMode = Literal["hermes_native", "context_only"]


class RcaFollowUpAssistant:
    """
    Answers developer follow-up questions about an existing RCA result.

    Hermes is the primary path. Anthropic is used only when Hermes cannot return
    usable text.
    """

    def __init__(
        self,
        *,
        hermes: HermesRCAAgent,
        anthropic_api_key: str | None,
        anthropic_model: str = MODEL,
    ) -> None:
        self._hermes = hermes
        self._anthropic_api_key = anthropic_api_key
        self._anthropic_model = anthropic_model
        self._client = httpx.AsyncClient(timeout=60.0)

    async def answer(
        self,
        *,
        incident: UnifiedResult,
        question: str,
        history: list[FollowUpMessage],
    ) -> FollowUpResponse:
        if _should_remind_scope(incident=incident, question=question, history=history):
            return FollowUpResponse(
                answer=UNRELATED_FOLLOWUP_REMINDER,
                provider=None,
                fallback_used=False,
            )

        hermes_error: str | None = None
        try:
            answer = await self._answer_with_hermes(incident, question, history)
            if answer.strip():
                return FollowUpResponse(
                    answer=answer.strip(),
                    provider="hermes",
                    fallback_used=False,
                )
            hermes_error = "Hermes returned an empty answer"
        except Exception as exc:
            hermes_error = str(exc)
            logger.warning("Hermes follow-up failed; falling back to Anthropic: %s", exc)

        if not self._anthropic_api_key:
            return FollowUpResponse(
                answer="",
                provider=None,
                fallback_used=True,
                error=(
                    "Hermes follow-up failed"
                    + (f": {hermes_error}" if hermes_error else "")
                    + "; Anthropic fallback is not configured."
                ),
            )

        try:
            fallback = await self._answer_with_anthropic(incident, question, history)
        except Exception as exc:
            logger.warning("Anthropic follow-up fallback failed: %s", exc)
            return FollowUpResponse(
                answer="",
                provider=None,
                fallback_used=True,
                error=(
                    "Hermes follow-up failed"
                    + (f": {hermes_error}" if hermes_error else "")
                    + f"; Anthropic fallback failed: {exc}"
                ),
            )
        if fallback.strip():
            return FollowUpResponse(
                answer=fallback.strip(),
                provider="anthropic",
                fallback_used=True,
                error=hermes_error,
            )

        return FollowUpResponse(
            answer="",
            provider=None,
            fallback_used=True,
            error=(
                "Hermes follow-up failed"
                + (f": {hermes_error}" if hermes_error else "")
                + "; Anthropic fallback returned no answer."
            ),
        )

    async def close(self) -> None:
        await self._client.aclose()
        await self._hermes.close()

    async def _answer_with_hermes(
        self,
        incident: UnifiedResult,
        question: str,
        history: list[FollowUpMessage],
    ) -> str:
        prompt_mode: PromptMode = (
            "hermes_native" if self._hermes._tools_enabled else "context_only"
        )
        messages = _build_followup_messages(
            incident=incident,
            question=question,
            history=history,
            prompt_mode=prompt_mode,
        )
        raw = await self._run_hermes_followup_conversation(messages, incident)
        return _clean_answer(raw)

    async def _run_hermes_followup_conversation(
        self,
        messages: list[Message],
        incident: UnifiedResult,
    ) -> str:
        del incident
        message = await self._hermes._call_hermes(messages, include_tools=False)
        return _message_text(message)

    async def _answer_with_anthropic(
        self,
        incident: UnifiedResult,
        question: str,
        history: list[FollowUpMessage],
    ) -> str:
        prompt = _build_anthropic_prompt(incident=incident, question=question, history=history)
        resp = await self._client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": self._anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self._anthropic_model,
                "max_tokens": 1200,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic API {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        try:
            return _clean_answer(str(data["content"][0]["text"]))
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Unexpected Anthropic response shape: {data}") from exc


def _build_followup_messages(
    *,
    incident: UnifiedResult,
    question: str,
    history: list[FollowUpMessage],
    prompt_mode: PromptMode,
) -> list[Message]:
    related_services = _related_services(incident)
    related_text = ", ".join(related_services) if related_services else "none"
    messages: list[Message] = [
        {
            "role": "system",
            "content": _system_prompt(prompt_mode=prompt_mode),
        },
        {
            "role": "user",
            "content": (
                "Scoped follow-up target:\n"
                f"- target: {incident.meta.target}\n"
                f"- namespace: {incident.meta.namespace}\n"
                f"- window_start: {incident.meta.window_start.isoformat()}\n"
                f"- window_end: {incident.meta.window_end.isoformat()}\n"
                "- candidate related services already visible in traces or "
                f"correlations: {related_text}\n"
                "Stay in the same namespace and incident window. Inspect only the "
                "scoped target service unless the incident context already shows one "
                "of those related services as part of the same failure path."
            ),
        },
        {
            "role": "user",
            "content": (
                "Incident context:\n"
                f"{json.dumps(_build_followup_context(incident), indent=2, default=str)}"
            ),
        },
    ]
    for item in history[-MAX_HISTORY_MESSAGES:]:
        messages.append({"role": item.role, "content": item.content})
    messages.append({"role": "user", "content": question})
    return messages


def _build_anthropic_prompt(
    *,
    incident: UnifiedResult,
    question: str,
    history: list[FollowUpMessage],
) -> str:
    messages = _build_followup_messages(
        incident=incident,
        question=question,
        history=history,
        prompt_mode="context_only",
    )
    return "\n\n".join(
        f"{message['role'].upper()}:\n{message['content']}"
        for message in messages
    )


def _system_prompt(*, prompt_mode: PromptMode) -> str:
    if prompt_mode == "hermes_native":
        tool_guidance = (
            "You may use your registered read-only Hermes MCP observability tools "
            "internally when the current incident context is not enough. Use them "
            "sparingly, keep them scoped to the target, namespace, and incident "
            "window, and answer in plain language rather than exposing tool internals."
        )
    else:
        tool_guidance = (
            "You cannot call tools in this path. Answer only from the incident "
            "context and conversation history, and say what is not known when evidence "
            "is missing."
        )
    return (
        "You are an inline follow-up assistant for a Kubernetes observability RCA. "
        "Answer the developer's question conversationally and concisely. Stay scoped "
        "to the incident unless the question asks for next checks or blast radius. "
        "If the developer asks something unrelated to this RCA or incident "
        "investigation, do not answer it. Briefly remind them that this chat is only "
        "for RCA follow-ups and invite an incident-scoped question instead. "
        "Separate observed evidence from inference. Do not claim to run shell commands, "
        "edit code, restart services, deploy changes, or inspect systems outside the "
        f"provided incident context and allowed tools. {tool_guidance}"
    )


def _build_followup_context(incident: UnifiedResult) -> dict[str, Any]:
    meta = incident.meta
    rca = incident.rca
    return {
        "target": meta.target,
        "namespace": meta.namespace,
        "window_start": meta.window_start.isoformat(),
        "window_end": meta.window_end.isoformat(),
        "rca": {
            "summary": rca.summary,
            "root_cause": rca.root_cause,
            "confidence": rca.confidence,
            "supporting_evidence": rca.supporting_evidence,
            "recommended_actions": [
                {
                    "priority": action.priority,
                    "action": action.action,
                    "rationale": action.rationale,
                }
                for action in rca.recommended_actions[:MAX_ACTIONS]
            ],
            "code_references": [
                {
                    "path": ref.path,
                    "line_number": ref.line_number,
                    "relevance": ref.relevance,
                }
                for ref in rca.code_references[:MAX_ACTIONS]
            ],
        },
        "correlations": [
            {
                "kind": event.kind,
                "severity": event.severity,
                "description": event.description,
                "related_metric": event.related_metric,
                "related_trace_id": event.related_trace_id,
                "confidence": event.confidence,
            }
            for event in incident.correlations[:MAX_CORRELATIONS]
        ],
        "metrics": [
            {
                "name": series.name,
                "labels": series.labels,
                "latest_value": series.latest_value,
                "peak_value": series.peak_value,
                "sample_count": len(series.samples),
            }
            for series in incident.metrics.series[:MAX_METRICS]
        ],
        "logs": [
            {
                "timestamp": line.timestamp.isoformat(),
                "severity": line.severity.value,
                "message": line.message[:500],
                "labels": line.labels,
            }
            for line in _select_logs(incident)[:MAX_LOGS]
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
                    }
                    for span in trace.spans[:MAX_SPANS]
                ],
            }
            for trace in sorted(
                incident.traces.traces,
                key=lambda trace: (trace.has_errors, trace.duration_ms),
                reverse=True,
            )[:MAX_TRACES]
        ],
    }


def _select_logs(incident: UnifiedResult) -> list[Any]:
    important = [
        line
        for line in incident.logs.lines
        if line.severity.value in {"error", "critical", "warn"}
    ]
    return (important or incident.logs.lines)[-MAX_LOGS:]


def _related_services(incident: UnifiedResult) -> list[str]:
    target = incident.meta.target
    services: list[str] = []

    for trace in incident.traces.traces:
        if trace.root_service:
            _append_related_service(services, trace.root_service, target=target)
        for span in trace.spans:
            _append_related_service(services, span.service_name, target=target)
            if len(services) >= 3:
                return services
    return services


def _append_related_service(
    services: list[str],
    service: str | None,
    *,
    target: str,
) -> None:
    if not service or service == target or service in services:
        return
    services.append(service)


def _should_remind_scope(
    *,
    incident: UnifiedResult,
    question: str,
    history: list[FollowUpMessage],
) -> bool:
    tokens = _question_tokens(question)
    if not tokens:
        return False
    if _has_incident_overlap(incident=incident, tokens=tokens):
        return False
    if OBSERVABILITY_KEYWORDS.intersection(tokens):
        return False
    if len(tokens) <= 3:
        return False
    if (
        history
        and len(tokens) <= MAX_CONTEXTUAL_QUESTION_TOKENS
        and CONTEXTUAL_SHORT_QUESTION_TOKENS.intersection(tokens)
    ):
        return False
    return True


def _has_incident_overlap(*, incident: UnifiedResult, tokens: set[str]) -> bool:
    incident_terms = set(_tokenize_service_name(incident.meta.target))
    for service in _related_services(incident):
        incident_terms.update(_tokenize_service_name(service))
    incident_terms.update(_tokenize_text(incident.rca.summary))
    incident_terms.update(_tokenize_text(incident.rca.root_cause))
    return bool(incident_terms.intersection(tokens))


def _question_tokens(question: str) -> set[str]:
    return set(_tokenize_text(question))


def _tokenize_service_name(value: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) >= 2]


def _tokenize_text(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) >= 3]


def _clean_answer(raw: str) -> str:
    answer = raw.strip()
    if answer.startswith("```"):
        answer = answer.removeprefix("```markdown").removeprefix("```").strip()
    if answer.endswith("```"):
        answer = answer[:-3].strip()
    return answer
