"""
Root cause analyzer.

Feeds the unified observability signals into a fallback LLM and asks it to:
  1. Identify the most likely root cause
  2. Suggest remediation actions
  3. Produce GitHub search terms for the relevant code

The response is parsed as structured JSON so it slots cleanly
into the RCAResult model.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx

from aggregator.core.rca_gate import should_run_rca
from aggregator.core.suspicious_absence import is_suspicious_absence_event
from aggregator.models.query import LlmConfig
from aggregator.models.rca import LogEvidence, RCAResult, RecommendedAction
from aggregator.models.result import UnifiedResult
from aggregator.models.signals import LogLine, Severity, Span

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"
MODEL = ANTHROPIC_MODEL
OPENAI_API_URL = "https://api.openai.com/v1/responses"
OPENAI_MODEL = "gpt-5.5"

_ANTHROPIC_PROVIDERS = frozenset({"anthropic", "claude"})
_OPENAI_PROVIDERS = frozenset({"openai", "chatgpt"})
_SUPPORTED_PROVIDERS = _ANTHROPIC_PROVIDERS | _OPENAI_PROVIDERS

# How many items to include in the prompt — keeps token count predictable
MAX_LOG_SAMPLES = 20
MAX_TRACE_SPANS = 10
MAX_METRIC_SERIES = 8

class RCAAnalyzer:
    """
    Calls the configured fallback/simple LLM with a structured summary of
    all observability signals and returns a parsed RCAResult.
    """

    def __init__(
        self,
        api_key: str | None = None,
        repo: str | None = None,
        *,
        provider: str = "anthropic",
        openai_api_key: str | None = None,
        openai_model: str = OPENAI_MODEL,
        openai_api_url: str = OPENAI_API_URL,
        anthropic_model: str = ANTHROPIC_MODEL,
        anthropic_api_url: str = ANTHROPIC_API_URL,
    ) -> None:
        """
        api_key  — Anthropic API key (or set ANTHROPIC_API_KEY env var)
        repo     — GitHub repo slug, e.g. "my-org/payment-service"
        """
        self._api_key = api_key
        self._provider = _normalize_provider(provider)
        self._anthropic_model = anthropic_model
        self._anthropic_api_url = anthropic_api_url
        self._openai_api_key = openai_api_key
        self._openai_model = openai_model
        self._openai_api_url = openai_api_url
        self._repo = repo
        self._client = httpx.AsyncClient(timeout=60.0)

    async def analyze(self, result: UnifiedResult, llm: LlmConfig | None = None) -> RCAResult:
        """
        Run root cause analysis on a UnifiedResult.

        `llm` is an optional per-request override (from the frontend Config LLM
        panel). When provided, its provider must be Anthropic/Claude or
        OpenAI/ChatGPT; other providers cause RCA to be skipped with an
        explanatory error. Its
        api_key / model / endpoint, when set, override the server defaults.

        Returns RCAResult(performed=False) if there is nothing to analyze,
        the provider is unsupported, no API key is available, or the API
        call fails.
        """
        if not self._should_run(result):
            return RCAResult(performed=False)

        provider = _normalize_provider(llm.provider if llm and llm.provider else self._provider)
        if provider not in _SUPPORTED_PROVIDERS:
            msg = (
                f"LLM provider '{provider}' is not supported yet - "
                "supported fallback providers are Anthropic and OpenAI"
            )
            logger.warning(msg)
            return RCAResult(performed=False, error=msg)

        if provider in _OPENAI_PROVIDERS:
            api_key = (llm.api_key if llm and llm.api_key else self._openai_api_key) or None
            model = llm.model if llm and llm.model else self._openai_model
            url = llm.endpoint if llm and llm.endpoint else self._openai_api_url
            provider_label = "OpenAI"
        else:
            api_key = (llm.api_key if llm and llm.api_key else self._api_key) or None
            model = llm.model if llm and llm.model else self._anthropic_model
            url = llm.endpoint if llm and llm.endpoint else self._anthropic_api_url
            provider_label = "Anthropic"

        if not api_key:
            if provider in _OPENAI_PROVIDERS:
                msg = (
                    "OpenAI API key not configured (set OPENAI_API_KEY, or enter a key "
                    "in the Config LLM panel)"
                )
            else:
                msg = (
                    "Anthropic API key not configured (set ANTHROPIC_API_KEY, or enter "
                    "a key in the Config LLM panel)"
                )
            logger.warning(msg)
            return RCAResult(performed=False, error=msg)

        prompt = self._build_prompt(result)
        try:
            if provider in _OPENAI_PROVIDERS:
                raw = await self._call_openai(prompt, api_key=api_key, model=model, url=url)
            else:
                raw = await self._call_llm(prompt, api_key=api_key, model=model, url=url)
            rca = self._parse_response(raw)
            rca.performed = True
            return rca
        except Exception as exc:
            logger.error("%s RCA analysis failed: %s", provider_label, exc)
            return RCAResult(performed=False, error=str(exc))

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_run(self, result: UnifiedResult) -> bool:
        """
        Return True if there are signals worth investigating.

        Triggers on any of:
        - Error or critical log lines
        - A correlation event with severity "error"
        - At least one error span in the traces
        - A latency metric or trace above the RCA threshold
        - A suspicious telemetry absence event

        The latency check lets RCA fire on slow-but-not-failing scenarios
        (e.g. a missing DB index) where no HTTP errors are produced.
        """
        return should_run_rca(result)

    def _build_prompt(self, result: UnifiedResult) -> str:
        """
        Construct the prompt sent to the fallback LLM.

        The prompt has two parts:
          - A structured data section (signals in a readable format)
          - A task instruction asking for JSON output

        Keeping it structured means the model can reference specific
        numbers and timestamps rather than hallucinating them.
        """
        m = result.meta
        window = (
            f"{m.window_start.strftime('%Y-%m-%dT%H:%M:%SZ')} → "
            f"{m.window_end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )
        lines: list[str] = [
            "# Kubernetes incident investigation",
            f"Target: {m.target}  Namespace: {m.namespace}",
            f"Window: {window}",
            "",
        ]

        lines.append("## Signal health")
        lines.append(
            "- metrics: "
            + (
                f"unavailable ({result.metrics.error})"
                if result.metrics.error
                else f"{len(result.metrics.series)} series"
            )
        )
        if result.logs.error:
            log_health = f"unavailable ({result.logs.error})"
        else:
            log_health = (
                f"{result.logs.total_lines} lines, {result.logs.error_count} errors, "
                f"{result.logs.warn_count} warnings"
            )
        lines.append(f"- logs: {log_health}")
        if result.traces.error:
            trace_health = f"unavailable ({result.traces.error})"
        else:
            trace_health = (
                f"{len(result.traces.traces)} traces, "
                f"{result.traces.error_trace_count} error traces"
            )
        lines.append(f"- traces: {trace_health}")
        lines.append("")

        # Correlation events (most important — pre-processed signal)
        if result.correlations:
            lines.append("## Detected correlations")
            for ev in result.correlations:
                lines.append(f"- [{ev.severity.upper()}] {ev.kind}: {ev.description}")
            lines.append("")

        telemetry_gaps = [
            ev for ev in result.correlations if is_suspicious_absence_event(ev)
        ]
        if telemetry_gaps:
            lines.append("## Telemetry gaps")
            for ev in telemetry_gaps:
                lines.append(f"- [{ev.severity.upper()}] {ev.kind}: {ev.description}")
            lines.append(
                "Treat missing telemetry as uncertainty, not proof that the service is "
                "healthy. Distinguish an application failure from an observability blind "
                "spot, and lower confidence when evidence is partial."
            )
            lines.append("")

        # Metric anomalies
        if result.metrics.series:
            lines.append("## Metric anomalies")
            for s in result.metrics.series[:MAX_METRIC_SERIES]:
                latest = s.latest_value
                peak = s.peak_value
                if latest is not None or peak is not None:
                    lines.append(
                        f"- {s.name}: latest={_fmt(latest)} peak={_fmt(peak)}"
                        + (f"  labels={s.labels}" if s.labels else "")
                    )
            lines.append("")

        # Error and warning log samples — both levels matter:
        # ERROR/CRITICAL contain the root failure; WARNING often captures downstream impact
        error_lines = [
            log for log in result.logs.lines
            if log.severity in (Severity.ERROR, Severity.CRITICAL, Severity.WARN)
        ]
        if error_lines:
            lines.append(f"## Error and warning log samples ({len(error_lines)} total)")
            for log in error_lines[-MAX_LOG_SAMPLES:]:
                ts = log.timestamp.strftime("%H:%M:%S")
                lines.append(f"  [{ts}] [{log.severity.value}] {log.message[:200]}")
            lines.append("")

        # Slow / error spans from Jaeger
        error_spans: list[Span] = []
        for trace in result.traces.traces:
            error_spans.extend(s for s in trace.spans if s.is_error)
        if error_spans:
            lines.append(f"## Error trace spans ({len(error_spans)} total)")
            for span in error_spans[:MAX_TRACE_SPANS]:
                lines.append(
                    f"  - {span.service_name} → {span.operation_name}"
                    f"  duration={span.duration_ms:.0f}ms"
                    + (f"  tags={dict(list(span.tags.items())[:4])}" if span.tags else "")
                )
            lines.append("")

        repo_context = (
            f"\nThe codebase lives in the GitHub repository: {self._repo}"
            if self._repo
            else "\nThe codebase GitHub repository is not specified."
        )

        lines.append(repo_context)
        lines.append("")
        lines.append(
            "For log_evidence, include only log messages that appear in the log samples "
            "above. Do not invent or paraphrase log lines; use an empty array if no log "
            "line directly supports the conclusion."
        )
        lines.append(
            "When telemetry is unavailable, empty, or suspiciously absent, say so "
            "explicitly. Do not treat missing logs, metrics, or traces as proof of health; "
            "frame conclusions as lower confidence when observability evidence is partial."
        )
        lines.append(
            "For supporting_evidence, write human-readable evidence, not raw tool or metric "
            "dumps. Do not output prefixes like get_metrics:, get_logs:, get_traces:, or "
            "get_correlations:, and do not expose internal fields like latest_value=, "
            "peak_value=, sample_count=, total_lines=, or error_trace_count=. Each item "
            "should be one concise claim, optionally followed by a second explanatory "
            "sentence after ' — '. Example: "
            '"http_error_rate for /crash peaked at 0.1404 req/s — 100% of requests to '
            'that handler failed".'
        )
        lines.append("")
        lines.append(
            "Based on the signals above, perform a root cause analysis. "
            "Respond ONLY with a valid JSON object — no markdown fences, "
            "no preamble, nothing outside the JSON. Use this exact schema:\n"
        )
        lines.append("""{
  "summary": "<one sentence plain-English description of what went wrong>",
  "root_cause": "<detailed technical explanation of the root cause>",
  "confidence": <float 0.0–1.0>,
  "supporting_evidence": [
    "<plain-English evidence claim — optional short explanation>",
    ...
  ],
  "log_evidence": [
    {
      "timestamp": "<ISO timestamp or null>",
      "severity": "<level>",
      "message": "<exact provided log excerpt>",
      "relevance": "<why this log matters>",
      "labels": {"<key>": "<value>"}
    },
    ...
  ],
  "recommended_actions": [
    {
      "priority": <1|2|3>,
      "action": "<what to do>",
      "rationale": "<why this helps>"
    },
    ...
  ],
  "github_search_terms": [
    "<extract function names, method names, exception class names, and exact custom error strings>",
    "<do not use Prometheus metric names or generic HTTP framework patterns>",
    "<good examples: '_process_payment', 'connection pool exhausted', 'FAILURE_RATE'>",
    ...
  ]
}""")

        return "\n".join(lines)

    async def _call_llm(self, prompt: str, *, api_key: str, model: str, url: str) -> str:
        """Backward-compatible alias for tests and older callers."""
        return await self._call_anthropic(prompt, api_key=api_key, model=model, url=url)

    async def _call_anthropic(self, prompt: str, *, api_key: str, model: str, url: str) -> str:
        """Send the prompt to the Anthropic Messages API and return the text response."""
        resp = await self._client.post(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 2048,
                "messages": [{"role": "user", "content": prompt}],
            },
        )

        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic API {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        return data["content"][0]["text"]

    async def _call_openai(self, prompt: str, *, api_key: str, model: str, url: str) -> str:
        """Send the prompt to the OpenAI Responses API and return the text response."""
        resp = await self._client.post(
            url,
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "input": prompt,
                "max_output_tokens": 2048,
                "text": {"format": {"type": "json_object"}},
            },
        )

        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI API {resp.status_code}: {resp.text[:300]}")

        return _extract_openai_text(resp.json())

    def _parse_response(self, raw: str) -> RCAResult:
        """
        Parse the LLM's JSON response into an RCAResult.

        Strips any accidental markdown fences before parsing,
        and is tolerant of extra/missing fields.
        """
        # Strip ```json ... ``` if the model adds them anyway
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned invalid JSON: {exc}\n---\n{raw[:500]}") from exc

        actions = [
            RecommendedAction(
                priority=max(1, min(3, int(a.get("priority", 2)))),
                action=a.get("action", ""),
                rationale=a.get("rationale", ""),
            )
            for a in data.get("recommended_actions", [])
        ]

        rca_data: dict[str, Any] = {
            "summary": data.get("summary", ""),
            "root_cause": data.get("root_cause", ""),
            "confidence": float(data.get("confidence", 0.5)),
            "supporting_evidence": data.get("supporting_evidence", []),
            "recommended_actions": actions,
            "github_search_terms": data.get("github_search_terms", []),
        }
        if "log_evidence" in data:
            rca_data["log_evidence"] = _parse_log_evidence(data["log_evidence"])

        return RCAResult(**rca_data)


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


def _normalize_provider(provider: str | None) -> str:
    return (provider or "anthropic").strip().lower()


def _extract_openai_text(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    parts: list[str] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)

    text = "".join(parts).strip()
    if text:
        return text
    raise ValueError(f"Unexpected OpenAI response shape: {data}")


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------
# Stack trace parser — extracts file:line references from log text
# ------------------------------------------------------------------

# Common stack trace patterns across Python, Java, Go, Node.js
_STACK_PATTERNS = [
    # Python:  File "path/to/file.py", line 42, in function_name
    re.compile(r'File "([^"]+\.py)",\s*line\s*(\d+)'),
    # Java:    at com.example.ClassName.method(FileName.java:42)
    re.compile(r'at\s+[\w.$]+\((\w+\.java):(\d+)\)'),
    # Go:      /path/to/file.go:42
    re.compile(r'([\w/.-]+\.go):(\d+)'),
    # Node.js: at Object.<anonymous> (/path/to/file.js:42:10)
    re.compile(r'\(([^)]+\.(?:js|ts)):(\d+):\d+\)'),
]


def extract_stack_frames(log_lines: list[LogLine]) -> list[tuple[str, int | None]]:
    """
    Scan log lines for stack trace file references.
    Returns a deduplicated list of (filepath, line_number_or_None) tuples.
    """
    seen: set[str] = set()
    results: list[tuple[str, int | None]] = []

    for line in log_lines:
        for pattern in _STACK_PATTERNS:
            for match in pattern.finditer(line.message):
                filepath = match.group(1)
                try:
                    lineno: int | None = int(match.group(2))
                except (IndexError, ValueError):
                    lineno = None

                key = f"{filepath}:{lineno}"
                if key not in seen:
                    seen.add(key)
                    results.append((filepath, lineno))

    return results


def _fmt(v: float | None) -> str:
    return f"{v:.4g}" if v is not None else "—"
