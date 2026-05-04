"""
Root cause analyzer.

Feeds the unified observability signals into Claude and asks it to:
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

import httpx

from aggregator.models.rca import RCAResult, RecommendedAction
from aggregator.models.result import UnifiedResult
from aggregator.models.signals import LogLine, MetricSeries, Severity, Span

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

# How many items to include in the prompt — keeps token count predictable
MAX_LOG_SAMPLES = 20
MAX_TRACE_SPANS = 10
MAX_METRIC_SERIES = 8

# p99/p95 latency above this threshold (in seconds) triggers RCA even with no hard errors.
# A value above 1 second is considered anomalous for most internal services.
LATENCY_ANOMALY_THRESHOLD_S = 1.0

# Metric name keywords that indicate a latency measurement
_LATENCY_KEYWORDS = ("latency", "duration", "p99", "p95")


class RCAAnalyzer:
    """
    Calls the Anthropic Messages API with a structured summary of
    all observability signals and returns a parsed RCAResult.
    """

    def __init__(self, api_key: str | None = None, repo: str | None = None) -> None:
        """
        api_key  — Anthropic API key (or set ANTHROPIC_API_KEY env var)
        repo     — GitHub repo slug, e.g. "my-org/payment-service"
        """
        self._api_key = api_key
        self._repo = repo
        self._client = httpx.AsyncClient(timeout=60.0)

    async def analyze(self, result: UnifiedResult) -> RCAResult:
        """
        Run root cause analysis on a UnifiedResult.
        Returns RCAResult(performed=False) if there is nothing to analyze
        or if the API call fails.
        """
        if not self._should_run(result):
            return RCAResult(performed=False)

        if not self._api_key:
            logger.warning("ANTHROPIC_API_KEY not set — skipping RCA")
            return RCAResult(
                performed=False,
                error="ANTHROPIC_API_KEY not configured",
            )

        prompt = self._build_prompt(result)
        try:
            raw = await self._call_llm(prompt)
            rca = self._parse_response(raw)
            rca.performed = True
            return rca
        except Exception as exc:
            logger.error("RCA analysis failed: %s", exc)
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
        - A latency metric (p99/p95/duration) above LATENCY_ANOMALY_THRESHOLD_S

        The latency check lets RCA fire on slow-but-not-failing scenarios
        (e.g. a missing DB index) where no HTTP errors are produced.
        """
        has_log_errors = result.logs.error_count > 0
        has_error_correlations = any(
            e.severity == "error" for e in result.correlations
        )
        has_error_traces = result.traces.error_trace_count > 0
        has_latency_anomaly = any(
            any(kw in s.name.lower() for kw in _LATENCY_KEYWORDS)
            and s.peak_value is not None
            and s.peak_value > LATENCY_ANOMALY_THRESHOLD_S
            for s in result.metrics.series
        )
        return has_log_errors or has_error_correlations or has_error_traces or has_latency_anomaly

    def _build_prompt(self, result: UnifiedResult) -> str:
        """
        Construct the prompt sent to Claude.

        The prompt has two parts:
          - A structured data section (signals in a readable format)
          - A task instruction asking for JSON output

        Keeping it structured means Claude can reference specific
        numbers and timestamps rather than hallucinating them.
        """
        m = result.meta
        lines: list[str] = [
            f"# Kubernetes incident investigation",
            f"Target: {m.target}  Namespace: {m.namespace}",
            f"Window: {m.window_start.strftime('%Y-%m-%dT%H:%M:%SZ')} → {m.window_end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "",
        ]

        # Correlation events (most important — pre-processed signal)
        if result.correlations:
            lines.append("## Detected correlations")
            for ev in result.correlations:
                lines.append(f"- [{ev.severity.upper()}] {ev.kind}: {ev.description}")
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
            "Based on the signals above, perform a root cause analysis. "
            "Respond ONLY with a valid JSON object — no markdown fences, "
            "no preamble, nothing outside the JSON. Use this exact schema:\n"
        )
        lines.append("""{
  "summary": "<one sentence plain-English description of what went wrong>",
  "root_cause": "<detailed technical explanation of the root cause>",
  "confidence": <float 0.0–1.0>,
  "supporting_evidence": [
    "<specific signal or data point that supports this conclusion>",
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
    "<look at the error log samples and trace spans above and extract: function names, method names, exception class names, and exact custom error message strings you can see there. These are the only good search terms. Do NOT use Prometheus metric names (e.g. http_request_duration_seconds, http_requests_total, http_error_rate) — they are auto-generated by a library and will return 0 results. Do NOT use generic HTTP framework patterns (e.g. 'raise HTTPException', 'status_code 500'). Good examples based on log content: '_process_payment', 'connection pool exhausted', 'FAILURE_RATE', 'payment_processor.charge', 'slow_query'>",
    ...
  ]
}""")

        return "\n".join(lines)

    async def _call_llm(self, prompt: str) -> str:
        """Send the prompt to the Anthropic API and return the text response."""
        resp = await self._client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": self._api_key or "",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 2048,
                "messages": [{"role": "user", "content": prompt}],
            },
        )

        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic API {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        return data["content"][0]["text"]

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

        return RCAResult(
            summary=data.get("summary", ""),
            root_cause=data.get("root_cause", ""),
            confidence=float(data.get("confidence", 0.5)),
            supporting_evidence=data.get("supporting_evidence", []),
            recommended_actions=actions,
            github_search_terms=data.get("github_search_terms", []),
        )


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