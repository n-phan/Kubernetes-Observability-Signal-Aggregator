# Aggregator

The aggregator is the backend API of this project. It accepts a query for a target service,
fans out to all three observability backends concurrently, correlates the results, and
optionally runs AI root cause analysis. Everything it produces is returned as a single
JSON response to the frontend.

---

## Directory structure

```
aggregator/
├── main.py               FastAPI app — HTTP entry point and route definitions
├── config.py             Settings loader — reads from .env and environment variables
├── cli.py                Typer CLI — runs the aggregator pipeline from the terminal
├── demo.py               Demo runner — SSE streaming endpoint for the in-browser demo panel
│
├── core/
│   ├── aggregator.py     Orchestrator — fans out queries, assembles UnifiedResult
│   ├── correlator.py     Rule engine — detects anomalies and cross-signal relationships
│   └── rca_analyzer.py   LLM interface — builds prompt, calls Anthropic API, parses response
│
├── clients/
│   ├── base.py           Shared HTTP client — retry logic, timeout, error wrapping
│   ├── prometheus.py     Prometheus client — queries metrics via HTTP API
│   ├── loki.py           Loki client — queries logs, parses tracebacks, merges multiline entries
│   ├── jaeger.py         Jaeger client — queries distributed traces
│   └── github.py         GitHub linker — enriches RCA results with code references
│
├── models/
│   ├── signals.py        Data shapes for metrics, logs, and traces
│   ├── result.py         UnifiedResult, QueryMeta, CorrelationEvent
│   ├── rca.py            RCAResult, CodeReference, RecommendedAction
│   └── query.py          QueryRequest — the input shape for POST /query
│
└── output/
    └── formatter.py      CLI output formatter (used when running outside Docker)
```

---

## Request lifecycle

A single `POST /query` request flows through the system like this:

```
POST /query
    │
    ▼
main.py (FastAPI route)
    │  validates QueryRequest via Pydantic
    ▼
SignalAggregator.query()          ← core/aggregator.py
    │
    ├── asyncio.gather(...)        ← all three run concurrently
    │     ├── PrometheusClient.query_metrics()
    │     ├── LokiClient.query_logs()
    │     └── JaegerClient.query_traces()
    │
    ├── Correlator.correlate()     ← rule-based cross-signal analysis
    ├── SuspiciousAbsenceDetector  ← missing telemetry / signal-gap checks
    │
    └── (if include_rca=true)
          ├── RCAAnalyzer.analyze()    ← builds prompt, calls Anthropic API
          └── GitHubLinker.enrich()    ← attaches code references
    │
    ▼
UnifiedResult (JSON response)
```

Individual backend failures are caught and stored as an `error` field on the relevant
signal — they do not abort the overall query. A Prometheus outage still returns logs
and traces.

---

## Components

### `main.py` — HTTP entry point

Defines the FastAPI application and its routes:

**Core**
- `GET /health` — liveness check, used by Docker healthchecks
- `POST /query` — main query endpoint, accepts a `QueryRequest`, returns `UnifiedResult`
- `GET /config` — returns non-sensitive runtime config for debugging

**Service management**
- `GET /services` — lists registered services (reads `prometheus.yml` scrape configs, excluding infrastructure jobs)
- `GET /services/registry` — returns `infra/service-registry.yml` as JSON
- `POST /services/test` — probes a metrics URL and reports reachability
- `POST /services/register` — appends a new scrape target to `prometheus.yml`, triggers a Prometheus hot-reload, and optionally records GitHub metadata in `service-registry.yml`
- `PUT /services/{name}` — updates the GitHub metadata for an existing service in `service-registry.yml`; sending an empty string for a field removes it
- `DELETE /services/{name}` — removes a service from `prometheus.yml`, triggers a reload, and removes its entry from `service-registry.yml`; protected services (`service-a`, `service-b`, and infrastructure) cannot be deleted

Manages a single shared `SignalAggregator` instance via FastAPI's lifespan context, so
HTTP clients are created once at startup and closed cleanly at shutdown.

---

### `config.py` — Settings

A Pydantic `BaseSettings` class that loads configuration from environment variables and
`.env`. All settings have defaults, so the aggregator starts without any `.env` file.

Key settings:

