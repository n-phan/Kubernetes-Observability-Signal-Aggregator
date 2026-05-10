# K8s Observability Signal Aggregator

Queries Prometheus (metrics), Loki (logs), and Jaeger (traces) in parallel, correlates the
results, and runs an AI-powered root cause analysis using the Anthropic API — all behind a
single web UI.

The core problem this solves: during an incident, engineers typically have to open three
separate dashboards, manually copy timestamps between them, and try to connect the dots
across tools. This project pulls all three signals into one view, adds a rule engine that
flags cross-signal patterns, and uses Claude to summarize what went wrong.

It runs two ways:

- **Local Docker stack** — `docker compose up` brings up the aggregator, the UI, the three
  observability backends, `node-exporter`, and four demo microservices. Good for trying it
  out and for development.
- **Against a real Kubernetes cluster** — point it at an in-cluster Prometheus/Loki/Jaeger
  (e.g. `kube-prometheus-stack` + `loki-stack`). Then the namespace selector, the per-node
  Cluster Status panel, and per-pod status all become meaningful. See
  [Deploying to Kubernetes](#deploying-to-kubernetes).

---

## How it works

A query for a target service fans out concurrently to Prometheus, Loki, and Jaeger. The
results are correlated by a rule engine that detects cross-signal patterns (e.g. an HTTP
error spike that coincides with a log burst). Optionally, the correlated signals are sent
to an LLM for root cause analysis. Notable queries are recorded so a recurring failure mode
can be distinguished from a brand-new one ("has this happened before?").

RCA can also run through an optional Hermes OpenAI-compatible agent. Recent RCA updates add
structured log evidence, suspicious telemetry-gap events, and a scoped follow-up chat after
analysis completes.

Four demo microservices generate realistic signals. `service-a` and `service-b` are always
present: `service-b` is intentionally flaky, with failure modes controllable via the
in-browser **⚙ Demo** panel; `service-a` calls `service-b` and shows how downstream failures
propagate (or get absorbed by retry / circuit breaker). `service-c` (payment processor) and
`service-d` (inventory service) demonstrate multi-frame Python tracebacks that RCA can link
to GitHub source lines. Each service can be registered / deregistered at runtime via the
sidebar's **Service → Manage** panel.

When a service is registered, its GitHub repository can optionally be recorded in
`infra/service-registry.yml` so RCA results include direct links to the relevant source
lines. See [`infra/README.md`](infra/README.md) for the registry format.

For the aggregator's internal architecture and request lifecycle, see
[`aggregator/README.md`](aggregator/README.md). For the infrastructure layer (Prometheus,
Loki, Promtail, Jaeger, node-exporter), see [`infra/README.md`](infra/README.md).

---

## The web UI

Open **http://localhost:8081**.

- **Left sidebar** — collapsible navigation rail:
  - **Service** — lists the registered services; click one to pick it as the query target.
    **+ Manage services…** opens the add/edit/remove panel.
  - **History** — a browsable log of recent (notable) queries.
  - **Setting** — **API Endpoint & Namespace** (which aggregator to talk to and which K8s
    namespace to scope to; **Apply** persists them in your browser) and **Config LLM** (pick
    a provider — ChatGPT / Gemini / Claude / Ollama / Custom — and enter endpoint / model /
    key; sent with each "Analyze with AI" request; only the Anthropic provider is wired up
    server-side for now).
- **Cluster Status** panel — CPU / memory / load / per-disk usage gauges and network
  throughput for the host (from `node-exporter`). When a target is selected and the backend
  is a real cluster, it also lists that service's pods (node, phase, restarts, waiting
  reasons, via `kube-state-metrics`).
- **Query results** — per signal:
  - **Recurrence banner** — "seen N times before" / "first occurrence — new failure mode".
  - **Root Cause Analysis** — the LLM hypothesis, confidence, recommended actions, and
    GitHub code references. Its **supporting evidence** items are clickable: clicking one
    scrolls to (and flashes) the matching Metrics / Logs / Traces row that backs it.
  - **Correlations** — cross-signal events the rule engine detected.
  - **Metrics** — a table of series, each with an inline sparkline.
  - **Logs / Traces** — paginated, filterable; multi-line tracebacks merged into one entry.

