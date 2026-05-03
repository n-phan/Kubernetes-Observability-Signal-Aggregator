# K8s Observability Signal Aggregator

A CLI and REST API that queries Prometheus, Loki, and Jaeger in parallel, correlates the
results, and runs an LLM-powered root cause analysis that links directly to relevant source
code in GitHub.

Built for a distributed systems course project. The core problem it solves: during an
incident, an engineer has to open three separate dashboards (metrics, logs, traces), copy
timestamps between them, and try to mentally correlate what they're seeing. This tool
does that in one command.

---

## How we structured the codebase

The code is split into three layers that depend on each other in one direction only —
models → clients → core — so each can be tested and understood independently.

```
aggregator/
├── models/         Data shapes shared by everything else
│   ├── query.py    QueryRequest — the input (pod name + time window)
│   ├── signals.py  MetricsSignal, LogsSignal, TracesSignal — one per backend
│   ├── result.py   UnifiedResult — the top-level output
│   └── rca.py      RCAResult, CodeReference, RecommendedAction
│
├── clients/        One file per external system
│   ├── base.py     Shared HTTP client (retry, timeout, error wrapping)
│   ├── prometheus.py
│   ├── loki.py
│   ├── jaeger.py
│   └── github.py   Stack trace linker + GitHub code search
│
├── core/           Business logic — depends on models and clients
│   ├── aggregator.py   asyncio.gather fan-out, partial-failure handling
│   ├── correlator.py   Rule-based cross-signal pattern detection
│   └── rca_analyzer.py LLM root cause analysis + stack frame parser
│
└── output/
    └── formatter.py    Rich terminal renderer + JSON serialiser
```

The demo microservices used for local testing live in `demo/`:

```
demo/
├── service-a/      Upstream API (port 8001) — calls service-b, has retry + circuit breaker
└── service-b/      Downstream service (port 8002) — intentionally flaky, configurable
```

---

## Architecture

```
Input: CLI / REST API
          │
          ▼
    QueryRequest  (pod name + time window)
          │
          ├── PrometheusClient ── PromQL range query
          ├── LokiClient       ── LogQL label stream    ← parallel via asyncio.gather
          └── JaegerClient     ── Jaeger trace search
          │
          ▼
    SignalAggregator
          │
          ├── Correlator     rule-based cross-signal events
          └── RCAAnalyzer    LLM hypothesis (Anthropic API)
                │
                └── GitHubLinker  stack trace links + code search
          │
          ▼
    UnifiedResult  (metrics + logs + traces + correlations + rca)
          │
          ├── RichFormatter  (terminal table, clickable GitHub links)
          └── JsonFormatter  (--json flag / REST response body)
```

---

## Quick start

**Requirements:** Docker Desktop, Python 3.11+, ports 8001, 8002, 8080, 9090, 3100, and
16686 available.

### 1. Install

```bash
git clone <your-repo-url>
cd k8s-obs-aggregator

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
cp .env.example .env
```

### 2. Start everything

```bash
docker compose up -d --build
```

This starts Prometheus, Loki, Jaeger, the aggregator API, and both demo services.
First run pulls Docker images and may take a few minutes.

Check that everything is up:

```bash
curl http://localhost:8001/health    # service-a
curl http://localhost:8002/health    # service-b
curl http://localhost:8080/health    # aggregator
```

### 3. Run a query

```bash
# Basic lookback against one of the demo services
obs query service-a

# Choose a time window and namespace
obs query service-a --lookback 60 --namespace default

# Skip a backend you don't have running
obs query service-a --no-traces

# Get JSON output (good for piping to jq)
obs query service-a --json | jq '.rca'
obs query service-a --json | jq '.correlations'
```

### 4. Frontend Web UI

Open http://localhost:8081 in your browser and use the dashboard to query the aggregator.
The UI handles all the state management — pagination, filtering, RCA triggering — and
renders results as you type.

### 5. REST API (optional)

For scripting or integration with other tools, the REST API is also available:

```bash
# Start the dev server (not needed if using docker compose)
uvicorn aggregator.main:app --reload --port 8080

# Full query
curl -s -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"target": "service-a", "namespace": "default", "lookback_minutes": 30}' \
  | jq '.'
```

---

## Frontend Dashboard

The web UI is the recommended way to explore results. It's a single-page app served by
nginx on **http://localhost:8081** and communicates with the aggregator API on port 8080.

### Features

- **Live query builder** — pick a service, namespace, and time window
- **Structured result panels** — metrics table, paginated logs with search & filtering,
  paginated traces with error highlighting, and RCA summary
- **RCA integration** — click "Analyze with AI" to run the LLM on demand (or use mock data)
- **Pagination & filtering** — navigate large result sets with prev/next buttons, search
  logs, toggle errors-only mode, jump to specific log lines or pages
- **Mock data** — click the "⊡ Mock" button to load a pre-built demo scenario without
  hitting the API
- **Status indicator** — coloured dot in the header shows query state: idle (grey), loading
  (amber pulse), ok (green), error (red), or mock (amber solid)

### Architecture

The frontend is split into focused modules (no build step or bundler needed):

```
frontend/
├── index.html                Shell — semantic HTML + script tags only
├── css/
│   └── styles.css           Dark terminal aesthetic (IBM Plex Mono, ~720 lines)
└── js/
    ├── config.js            Page size constants + MOCK_DATA (~160 lines)
    ├── utils.js             Helpers: $, fmt, fmtTime, escHtml, collapsible (~67 lines)
    ├── render.js            Panel builders: renderMeta, renderRCA, renderMetrics,
    │                        renderLogs, renderTraces (~352 lines)
    ├── filters.js           Filter/pagination state + all navigation functions
    │                        (logPagePrev, logJumpToLine, etc.) (~198 lines)
    └── api.js               runQuery, runAnalyze, runMock, renderResult,
                             event listeners (~181 lines)
```

**Load order matters:** config → utils → render → filters → api. Each module has
one responsibility, so features are easy to locate. The total minified size is ~1.7 KB
JavaScript + ~0.7 KB CSS, enabling fast page loads even on slow connections.

---

## Ports

| Service | URL | Notes |
|---|---|---|
| **Frontend** | **http://localhost:8081** | **Web UI — start here** |
| service-a | http://localhost:8001 | Upstream API — main entry point for demo |
| service-b | http://localhost:8002 | Flaky downstream — toggle failure modes here |
| Aggregator API | http://localhost:8080 | Query endpoint (JSON REST) |
| Aggregator docs | http://localhost:8080/docs | Auto-generated OpenAPI UI |
| Prometheus | http://localhost:9090 | Metrics query explorer |
| Loki | http://localhost:3100 | Log store (no UI — query via aggregator) |
| Jaeger | http://localhost:16686 | Distributed trace UI |

---

## Demo services — triggering failure modes

The demo services are the easiest way to generate real signals for the aggregator to
analyze. Both are configured entirely via environment variables in `docker-compose.yml`.
Changing a variable only requires restarting that one service — no image rebuild needed.

### service-b variables

| Variable | Default | Effect |
|---|---|---|
| `FAILURE_RATE` | `0.0` | Fraction of `/data` requests that return 500 (0.5 = 50%) |
| `LATENCY_MS` | `0` | Milliseconds of extra delay added to every `/data` response |
| `OOM_ENDPOINT` | `false` | Expose `/oom` which leaks 10 MB per call |

### service-a variables

| Variable | Default | Effect |
|---|---|---|
| `ENABLE_RETRY` | `false` | Retry failed service-b calls up to 3× with backoff |
| `ENABLE_CIRCUIT_BREAKER` | `false` | Open circuit after 5 consecutive service-b errors |

### Demo scenario — connection pool exhaustion