| Setting | Default | What it controls |
|---|---|---|
| `prometheus_url` / `loki_url` / `jaeger_url` | localhost ports | Backend URLs |
| `default_lookback_minutes` | 30 | Time window if not specified in the query |
| `max_log_lines` | 500 | Cap on log lines returned per query |
| `anthropic_api_key` | (empty) | Enables RCA when set |
| `github_repo` | (empty) | Default GitHub repo for code linking; overridden per-service by `service-registry.yml` |
| `github_path_prefix` | (empty) | Default path prefix; overridden per-service by `service-registry.yml` |

The module-level `settings` singleton is imported directly by every other module:
```python
from aggregator.config import settings
```

---

### `core/aggregator.py` — Orchestrator

`SignalAggregator` is the central class. Its `query()` method:

1. Resolves the time window from the request
2. Fans out to all three backends concurrently using `asyncio.gather`
3. Passes results to the `Correlator`
4. Adds suspicious absence events when telemetry is missing or inconsistent
5. Optionally runs RCA and GitHub enrichment
6. Returns the assembled `UnifiedResult`

All client dependencies are injected via the constructor, which makes the class fully
testable — the test suite passes in mock clients directly.

---

### `core/correlator.py` — Rule engine

The `Correlator` takes the three signals and produces a list of `CorrelationEvent`
objects by running a set of threshold-based rules:

**Individual signal checks:**
- `_check_error_rate` — flags HTTP error rate above 1%, with optional z-score spike detection
- `_check_restarts` — flags any container restart within the window
- `_check_high_latency` — flags trace p99 latency above 1000 ms
- `_check_log_error_burst` — flags when more than 5% of log lines are errors

**Cross-signal correlations:**
- `_cross_correlate_errors_and_logs` — surfaces when a metric error spike and log error burst
  co-occur in the same window, indicating the same incident
- `_cross_correlate_latency_and_traces` — surfaces when high p99 latency coincides with
  error traces, pointing to slow error paths worth investigating

Correlation events are sorted by severity before being returned. The code includes
`# ML-HOOK` comments at each decision point marking where rule-based logic could
be replaced or augmented with a trained model.

The aggregator also emits suspicious absence events when missing telemetry is itself
worth investigating. Examples include Prometheus, Loki, or Jaeger being unavailable,
request traffic with zero logs or traces, and logs/traces showing activity while
Prometheus returns no metric series. These events are returned as normal
`CorrelationEvent` objects with kinds such as `traffic_without_traces` and can trigger
low-confidence RCA instead of silently skipping analysis.

---

### `core/rca_analyzer.py` — LLM interface

`RCAAnalyzer` calls the Anthropic API to generate a structured root cause hypothesis.

**`_should_run()`** gates the analysis — RCA only runs when there is something meaningful
to analyze: error log lines, error correlation events, error trace spans, latency
evidence above the incident threshold, or suspicious absence events. Clean queries with
present telemetry still skip RCA, but missing telemetry is treated as uncertainty rather
than proof of service health.

**`_build_prompt()`** assembles the context sent to the model, including:
- Detected correlation events
- Metric anomalies (series name + peak value)
- Error and warning log samples with timestamps and severity level
- Error trace spans with service name, operation, duration, and tags

The model is asked to return a strict JSON object with fields: `summary`, `root_cause`,
`confidence`, `supporting_evidence`, `recommended_actions`, and `github_search_terms`.

**`_parse_response()`** extracts and validates that JSON from the model's reply, handling
cases where the model wraps the JSON in markdown fences.

On any failure (missing API key, rate limit, parse error), `analyze()` catches the
exception and returns `RCAResult(performed=False, error=...)` rather than raising — the
rest of the query result is always returned intact.

---

### `clients/base.py` — Shared HTTP client

`BaseObservabilityClient` is the parent class for all three backend clients. It provides:

- A shared `httpx.AsyncClient` configured with the global timeout from settings
- Automatic retry with up to `http_max_retries` attempts (default: 3)
- Consistent `ObservabilityClientError` wrapping for all HTTP failures
- Query timing, reported back as `query_duration_ms` on each signal

---

### `clients/prometheus.py` — Metrics client

Queries Prometheus for a set of pre-defined metrics relevant to the target service using
the `query_range` API. Returns a `MetricsSignal` containing named `MetricSeries` objects,
each with timestamped samples and computed `peak_value` and `latest_value` fields.

---

### `clients/loki.py` — Log client

