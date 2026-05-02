"""
CLI entrypoint.

Usage:
    obs query my-api --namespace production --lookback 60
    obs query my-api --start 2024-01-01T10:00:00 --end 2024-01-01T11:00:00
    obs query my-api --json
"""
import asyncio
import sys
from typing import Annotated

import typer
from rich.console import Console

from aggregator.core.aggregator import SignalAggregator
from aggregator.models.query import QueryRequest
from aggregator.output.formatter import JsonFormatter, RichFormatter

app = typer.Typer(
    name="obs",
    help="K8s Observability Signal Aggregator — unified metrics, logs, and traces.",
    no_args_is_help=True,
)
console = Console(stderr=True)


@app.command()
def query(
    target: Annotated[str, typer.Argument(help="Pod name, deployment name, or service name")],
    namespace: Annotated[str, typer.Option("--namespace", "-n", help="Kubernetes namespace")] = "default",
    lookback: Annotated[
        int | None,
        typer.Option("--lookback", "-l", help="Lookback in minutes (default: 30)"),
    ] = None,
    start: Annotated[str | None, typer.Option(help="Start time (ISO 8601)")] = None,
    end: Annotated[str | None, typer.Option(help="End time (ISO 8601)")] = None,
    no_metrics: Annotated[bool, typer.Option("--no-metrics", help="Skip Prometheus")] = False,
    no_logs: Annotated[bool, typer.Option("--no-logs", help="Skip Loki")] = False,
    no_traces: Annotated[bool, typer.Option("--no-traces", help="Skip Jaeger")] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Output raw JSON")] = False,
) -> None:
    """Query all observability backends and display a unified view."""
    try:
        request = QueryRequest(
            target=target,
            namespace=namespace,
            lookback_minutes=lookback,
            start=start,
            end=end,
            include_metrics=not no_metrics,
            include_logs=not no_logs,
            include_traces=not no_traces,
        )
    except Exception as exc:
        console.print(f"[red]Invalid query:[/red] {exc}")
        raise typer.Exit(1) from exc

    async def _run() -> None:
        async with SignalAggregator() as agg:
            result = await agg.query(request)

        if output_json:
            print(JsonFormatter().render(result))
        else:
            RichFormatter().render(result)

    asyncio.run(_run())


@app.command()
def version() -> None:
    """Print version and backend connection info."""
    from aggregator.config import settings
    console = Console()
    console.print("[bold]k8s-obs-aggregator[/bold] v0.1.0")
    console.print(f"  Prometheus : {settings.prometheus_url}")
    console.print(f"  Loki       : {settings.loki_url}")
    console.print(f"  Jaeger     : {settings.jaeger_url}")


if __name__ == "__main__":
    app()
