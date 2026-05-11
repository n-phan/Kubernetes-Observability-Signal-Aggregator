import logging
import re
from datetime import datetime

from aggregator.clients.base import BaseObservabilityClient, ObservabilityClientError
from aggregator.config import settings
from aggregator.models.signals import LogLine, LogsSignal, Severity

logger = logging.getLogger(__name__)

# Matches Python log format: "LEVEL name message"
# e.g. "ERROR service-b Unhandled exception..."
_MSG_LEVEL_PREFIX = re.compile(
    r'^(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\s+\S+\s+(.*)',
    re.IGNORECASE | re.DOTALL,
)

# Lines that are part of a Python traceback block
_TRACEBACK_PATTERNS = re.compile(
    r'^('
    r'\s+'                          # indented lines (code context)
    r'|Traceback \(most recent'     # traceback header
    r'|File "'                      # file reference
    r'|\w[\w.]*Error:'              # exception line e.g. ValueError:
    r'|\w[\w.]*Exception:'          # exception line
    r'|\w[\w.]*Warning:'            # warning line
    r'|raise \w'                    # raise statement
    r')'
)

_SEVERITY_MAP = {
    "debug":    Severity.DEBUG,
    "info":     Severity.INFO,
    "warning":  Severity.WARN,
    "warn":     Severity.WARN,
    "error":    Severity.ERROR,
    "critical": Severity.CRITICAL,
    "fatal":    Severity.CRITICAL,
}


def _severity_from_message(message: str) -> tuple[Severity, str]:
    """
    Try to extract severity and the real message body from a log line
    that embeds the level in the text (Python logging %(levelname)s format).

    Returns (severity, cleaned_message). If no level prefix found,
    returns (UNKNOWN, original_message).
    """
    m = _MSG_LEVEL_PREFIX.match(message)
    if m:
        level_str = m.group(1).lower()
        body = m.group(2)
        return _SEVERITY_MAP.get(level_str, Severity.UNKNOWN), body
    return Severity.UNKNOWN, message


def _is_traceback_continuation(message: str) -> bool:
    """Return True if this log line is a continuation of a Python traceback."""
    return bool(_TRACEBACK_PATTERNS.match(message))


def _is_error_initiator(line: LogLine) -> bool:
    """Return True if this line should trigger traceback grouping."""
    return line.severity in (Severity.ERROR, Severity.CRITICAL)


def _group_multiline(lines: list[LogLine]) -> list[LogLine]:
    """
    Merge consecutive traceback lines into the preceding ERROR or CRITICAL entry.

    When Python logs an exception with traceback.format_exc(), each line of
    the traceback arrives in Loki as a separate log entry. This function walks
    the sorted list and appends those continuation lines (indented code lines,
    "File …" references, exception type lines, etc.) onto the message of the
    error entry that triggered them, as long as they arrive within 1 second
    of the initiating entry.

    The result is one LogLine per exception that contains the full traceback,
    which is much easier for both the frontend and the RCA analyzer to read.
    """
    if not lines:
        return lines

    grouped: list[LogLine] = []
    i = 0

    while i < len(lines):
        current = lines[i]

        if _is_error_initiator(current):
            accumulated = [current.message]
            j = i + 1

            while j < len(lines):
                nxt = lines[j]
                time_gap = abs(
                    (nxt.timestamp - current.timestamp).total_seconds()
                )
                if time_gap <= 1.0 and _is_traceback_continuation(nxt.message):
                    accumulated.append(nxt.message)
                    j += 1
                else:
                    break

            if len(accumulated) > 1:
                merged = current.model_copy(
                    update={"message": "\n".join(accumulated)}
                )
                grouped.append(merged)
                i = j
                continue

        grouped.append(current)
        i += 1

    return grouped


class LokiClient(BaseObservabilityClient):
    backend_name = "loki"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or settings.loki_url)

    async def query_logs(
        self,
        target: str,
        namespace: str,
        start: datetime,
        end: datetime,
        limit: int | None = None,
    ) -> LogsSignal:
        """
        Query Loki for log lines matching the target service name.

        Tries several label selectors in order — the first that returns at least
        one line wins; the rest are skipped. This covers both deployment shapes:
          • Kubernetes (Promtail labels pods with namespace / app / pod / container):
              {namespace="<ns>", app="<target>"} , {app="<target>"} , {pod=~"<target>-.+"}
          • docker-compose (Promtail's static "job"/"service" labels):
              {job="<target>"} , {service=~".*<target>.*"}

        If all fail, an empty LogsSignal is returned so callers never handle None.
        Returned lines are sorted chronologically and multiline tracebacks are
        merged into single entries (see _group_multiline).
        """
        limit = limit or settings.max_log_lines

        ns = (namespace or "").strip()
        selectors: list[str] = []
        if ns and ns != "default":
            selectors.append(f'{{namespace="{ns}", app="{target}"}}')
            selectors.append(f'{{namespace="{ns}", pod=~"{target}-.+"}}')
        selectors += [
            f'{{app="{target}"}}',          # Kubernetes Promtail pod-label "app"
            f'{{job="{target}"}}',          # docker-compose Promtail "job"
            f'{{pod=~"{target}-.+"}}',      # Kubernetes pod name prefix
            f'{{service=~".*{target}.*"}}', # legacy broad fallback
        ]

        for selector in selectors:
            try:
                lines, duration_ms = await self._query_range(
                    selector=selector,
                    start=start,
                    end=end,
                    limit=limit,
                )
                if lines:
                    signal = LogsSignal(lines=lines, query_duration_ms=duration_ms)
                    signal.compute_counts()
                    return signal
            except ObservabilityClientError as exc:
                logger.warning("Loki selector '%s' failed: %s", selector, exc)

        return LogsSignal()

    async def _query_range(
        self,
        selector: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> tuple[list[LogLine], float]:
        """Execute a Loki query_range call and parse the response into LogLines."""
        data, duration_ms = await self._get(
            "/loki/api/v1/query_range",
            params={
                "query": selector,
                "start": _to_ns(start),
                "end": _to_ns(end),
                "limit": limit,
                "direction": "backward",
            },
        )

        if data.get("status") != "success":
            raise ObservabilityClientError(
                self.backend_name,
                f"Non-success status: {data.get('status')}",
            )

        lines: list[LogLine] = []
        for stream in data.get("data", {}).get("result", []):
            labels: dict[str, str] = stream.get("stream", {})
            for ts_ns, message in stream.get("values", []):
                if not message.strip():
                    continue
                line = LogLine.from_loki_entry(ts_ns, message, dict(labels))

                # If severity is still UNKNOWN, try extracting it from the
                # message text (Python logging embeds level in the message)
                if line.severity == Severity.UNKNOWN:
                    detected_severity, cleaned_message = _severity_from_message(message)
                    if detected_severity != Severity.UNKNOWN:
                        line = line.model_copy(update={
                            "severity": detected_severity,
                            "message": cleaned_message,
                        })

                lines.append(line)

        # Sort chronologically (Loki returns newest-first by default)
        lines.sort(key=lambda l: l.timestamp)

        # Merge multiline tracebacks into single log entries
        lines = _group_multiline(lines)

        return lines, duration_ms


def _to_ns(dt: datetime) -> str:
    """Convert a datetime to nanosecond epoch string for Loki API."""
    return str(int(dt.timestamp() * 1_000_000_000))