Queries Loki for log streams matching the target service. Two selectors are tried in order:
`{job="<target>"}` then `{service=~".*<target>.*"}`, returning on the first that produces results.

Two post-processing steps run on every result:

- **Severity detection** — if Loki labels don't include a severity, the message text is
  inspected for a Python logging level prefix (e.g. `ERROR service-b ...`) and the severity
  is extracted from there.
- **Multiline merging** (`_group_multiline`) — Python tracebacks arrive as separate Loki
  entries, one per line. This step re-joins traceback continuation lines (indented lines,
  `File "..."` references, exception names) back into the preceding ERROR entry, so the
  full stack trace is one `LogLine`. This is what enables stack frame linking in the
  GitHub linker.

---

### `clients/jaeger.py` — Traces client

Queries Jaeger's HTTP API for traces involving the target service within the time window.
Assembles `Trace` and `Span` objects and computes p99 latency across all traces.

A span is flagged as an error if any of the following tags are present:
- `error=true` — legacy Jaeger convention
- `otel.status_code=ERROR` — OpenTelemetry status convention
- `http.status_code >= 500` — HTTP-level error code

---

### `clients/github.py` — GitHub linker

Enriches an `RCAResult` with direct links to source code using two strategies, run in sequence:

**Per-service repository resolution**  
Before either strategy runs, the aggregator reads `infra/service-registry.yml` and looks up
the queried service to determine which GitHub repo, branch, and path prefix to use. This
allows each service to point at a different repository. For example:
- `service-a` and `service-b` live inside the aggregator mono-repo with a `demo/service-*`
  prefix, so their stack traces map to paths like `demo/service-b/app.py`.
- `service-c` and `service-d` each have their own dedicated repo with no prefix, so
  `/app/main.py` maps directly to `main.py` at the repo root.

**Strategy 1 — Stack frame linking** (no API call)  
Parses `File "..."` references out of log messages (placed there by multiline merging in
the Loki client) and constructs direct GitHub blob URLs. The path prefix from the registry
is applied, then the result is turned into a link like
`github.com/owner/repo/blob/main/demo/service-b/app.py#L88`.
This strategy only produces results when logs contain Python tracebacks.

**Strategy 2 — Code search** (GitHub Search API)  
Takes the `github_search_terms` extracted by the LLM and runs up to three of the most
specific terms through the GitHub code search API. Results are deduplicated against
Strategy 1 output. Rate limit errors (HTTP 403) stop the search early rather than failing.

---

### `models/` — Data shapes

All data flowing through the system is typed with Pydantic models:

- **`signals.py`** — `MetricSample`, `MetricSeries`, `MetricsSignal`, `LogLine`,
  `LogsSignal`, `Span`, `Trace`, `TracesSignal` — the raw signal shapes from each backend
- **`result.py`** — `UnifiedResult` (the top-level response), `QueryMeta` (timing and
  window info), `CorrelationEvent` (a single correlation finding)
- **`rca.py`** — `RCAResult` (the full LLM analysis), `CodeReference` (one GitHub link),
  `RecommendedAction` (one prioritized action item)
- **`query.py`** — `QueryRequest` (the POST body), including flags for `include_rca`,
  `include_metrics`, `include_logs`, `include_traces`

---

### `cli.py` — Command-line interface

A [Typer](https://typer.tiangolo.com/) app that exposes the aggregator as a terminal
command for use outside Docker. Useful during development to query a live stack without
opening the browser.

**Commands:**

```bash
# Query a service and display rich terminal output
obs query service-b --namespace default --lookback 60

# Restrict to specific backends
obs query service-b --no-traces --json

# Use an explicit time range instead of a lookback window
obs query service-b --start 2024-01-01T10:00:00 --end 2024-01-01T11:00:00

# Print version and configured backend URLs
obs version
```

The `query` command runs through the full `SignalAggregator` pipeline — the same code
path as `POST /query` — and renders output with `RichFormatter` (tables and panels) or
`JsonFormatter` (raw JSON with `--json`). RCA is not included by default; it must be
triggered from the web UI.

---

### `demo.py` — Demo runner

Provides the SSE (Server-Sent Events) endpoint used by the in-browser demo panel.
`POST /demo/run/{scenario}` streams live progress back to the browser as each request
fires — resetting service-b, configuring the failure mode, and firing the scenario's
requests one by one.
