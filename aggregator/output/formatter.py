"""
Output formatters for the unified result.

- RichFormatter  → terminal tables / panels via the `rich` library
- JsonFormatter  → machine-readable JSON (wraps the Pydantic model)
"""
from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aggregator.models.rca import RCAResult
from aggregator.models.result import CorrelationEvent, UnifiedResult
from aggregator.models.signals import LogLine, MetricSeries, Severity, Trace

console = Console()


def _split_evidence(value: str) -> tuple[str, str]:
    lead, separator, detail = value.partition(" — ")
    if not separator:
        return value, ""
    return lead.strip(), detail.strip()


# ---------------------------------------------------------------------------
# Rich / terminal formatter
# ---------------------------------------------------------------------------


class RichFormatter:
    """Renders a UnifiedResult to the terminal using Rich."""

    def __init__(self, con: Console | None = None) -> None:
        self._con = con or console

    def render(self, result: UnifiedResult) -> None:
        self._header(result)
        if result.rca.performed:
            self._rca(result.rca)
        if result.correlations:
            self._correlations(result.correlations)
        if result.metrics.series:
            self._metrics(result.metrics.series)
        elif result.metrics.error:
            self._con.print(f"[red]Metrics error:[/red] {result.metrics.error}")
        if result.logs.lines:
            self._logs(result.logs.lines)
        elif result.logs.error:
            self._con.print(f"[red]Logs error:[/red] {result.logs.error}")
        if result.traces.traces:
            self._traces(result.traces.traces)
        elif result.traces.error:
            self._con.print(f"[red]Traces error:[/red] {result.traces.error}")
        self._footer(result)

    def _rca(self, rca: RCAResult) -> None:
        if rca.confidence >= 0.8:
            conf_color = "red"
        elif rca.confidence >= 0.5:
            conf_color = "yellow"
        else:
            conf_color = "dim"
        self._con.print(
            Panel(
                f"[bold]{rca.summary}[/bold]\n\n"
                f"{rca.root_cause}\n\n"
                f"[dim]Confidence: [{conf_color}]{rca.confidence:.0%}[/{conf_color}][/dim]",
                title="Root cause analysis",
                title_align="left",
                border_style="red",
                expand=False,
            )
        )

        if rca.supporting_evidence:
            self._con.print("[dim]Supporting evidence:[/dim]")
            for idx, ev in enumerate(rca.supporting_evidence, start=1):
                lead, detail = _split_evidence(ev)
                self._con.print(f"  [dim]{idx}.[/dim] {lead}")
                if detail:
                    self._con.print(f"     [dim]{detail}[/dim]")
            self._con.print()

        if rca.log_evidence:
            t = Table(box=box.SIMPLE_HEAD, title="Log evidence", title_style="bold green")
            t.add_column("Time", width=12)
            t.add_column("Level", width=8)
            t.add_column("Message")
            t.add_column("Relevance", style="dim")
            severity_styles = {
                "error": "red",
                "critical": "bold red",
                "warn": "yellow",
                "warning": "yellow",
                "info": "white",
                "debug": "dim",
            }
            for log in rca.log_evidence[:10]:
                severity = log.severity.upper() if log.severity else "—"
                style = severity_styles.get(log.severity.lower(), "dim")
                timestamp = log.timestamp.strftime("%H:%M:%S") if log.timestamp else "—"
                t.add_row(timestamp, Text(severity, style=style), log.message[:160], log.relevance)
            self._con.print(t)

        if rca.recommended_actions:
            t = Table(box=box.SIMPLE_HEAD, title="Recommended actions", title_style="bold")
            t.add_column("Priority", width=8, justify="center")
            t.add_column("Action")
            t.add_column("Rationale", style="dim")
            priority_labels = {1: "[red]P1[/red]", 2: "[yellow]P2[/yellow]", 3: "[cyan]P3[/cyan]"}
            for a in rca.recommended_actions:
                t.add_row(priority_labels.get(a.priority, str(a.priority)), a.action, a.rationale)
            self._con.print(t)

        if rca.code_references:
            self._con.print("[bold]Code references[/bold]")
            for ref in rca.code_references:
                line_suffix = f"#L{ref.line_number}" if ref.line_number else ""
                self._con.print(
                    f"  [cyan]{ref.path}{line_suffix}[/cyan]  [dim]{ref.relevance}[/dim]\n"
                    f"  [link={ref.url}]{ref.url}[/link]"
                )
                if ref.snippet:
                    self._con.print(f"  [dim]{ref.snippet[:120]}[/dim]")
                self._con.print()

    def _header(self, result: UnifiedResult) -> None:
        m = result.meta
        title = f"[bold]{m.target}[/bold] · {m.namespace}"
        subtitle = (
            f"{m.window_start.strftime('%H:%M:%S')} → {m.window_end.strftime('%H:%M:%S')} UTC  "
            f"({m.total_duration_ms:.0f} ms)"
        )
        self._con.print(Panel(f"{title}\n[dim]{subtitle}[/dim]", expand=False))

    def _correlations(self, events: list[CorrelationEvent]) -> None:
        t = Table(
            title="Correlations",
            box=box.SIMPLE_HEAD,
            title_style="bold yellow",
            show_lines=False,
        )
        t.add_column("Severity", style="bold", width=8)
        t.add_column("Kind", width=30)
        t.add_column("Description")
        t.add_column("Conf.", width=6, justify="right")

        for ev in events:
            severity_style = {"error": "red", "warn": "yellow", "info": "cyan"}.get(
                ev.severity, "white"
            )
            t.add_row(
                Text(ev.severity.upper(), style=severity_style),
                ev.kind,
                ev.description,
                f"{ev.confidence:.0%}",
            )
        self._con.print(t)

    def _metrics(self, series_list: list[MetricSeries]) -> None:
        t = Table(
            title="Metrics",
            box=box.SIMPLE_HEAD,
            title_style="bold cyan",
        )
        t.add_column("Metric")
        t.add_column("Latest", justify="right")
        t.add_column("Peak", justify="right")
        t.add_column("Labels", style="dim")

        for s in series_list:
            latest = f"{s.latest_value:.4g}" if s.latest_value is not None else "—"
            peak = f"{s.peak_value:.4g}" if s.peak_value is not None else "—"
            labels = ", ".join(f"{k}={v}" for k, v in list(s.labels.items())[:3])
            t.add_row(s.name, latest, peak, labels)

        self._con.print(t)

    def _logs(self, lines: list[LogLine], max_lines: int = 30) -> None:
        t = Table(
            title=f"Logs (showing {min(max_lines, len(lines))} of {len(lines)})",
            box=box.SIMPLE_HEAD,
            title_style="bold green",
        )
        t.add_column("Time", width=12)
        t.add_column("Level", width=8)
        t.add_column("Message")

        severity_styles = {
            Severity.ERROR: "red",
            Severity.CRITICAL: "bold red",
            Severity.WARN: "yellow",
            Severity.INFO: "white",
            Severity.DEBUG: "dim",
            Severity.UNKNOWN: "dim",
        }

        for line in lines[-max_lines:]:
            style = severity_styles.get(line.severity, "white")
            t.add_row(
                line.timestamp.strftime("%H:%M:%S"),
                Text(line.severity.upper(), style=style),
                line.message[:120],
            )
        self._con.print(t)

    def _traces(self, traces: list[Trace], max_traces: int = 20) -> None:
        t = Table(
            title=f"Traces (showing {min(max_traces, len(traces))} of {len(traces)})",
            box=box.SIMPLE_HEAD,
            title_style="bold magenta",
        )
        t.add_column("Trace ID", width=16)
        t.add_column("Operation")
        t.add_column("Service")
        t.add_column("Duration", justify="right")
        t.add_column("Spans", justify="right")
        t.add_column("Error", justify="center")

        for trace in traces[:max_traces]:
            root = trace.root_span
            error_text = Text("✗", style="red") if trace.has_errors else Text("·", style="dim")
            t.add_row(
                trace.trace_id[:16],
                root.operation_name if root else "—",
                root.service_name if root else "—",
                f"{trace.duration_ms:.1f} ms",
                str(len(trace.spans)),
                error_text,
            )
        self._con.print(t)

    def _footer(self, result: UnifiedResult) -> None:
        self._con.print(f"\n[dim]Summary: {result.summary_line}[/dim]")


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


class JsonFormatter:
    """Serialises a UnifiedResult to a JSON string."""

    def render(self, result: UnifiedResult, indent: int = 2) -> str:
        return result.model_dump_json(indent=indent)
