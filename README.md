# K8s Observability Signal Aggregator

A local Docker stack that queries Prometheus (metrics), Loki (logs), and Jaeger (traces)
in parallel, correlates the results, and runs an AI-powered root cause analysis using the
Anthropic API.

The core problem this solves: during an incident, engineers typically have to open three
separate dashboards, manually copy timestamps between them, and try to connect the dots
across tools. This project pulls all three signals into one view and uses Claude to
summarize what went wrong.

---

## How it works

A query for a target service fans out concurrently to Prometheus, Loki, and Jaeger. The
results are correlated by a rule engine that detects cross-signal patterns (e.g. an HTTP
error spike that coincides with a log burst). Optionally, the correlated signals are sent
to the Anthropic API for AI root cause analysis.

Four demo microservices generate realistic signals. `service-a` and `service-b` are always
registered: `service-b` is intentionally flaky, with failure modes controllable via the
in-browser Demo panel. `service-c` (payment processor) and `service-d` (inventory service)
are pre-built but can be registered and deregistered at runtime via the Services panel —
they serve as examples of the self-service registration flow.

When a service is registered, its GitHub repository can optionally be linked in
`infra/service-registry.yml` so that RCA results include direct links to the relevant
source lines. See [`infra/README.md`](infra/README.md) for the registry format.

For a detailed walkthrough of the aggregator's internal architecture and request lifecycle,
see [`aggregator/README.md`](aggregator/README.md). For the infrastructure layer (how
Prometheus, Loki, Promtail, and Jaeger fit together), see [`infra/README.md`](infra/README.md).

---

## Prerequisites