```bash
# 1. Turn on failure rate in service-b
#    Edit docker-compose.yml:  FAILURE_RATE: "0.5"
docker compose up -d --build service-b

# 2. Generate traffic through service-a so there are signals to query
for i in $(seq 1 40); do curl -s http://localhost:8001/api/data; done

# 3. Wait ~60 seconds for Prometheus to scrape the error rate

# 4. Query — RCA will fire because error signals are present
obs query service-a --json | jq '.rca.summary'
obs query service-a --json | jq '.rca.recommended_actions'
obs query service-a --json | jq '.rca.code_references[].url'
```

### Demo scenario — cascading latency

```bash
# 1. Add 2 seconds of latency to service-b
#    Edit docker-compose.yml:  LATENCY_MS: "2000"
docker compose up -d --build service-b

# 2. Hit the slow endpoint repeatedly
for i in $(seq 1 20); do curl -s http://localhost:8001/api/slow; done

# 3. Query — look at the traces section and RCA latency analysis
obs query service-a --json | jq '.traces.p99_duration_ms'
obs query service-a --json | jq '.rca.root_cause'
```

### Demo scenario — circuit breaker comparison

```bash
# Step 1: service-b at 80% failure, no circuit breaker
#   FAILURE_RATE: "0.8"  ENABLE_CIRCUIT_BREAKER: false (in docker-compose.yml)
docker compose up -d --build service-a service-b
for i in $(seq 1 30); do curl -s http://localhost:8001/api/data; done
obs query service-a --json | jq '.rca.summary'

# Step 2: enable circuit breaker and observe the difference
#   ENABLE_CIRCUIT_BREAKER: "true"
docker compose up -d --build service-a
for i in $(seq 1 30); do curl -s http://localhost:8001/api/data; done
# Errors drop quickly — circuit opens and fails fast instead of fanning into service-b
obs query service-a --json | jq '.correlations'
```

---

## Configuration

All settings come from environment variables or `.env`.

| Variable | Default | Description |
|---|---|---|
| `PROMETHEUS_URL` | `http://localhost:9090` | Prometheus base URL |
| `LOKI_URL` | `http://localhost:3100` | Loki base URL |
| `JAEGER_URL` | `http://localhost:16686` | Jaeger query API base URL |
| `DEFAULT_LOOKBACK_MINUTES` | `30` | Window when none is specified |
| `MAX_LOG_LINES` | `500` | Cap on log lines per query |
| `MAX_TRACES` | `50` | Cap on traces per query |
| `HTTP_TIMEOUT_SECONDS` | `30` | Per-backend request timeout |
| `ANTHROPIC_API_KEY` | _(none)_ | Required for RCA |
| `RCA_ENABLED` | `true` | Set `false` to disable RCA entirely |
| `GITHUB_TOKEN` | _(none)_ | GitHub PAT — needed for code search |
| `GITHUB_REPO` | _(none)_ | `owner/repo` to search and link against |
| `GITHUB_DEFAULT_BRANCH` | `main` | Branch used for blob URLs |

RCA is silently skipped if `ANTHROPIC_API_KEY` is absent. GitHub linking is skipped if
`GITHUB_REPO` is absent. All other features still work normally.

### Setting up RCA

Get an Anthropic API key at https://console.anthropic.com and add to `.env`:

```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

Get a GitHub personal access token (Settings → Developer settings → Fine-grained tokens,
`Contents: Read` scope) and add:

```
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=your-org/your-repo
```

---

## Testing

### Unit tests (no running services needed)

Mock clients replace the real HTTP calls, so these run in under a second regardless of
whether Docker is up.

```bash
pytest                            # run everything
pytest -v                         # verbose — shows each test name
pytest tests/test_aggregator.py   # correlator and aggregator logic
pytest tests/test_rca_scenarios.py  # RCA pipeline, four failure scenarios
pytest -x                         # stop on first failure
```

### What the test files cover

**`tests/test_aggregator.py`** tests the correlator rules (does a 5% error rate produce
an `error_spike` event?) and the aggregator's fan-out and partial-failure behaviour (does
a Prometheus crash still return logs and traces?).

**`tests/test_rca_scenarios.py`** tests the full RCA pipeline across four realistic
failure modes, all with mocked Anthropic and GitHub calls:

| Scenario | Failure mode | Key assertions |
|---|---|---|
| 1 | DB connection pool exhausted | Stack trace in logs → `pool.py` GitHub link, P1 action to increase pool size |
| 2 | Cascading latency (missing DB index) | p99 > 2.5 s, RCA recommends `CREATE INDEX`, skips when no errors present |
| 3 | OOM crash loop | `restart_count > 0` triggers correlator, stack trace links to line 51 in `app.py` |
| 4 | Upstream error propagation | Root cause names service-b, recommends circuit breaker, GitHub link to CB code |

### Testing the RCA pipeline against real error traffic

RCA only activates when there are error signals. To generate them:

```bash
# Edit docker-compose.yml: FAILURE_RATE: "0.5"
docker compose up -d --build service-b
for i in $(seq 1 40); do curl -s http://localhost:8001/api/data; done
sleep 60
obs query service-a --json | jq '.rca'
```

A `performed: true` response means the LLM ran. If `performed` is `false`:

| `error` field value | Meaning |
|---|---|
| `"ANTHROPIC_API_KEY not configured"` | Add the key to `.env` |
| `null` | No error signals — service is healthy, RCA intentionally skipped |
| Any other string | API call failed — check the key or network |

### Spot-checking the GitHub linker

```python
import asyncio
from aggregator.clients.github import GitHubLinker
from aggregator.models.rca import RCAResult
from aggregator.models.signals import LogsSignal

async def test():
    linker = GitHubLinker(token="your-token", repo="your-org/your-repo")
    rca = RCAResult(performed=True, github_search_terms=["connection_pool", "acquire"])
    enriched = await linker.enrich(rca, LogsSignal())
    for ref in enriched.code_references:
        print(ref.path, "→", ref.url)
    await linker.close()

asyncio.run(test())
```

---

## Common issues

**`obs: command not found`**
The virtual environment is not active or the package is not installed. Run
`source .venv/bin/activate` then `pip install -e .`.

**All signal counts are zero**
The target name must match what is in the Kubernetes or Loki labels. Try querying
`service-a` or `service-b` when using the demo stack. Also confirm there was traffic
to the service in the query window.

**`port is already allocated`**
Something else is using one of the required ports. `lsof -i :8002` (or the relevant port)
identifies it.

**Requests fail right after `docker compose up`**
Services need 15–30 seconds to pass their health checks. Run `docker compose ps` and wait
for all services to show `(healthy)`.

**RCA `performed` is always `false` with no error**
The service is producing no error signals. Enable `FAILURE_RATE` in service-b and
generate traffic, then wait ~60 s for Prometheus to scrape the new error rate.

**GitHub search returns no results**
The GitHub search index lags real-time by a few minutes. Also check that `GITHUB_REPO`
matches exactly — it is case-sensitive.

---

## Extending the project

**Add a new PromQL metric:** append to `METRIC_QUERIES` in `aggregator/clients/prometheus.py`.
Each entry is a `(friendly_name, promql_template)` tuple; `{target}` and `{namespace}` are
substituted at query time.

**Add a new correlation rule:** add a `_check_*` or `_cross_correlate_*` method to
`Correlator` in `aggregator/core/correlator.py` and call it from `correlate()`. The
`# ML-HOOK` comments mark where a trained model could replace a threshold comparison.

**Add a new observability backend:** create a client in `aggregator/clients/`, add a signal
model in `aggregator/models/signals.py`, wire it into `SignalAggregator.query()`, and add
a rendering section in `aggregator/output/formatter.py`.

---

## Roadmap

- [ ] `tests/test_github.py` — pytest-httpx mocks for the GitHub search API
- [ ] ML-based anomaly detection (Isolation Forest, Z-score baselines)
- [ ] Relevance ranking of correlation events
- [ ] Kubernetes API integration (pod status, events, resource limits)
- [ ] Grafana dashboard for aggregator query latency