---

## Prerequisites

- Docker Desktop (includes `docker compose`)
- An Anthropic API key — required for the "Analyze with AI" button in default RCA mode
  (get one at https://console.anthropic.com)
- A GitHub personal access token — optional, enables code reference links in RCA results
  (get one at https://github.com/settings/tokens, `repo:read` scope sufficient)
- Ports 8001, 8002, 8080, 8081, 9090, 3100, and 16686 free on your machine
- ~4 GB of available RAM

---

## Quick start (local Docker)

### 1. Clone and configure

```bash
git clone <your-repo-url>
cd Kubernetes-Observability-Signal-Aggregator
cp .env.example .env
```

Open `.env` and set:

```
# Enables the "Analyze with AI" button. (Alternatively, leave this blank and
# enter your key in the UI's Setting → Config LLM panel.)
ANTHROPIC_API_KEY=sk-ant-api03-...

# Optional — adds a "Code references" section to RCA results with GitHub links.
# Per-service repos live in infra/service-registry.yml; GITHUB_REPO is the fallback.
GITHUB_TOKEN=github_pat_...
```

# Required — enables the "Analyze with AI" button in default RCA mode
ANTHROPIC_API_KEY=sk-ant-api03-...

# Optional — use Hermes as the RCA agent instead of the one-shot Anthropic path.
# Start a Hermes OpenAI-compatible API server separately, then set:
RCA_MODE=hermes
HERMES_API_URL=http://host.docker.internal:8642/v1
HERMES_API_KEY=
HERMES_MODEL=hermes-agent
HERMES_TOOLS_ENABLED=true
HERMES_INVESTIGATION_MODE=tools_first
HERMES_TIMEOUT_SECONDS=90
HERMES_MAX_TOOL_ROUNDS=4
HERMES_MAX_TOOL_CALLS=8
HERMES_TOOL_LOOKBACK_MAX_MINUTES=120
HERMES_MODEL=hermes-agent
HERMES_TOOLS_ENABLED=true
HERMES_INVESTIGATION_MODE=tools_first

# Optional — adds a "Code references" section to RCA results with GitHub links
# The GITHUB_REPO is pre-configured for this repository (n-phan/k8s-obs-aggregator-final).
# If you fork this project to a different repo, update GITHUB_REPO to your fork.
# You'll also need a GitHub personal access token (repo:read scope is sufficient).
GITHUB_TOKEN=github_pat_...
```

`ANTHROPIC_API_KEY` is the only setting that must be filled in for the default RCA mode.
To use Hermes, run a Hermes OpenAI-compatible API server and set `RCA_MODE=hermes`; Hermes
first calls the aggregator overview tool, then can call read-only metrics, logs, traces,
and correlation tools for drill-down evidence. If Hermes is unavailable, the aggregator
falls back to the default one-shot RCA when `ANTHROPIC_API_KEY` is configured. The GitHub
integration is purely optional — RCA produces a full analysis (summary, root cause,
recommended actions) without it.
`ANTHROPIC_API_KEY` is the only setting that must be filled in for the default RCA mode. To use Hermes, run a Hermes OpenAI-compatible API server and set `RCA_MODE=hermes`; Hermes first calls the aggregator overview tool, then can call read-only metrics, logs, traces, and correlation tools for drill-down evidence. If Hermes is unavailable, the aggregator falls back to the default one-shot RCA when `ANTHROPIC_API_KEY` is configured. The GitHub integration is purely optional — RCA produces a full analysis (summary, root cause, recommended actions) without it.

### 2. Start the stack

```bash
docker compose up -d --build
```

The first run pulls images and builds the Python services — a few minutes. Subsequent
starts are faster.

### 3. Open the web UI

**http://localhost:8081**. Pick `service-a` or `service-b` from the sidebar **Service**
list and click **Query**. If the panels are empty, generate some traffic with the **⚙ Demo**
panel first (signals are shown for the last 30 minutes).

### Port conflicts

If `docker compose up` fails with *"ports are not available"* on 8001–8004 (common on
Windows, where `netsh int ipv4 show excludedportrange protocol=tcp` reserves them for
Hyper-V), remap the host side without touching the committed file — create a
`docker-compose.override.yml` (gitignored):

```yaml
services:
  service-a: { ports: !override [ "18001:8001" ] }
  service-b: { ports: !override [ "18002:8002" ] }
  service-c: { ports: !override [ "18003:8003" ] }
  service-d: { ports: !override [ "18004:8004" ] }
```

Container ports are unchanged, so service-to-service traffic and the Demo runner are
unaffected — only the host-side URLs become `localhost:1800x`.

---

## Ports

| Service | URL | Notes |
|---|---|---|
| **Web UI** | **http://localhost:8081** | **Start here** |
| Aggregator API | http://localhost:8080 | REST endpoint |
| Aggregator docs | http://localhost:8080/docs | Auto-generated OpenAPI UI |
| service-a | http://localhost:8001 | Upstream demo API (calls service-b) |
| service-b | http://localhost:8002 | Flaky downstream — all failure injection here |
| service-c | http://localhost:8003 | Payment processor demo |
| service-d | http://localhost:8004 | Inventory service demo |
| Prometheus | http://localhost:9090 | Metrics query explorer |
| Loki | http://localhost:3100 | Log store (no UI — query via the aggregator) |
| Jaeger | http://localhost:16686 | Distributed trace UI |
| node-exporter | — | Host metrics; scraped by Prometheus, no host port |

See [`infra/README.md`](infra/README.md) for what each one does and how they connect.

---

## Demo scenarios

Failure modes are injected and traffic generated through the **⚙ Demo** panel — no shell
scripts needed.

| Scenario | Target | What it demonstrates |
|---|---|---|
| Random 500 errors | service-a / service-b | HTTP error-rate spike, error correlation |
| Latency spike | service-b | High p99 latency, slow-query detection |
| Payment crash | service-b | Unhandled exception with a full Python traceback |
| Gateway timeout | service-c | Three-frame call-chain exception, stack-frame linking in RCA |
| DB connection lost | service-d | Two-frame call-chain exception, custom error counter |
| Resilience comparison | service-a / service-b | Retry / circuit-breaker effect on error propagation |

Each scenario fires a burst of requests, then stops. Pick the target in the sidebar
**Service** list and click **Query** (then **⚡ Analyze with AI**). The Demo panel's **Reset**
button restores all services to their default (no-failure) state.
Each scenario fires a burst of requests, then stops. The demo stream records the exact
`window_start` and `window_end`, and the UI uses that window on the next **Query** so the
result is scoped to the run you just generated. After that query renders, **Analyze with
AI** reuses the same exact window.

---

## Deploying to Kubernetes

`k8s/` contains manifests to run the demo workloads and the aggregator inside a real
cluster; `deploy.sh` automates the whole thing on a single Linux host (k3s).

### One command (k3s)

On the target Linux server, from the repo root:

```bash
bash deploy.sh                                  # installs k3s + Helm + the obs stack + the demo
ANTHROPIC_API_KEY=sk-ant-... bash deploy.sh     # ...and the RCA key (or set it later in the UI)
K3S_EXTRA_SAN=100.x.x.x bash deploy.sh          # ...and an extra SAN for the API-server cert
```

It installs k3s + Helm, `helm install`s `kube-prometheus-stack` (Prometheus + node-exporter
+ kube-state-metrics) and `loki-stack` (Loki + Promtail), imports the demo images into
containerd, and applies `k8s/demo.yaml` (Jaeger + service-a..d) and `k8s/aggregator.yaml`
(the aggregator on NodePort **30080**). It's idempotent. See the header of `deploy.sh` for
how to supply the demo images (build with `docker compose build` + `docker save` elsewhere,
or let the script build them if Docker is present).

### Then

The frontend stays run locally — it's a static site that just calls the aggregator:

```bash
docker compose up -d --no-deps frontend          # http://localhost:8081
```

In the UI: **Setting → API Endpoint & Namespace** → set the endpoint to
`http://<node-ip>:30080` and the namespace to `obs-demo`, click **Apply**, then pick a
service from the sidebar **Service** list and **Query**. Metrics / logs / traces now come
from the cluster, the Cluster Status panel shows the node's real resources, and selecting a
target shows that service's pods.

> Service registration (add/remove) is read-only in Kubernetes mode — Prometheus scraping
> there is managed by `ServiceMonitor`s, not by editing `prometheus.yml`. The Target list is
> populated from a ConfigMap mounted into the aggregator.

---

## RCA follow-up and tools

The **Analyze with AI** button reruns the last query with `include_rca=true`. RCA now runs
for errors, high latency, and suspicious telemetry absence events such as traffic without
matching logs or traces. RCA output can include a structured **Logs** evidence section when
error or warning log lines directly support the conclusion.

When `RCA_MODE=hermes`, Hermes can run in `tools_first` mode: it is instructed to call the
aggregate observability overview first, then use read-only metrics, logs, traces, or
correlation tools only when it needs more evidence. The same read-only tool set is exposed
through the local MCP bridge:

```bash
AGGREGATOR_API_URL=http://localhost:8080 python -m aggregator.mcp_observability_server
```

After a completed RCA, the RCA panel shows a follow-up chat for incident-scoped questions.
Follow-up uses Hermes first and falls back to Anthropic when Hermes is unavailable and
`ANTHROPIC_API_KEY` is configured.

---

## Project structure



---

## Running the tests

Tests use mock HTTP clients, so they run without Docker or any live services.

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                        # -v for verbose, -x to stop on first failure
```

`tests/test_aggregator.py` covers the correlator rules, the aggregator's fan-out
behavior, suspicious absence events, Hermes RCA behavior, RCA follow-up, `TimeWindow`
validation, and the Loki multiline-merging and severity-detection helpers.
`tests/test_rca_scenarios.py` covers the full RCA pipeline across four failure scenarios
using mocked model responses. `tests/test_mcp_observability_server.py` covers the
read-only MCP bridge, and `tests/test_demo.py` covers the demo scenario query-window
events.

---

## Common issues

**Nothing shows up after clicking Query**
Signals are shown for the last 30 minutes — use the **⚙ Demo** panel to generate traffic
first. If the sidebar **Service** list is empty, wait a few seconds and reload (it's
populated from `prometheus.yml` locally, or a ConfigMap in Kubernetes mode).

**"Analyze with AI" shows an error or does nothing**
For default RCA mode, `ANTHROPIC_API_KEY` is missing or invalid in `.env`. For
`RCA_MODE=hermes`, check that `HERMES_API_URL` points at a running Hermes server. Metrics,
logs, and traces still work without it. If analysis runs but the "Code references" section
is missing or shows "No matches found", the GitHub token (`GITHUB_TOKEN`) is not set —
that section is optional. Code references only appear in scenarios that produce Python
stack traces (e.g., the crash scenario).

**Follow-up chat fails**
Follow-up requires a completed RCA first. Hermes is the primary follow-up provider; if it is
unavailable, Anthropic fallback only works when `ANTHROPIC_API_KEY` is configured.

**`port is already allocated` / `ports are not available`**
Another process holds one of the ports, or (on Windows) it's in a reserved range — see
[Port conflicts](#port-conflicts).

**`aggregator` container shows `unhealthy`**
Its health-check uses `wget`, which isn't in the `python:3.11-slim` image — cosmetic; the
API works fine (`curl http://localhost:8080/health`).

**Stack is slow or containers keep restarting**
Nine containers run simultaneously. On machines with less than 8 GB of RAM this can cause
memory pressure — try closing other applications before starting the stack.