- Docker Desktop (includes `docker compose`)
- An Anthropic API key — required for the "Analyze with AI" button
  (get one at https://console.anthropic.com)
- A GitHub personal access token — optional, enables code reference links in RCA results
  (get one at https://github.com/settings/tokens, `repo:read` scope sufficient)
- Ports 8001, 8002, 8080, 8081, 9090, 3100, and 16686 free on your machine
- ~4 GB of available RAM

---

## Quick start

### 1. Clone and configure

```bash
git clone <your-repo-url>
cd k8s-obs-aggregator
cp .env.example .env
```

Open `.env` and configure your credentials:

```
# Required — enables the "Analyze with AI" button
ANTHROPIC_API_KEY=sk-ant-api03-...

# Optional — adds a "Code references" section to RCA results with GitHub links
# The GITHUB_REPO is pre-configured for this repository (n-phan/k8s-obs-aggregator-final).
# If you fork this project to a different repo, update GITHUB_REPO to your fork.
# You'll also need a GitHub personal access token (repo:read scope is sufficient).
GITHUB_TOKEN=github_pat_...
```

`ANTHROPIC_API_KEY` is the only setting that must be filled in. The GitHub integration is purely optional — RCA produces a full analysis (summary, root cause, recommended actions) without it.

### 2. Start the stack

```bash
docker compose up -d --build
```

The first run pulls Docker images and builds the Python services — this can take a few
minutes. Subsequent starts are faster since images are cached.

### 3. Open the web UI

**http://localhost:8081**

Select `service-a` or `service-b` from the **Target** dropdown and click **Query**. The
metrics, logs, and traces panels should populate. If they're empty, wait 15–30 seconds for
the services to finish starting up and try again.

---

## Ports

| Service | URL | Notes |
|---|---|---|
| **Web UI** | **http://localhost:8081** | **Start here** |
| Aggregator API | http://localhost:8080 | REST endpoint |
| Aggregator docs | http://localhost:8080/docs | Auto-generated OpenAPI UI |
| service-a | http://localhost:8001 | Upstream demo API |
| service-b | http://localhost:8002 | Flaky downstream demo service |
| service-c | http://localhost:8003 | Payment processor demo service |
| service-d | http://localhost:8004 | Inventory service demo service |
| Prometheus | http://localhost:9090 | Metrics query explorer |
| Loki | http://localhost:3100 | Log store (no UI — query via aggregator) |
| Jaeger | http://localhost:16686 | Distributed trace UI |

See [`infra/README.md`](infra/README.md) for details on what each service does and how they connect.

---

## Demo scenarios

Failure modes are injected and traffic is generated through the **⚙ Demo** panel in the
web UI — no shell scripts needed. Click the button in the header to open it.

Available scenarios:

| Scenario | Target service | What it demonstrates |
|---|---|---|
| Random 500 errors | service-a / service-b | HTTP error rate spike, error correlation |
| Latency spike | service-a / service-b | High p99 latency, slow-query detection |
| Payment processor crash | service-c | Multi-frame Python traceback, stack frame linking in RCA |
| Inventory DB failure | service-d | Two-frame call chain exception, custom error counter |
| Resilience comparison | service-a / service-b | Retry and circuit breaker effect on error propagation |

Each scenario fires a burst of requests, then stops. Open the UI, select the target service
in the **Target** dropdown, and click **Query** (or **Analyze with AI**) to see the signals.

The demo panel also has a **Reset** button that restores all services to their default
(no-failure) state.

---

## Project structure

```
k8s-obs-aggregator/
│
├── aggregator/               FastAPI backend — see aggregator/README.md
│
├── demo/
│   ├── service-a/            Upstream API (port 8001) — retry and circuit breaker logic
│   ├── service-b/            Flaky downstream (port 8002) — all failure injection here
│   ├── service-c/            Payment processor (port 8003) — GatewayTimeoutError demo
│   └── service-d/            Inventory service (port 8004) — DatabaseConnectionError demo
│
├── frontend/                 Web UI served by nginx
│   ├── index.html
│   ├── css/styles.css
│   └── js/
│       ├── components/       One self-contained class per UI panel
│       ├── api.js            Calls the aggregator REST API
│       └── config.js         Constants and mock data
│
├── infra/                    Config files for Prometheus, Loki, Promtail, nginx, and
│   │                         service registry — see infra/README.md
│   └── service-registry.yml  Per-service GitHub metadata for RCA code references
│
├── tests/                    Unit tests — run without Docker using mock clients
├── docker-compose.yml        All services and their environment variables
└── .env                      Local secrets (not committed to git)
```

---

## Running the tests

Tests use mock HTTP clients so they run without Docker or any live services.

```bash
# First time setup
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run all tests
pytest

# Verbose output
pytest -v

# Stop on first failure
pytest -x
```

`tests/test_aggregator.py` covers the correlator rules, the aggregator's fan-out
behavior, `TimeWindow` validation, and the Loki multiline-merging and severity-detection
helpers. `tests/test_rca_scenarios.py` covers the full RCA pipeline across four failure
scenarios using mocked Anthropic responses.

---

## Common issues

**Nothing shows up after clicking Query**
The target dropdown is populated from `prometheus.yml`. Use the **⚙ Demo** panel to
generate traffic first — the panels show signals from the last 30 minutes, so there needs
to be recent activity. If the dropdown is empty, wait a few seconds and reload the page.

**"Analyze with AI" shows an error or does nothing**
`ANTHROPIC_API_KEY` is missing or invalid in `.env`. Metrics, logs, and traces still work
without it. If analysis runs but the "Code references" section is missing or shows
"No matches found", the GitHub token (`GITHUB_TOKEN`) is not set — that section is optional.
Code references only appear in scenarios that produce Python stack traces (e.g., the crash scenario).

**`port is already allocated`**
Another process is using one of the required ports. `lsof -i :<port>` identifies it.

**Services not ready right after startup**
Health checks take 15–30 seconds to pass. Run `docker compose ps` and wait until all
services show `healthy` or `running` before querying.

**Stack is slow or containers keep restarting**
Seven containers run simultaneously. On machines with less than 8 GB of RAM this can cause
memory pressure — try closing other applications before starting the stack.