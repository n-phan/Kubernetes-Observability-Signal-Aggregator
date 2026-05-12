# K8s Observability Signal Aggregator

A unified incident investigation stack that queries Prometheus, Loki, and Jaeger in parallel, correlates cross-signal anomalies, and optionally runs AI-powered RCA with follow-up Q&A.

## Features

- Unified timeline generation for incident causality ordering.
- Suspicious telemetry-absence detection (for example traffic without logs/traces).
- RCA follow-up chat after analysis completes.
- Per-request LLM override from the UI Config LLM panel.
- Hermes RCA mode with tools-first investigation flow.
- Runtime service registration and per-service GitHub metadata management.
- GitHub code-link enrichment using stack frames and search terms.
- Environment switching endpoint and panel (local, staging, production).
- Auto-watchdog background monitoring with alert APIs.
- Optional Slack, SNS, SMTP, and Mailgun alert delivery.
- Demo scenarios streamed via Server-Sent Events, including exact query window reuse.

## Architecture at a glance

A single query request does the following:

1. Fan-out to metrics, logs, and traces concurrently.
2. Correlate anomalies and cross-signal patterns.
3. Add suspicious-absence events when missing telemetry is meaningful.
4. Build an incident timeline.
5. Optionally run RCA.
6. Optionally enrich RCA with GitHub code references.
7. Persist notable incidents for recurrence history.

See [aggregator/README.md](aggregator/README.md) for backend internals and [infra/README.md](infra/README.md) for infrastructure wiring.

## UI overview

Open http://localhost:8081.

Main capabilities in the UI:

- Sidebar Service panel
  - Pick target services discovered from Prometheus jobs.
  - Add, update, remove services from the Manage Services flow.
- Sidebar Setting panel
  - Connection panel for API endpoint and namespace.
  - Config LLM panel for request-level model/provider settings.
  - Environment panel for local, staging, production switching.
- Cluster Status panel
  - CPU, memory, load, disk, network, plus per-target pod status when available.
- Query result panels
  - Meta and recurrence summary.
  - RCA panel with confidence, actions, supporting evidence, log evidence, code references, follow-up chat.
  - Correlations panel.
  - Timeline panel.
  - Metrics, Logs, Traces panels.
- Watchdog panel
  - Start/stop continuous monitoring and inspect anomaly alerts.
- Demo panel
  - Run incident scenarios and automatically pin next query to the exact scenario window.

## API surface

Core endpoints:

- GET /health
- POST /query
- GET /config
- POST /rca/followup
- GET /history

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
  - **Root Cause Analysis** — the LLM hypothesis, confidence, recommended actions, GitHub
    code references, and (when log lines support the conclusion) a structured Logs evidence
    section. Its **supporting evidence** items are clickable: clicking one scrolls to (and
    flashes) the matching Metrics / Logs / Traces row that backs it. After RCA completes, a
    **follow-up chat** lets you ask incident-scoped questions (Hermes, falling back to Anthropic).
  - **Correlations** — cross-signal events the rule engine detected, including suspicious
    telemetry gaps (traffic with no matching logs/traces).
  - **Metrics** — a table of series, each with an inline sparkline.
  - **Logs / Traces** — paginated, filterable; multi-line tracebacks merged into one entry.

- GET /services
- POST /services/test
- POST /services/register
- PUT /services/{name}
- DELETE /services/{name}
- GET /services/registry

Demo endpoints:

- GET /demo/config
- POST /demo/reset
- POST /demo/run/{scenario}

Watchdog endpoints:

- POST /api/watchdog
- GET /api/watchdog/alerts
- DELETE /api/watchdog/alerts

Environment endpoints:

- GET /api/environment
- POST /api/environment

Cluster status endpoints:

- GET /cluster/status

## Prerequisites

- Docker Desktop (includes `docker compose`)
- An Anthropic API key — required for the "Analyze with AI" button in the default RCA mode
  (get one at https://console.anthropic.com). You can also enter it in the UI's
  **Config LLM** panel instead of `.env`. (Optionally, use Hermes as the RCA agent
  instead — see [Quick start](#1-clone-and-configure).)
- A GitHub personal access token — optional, enables code-reference links in RCA results
  (`repo:read` scope is enough)
- Free host ports: **8001–8004, 8080, 8081, 9090, 3100, 16686** (see [Ports](#ports); on
  Windows some of these may be in a reserved range — see [Port conflicts](#port-conflicts))
- ~5 GB of available RAM (nine containers)

## Quick start with Docker

1. Create env file:

```bash
cp .env.example .env
```

2. Set at least one RCA key path:

```
# Enables the "Analyze with AI" button in the default RCA mode. (Alternatively,
# leave this blank and enter your key in the UI's Setting → Config LLM panel.)
ANTHROPIC_API_KEY=sk-ant-api03-...

# Optional — use Hermes as the RCA agent instead of the one-shot Anthropic path.
# Start a Hermes OpenAI-compatible API server separately, then set:
RCA_MODE=hermes
HERMES_API_URL=http://host.docker.internal:8642/v1
HERMES_API_KEY=YOUR_API_KEY
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

# Optional — adds a "Code references" section to RCA results with GitHub links.
# Per-service repos live in infra/service-registry.yml; GITHUB_REPO is the fallback.
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

4. Open:

- UI: http://localhost:8081
- API docs: http://localhost:8080/docs

## Ports

| Service | URL | Notes |
|---|---|---|
| Web UI | http://localhost:8081 | Main dashboard |
| Aggregator API | http://localhost:8080 | Query and management API |
| Aggregator docs | http://localhost:8080/docs | OpenAPI UI |
| service-a | http://localhost:8001 | Upstream demo service |
| service-b | http://localhost:8002 | Flaky downstream demo service |
| service-c | http://localhost:8003 | Payment demo service |
| service-d | http://localhost:8004 | Inventory demo service |
| Prometheus | http://localhost:9090 | Metrics backend |
| Loki | http://localhost:3100 | Log backend |
| Jaeger | http://localhost:16686 | Trace backend UI |

### If service-c or service-d build assets are missing in your checkout

Some partial checkouts may not include demo service build files. Start the core runnable stack with:

```bash
docker compose up -d --build service-b service-a prometheus node-exporter loki promtail jaeger aggregator frontend
```

## Configuration reference

Main environment variables (see [.env.example](.env.example)):

Observability backends:

- PROMETHEUS_URL
- LOKI_URL
- JAEGER_URL

Aggregator behavior:

- DEFAULT_LOOKBACK_MINUTES
- MAX_LOG_LINES
- MAX_TRACES

RCA settings:

- ANTHROPIC_API_KEY
- RCA_ENABLED
- RCA_MODE
- HERMES_API_URL
- HERMES_API_KEY
- HERMES_MODEL
- HERMES_TIMEOUT_SECONDS
- HERMES_TOOLS_ENABLED
- HERMES_INVESTIGATION_MODE
- HERMES_MAX_TOOL_ROUNDS
- HERMES_MAX_TOOL_CALLS
- HERMES_TOOL_LOOKBACK_MAX_MINUTES

GitHub enrichment:

- GITHUB_TOKEN
- GITHUB_REPO
- GITHUB_DEFAULT_BRANCH
- GITHUB_PATH_PREFIX

Watchdog and notifications:

- WATCHDOG_ENABLED
- WATCHDOG_INTERVAL_SECONDS
- WATCHDOG_LOOKBACK_MINUTES
- WATCHDOG_ANOMALY_THRESHOLD
- SLACK_WEBHOOK_URL
- SNS_TOPIC_ARN
- SNS_REGION
- SMTP_HOST
- SMTP_PORT
- SMTP_USERNAME
- SMTP_PASSWORD
- SMTP_FROM_EMAIL
- SMTP_USE_STARTTLS
- ALERT_EMAIL
- MAILGUN_DOMAIN
- MAILGUN_API_KEY

Misc:

- ENVIRONMENT
- HISTORY_DB_PATH
- LOG_LEVEL

## Demo scenarios

Built-in scenario IDs available through the Demo panel and /demo/run/{scenario}:

- healthy
- errors
- slow
- crash
- payment_crash
- inventory_crash

Each scenario fires a burst of requests, then stops, and records the exact
`window_start`/`window_end`. Pick the target in the sidebar **Service** list and click
**Query** — the UI scopes the result to that run's window — then **⚡ Analyze with AI**
(which reuses the same window). The Demo panel's **Reset** button restores all services to
their default (no-failure) state.

## RCA behavior

RCA can run in two modes:

- simple
  - Uses the Anthropic path.
- hermes
  - Uses an OpenAI-compatible Hermes API server.
  - Supports tools_first and dossier investigation modes.
  - Falls back to simple analyzer when configured and primary execution fails.

RCA outputs include:

- summary and root cause
- confidence
- supporting evidence
- structured log evidence
- recommended actions
- GitHub search terms
- optional code references

After a successful RCA, follow-up questions are supported through POST /rca/followup.

## Testing

Install dev dependencies and run tests:

```bash
bash deploy.sh                                  # installs k3s + Helm + the obs stack + the demo
ANTHROPIC_API_KEY=sk-ant-... bash deploy.sh     # ...and the RCA key (or set it later in the UI)
K3S_EXTRA_SAN=100.x.x.x bash deploy.sh          # ...and an extra SAN for the API-server cert
```

It installs k3s + Helm, `helm install`s `kube-prometheus-stack` (Prometheus + node-exporter
+ kube-state-metrics) and `loki-stack` (Loki + Promtail), imports the demo images into
containerd, and applies `k8s/demo.yaml` (Jaeger + service-a/service-b) and `k8s/aggregator.yaml`
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

```
.
├── aggregator/               FastAPI backend — see aggregator/README.md
│   ├── cluster.py            GET /cluster/status — node-exporter + pod status
│   ├── history.py            GET /history + query-history recording (SQLite)
│   ├── mcp_observability_server.py  read-only MCP bridge for Hermes
│   ├── clients/              Prometheus / Loki / Jaeger / GitHub clients
│   ├── models/               request & result data shapes
│   └── core/
│       ├── aggregator.py     orchestrator (fan-out + correlate + RCA)
│       ├── correlator.py     cross-signal rule engine
│       ├── rca_analyzer.py   one-shot Anthropic RCA
│       ├── hermes_rca_agent.py      optional Hermes RCA agent (tools)
│       ├── rca_followup.py          RCA follow-up assistant
│       └── suspicious_absence.py    telemetry-gap detector
│
├── demo/
│   ├── service-a/            Upstream API (8001) — retry / circuit-breaker logic
│   ├── service-b/            Flaky downstream (8002) — all failure injection here
│   ├── service-c/            Payment processor (8003) — GatewayTimeoutError demo
│   └── service-d/            Inventory service (8004) — DatabaseConnectionError demo
│
├── frontend/                 Web UI served by nginx (static files)
│   ├── index.html
│   ├── css/styles.css
│   └── js/
│       ├── components/       one self-contained module per UI panel
│       │   ├── ClusterStatusPanel.js  ConnectionPanel.js  LlmConfigPanel.js
│       │   ├── HistoryPanel.js  Sidebar.js  ...
│       ├── api.js            calls the aggregator REST API
│       ├── evidence.js       makes RCA evidence items jump to the matching signal row
│       └── config.js         constants + mock data
│
├── infra/                    Prometheus / Loki / Promtail / nginx config; service registry
│   └── service-registry.yml  per-service GitHub metadata for RCA code references
│
├── k8s/                      Kubernetes manifests (demo workloads + aggregator)
├── deploy.sh                 one-shot k3s deploy
├── tests/                    unit tests — run without Docker using mock clients
├── docker-compose.yml        local stack
└── .env                      local secrets (not committed)
```

---

## Running the tests

Tests use mock HTTP clients, so they run without Docker or any live services.

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

`tests/test_aggregator.py` covers the correlator rules, the aggregator's fan-out behavior,
suspicious-absence events, Hermes RCA behavior, RCA follow-up, `TimeWindow` validation, and
the Loki multiline-merging / severity-detection helpers. `tests/test_rca_scenarios.py`
covers the RCA pipeline across failure scenarios with mocked model responses.
`tests/test_services.py` covers the service-registration endpoints,
`tests/test_mcp_observability_server.py` the read-only MCP bridge, and `tests/test_demo.py`
the demo scenario query-window events.

- [tests/test_aggregator.py](tests/test_aggregator.py)
- [tests/test_rca_scenarios.py](tests/test_rca_scenarios.py)
- [tests/test_mcp_observability_server.py](tests/test_mcp_observability_server.py)
- [tests/test_demo.py](tests/test_demo.py)

## Deployment

Kubernetes assets are under [k8s](k8s). The [deploy.sh](deploy.sh) script installs and wires a full k3s-based stack for demo environments.

Quick deploy examples:

```bash
bash deploy.sh
ANTHROPIC_API_KEY=sk-ant-... bash deploy.sh
K3S_EXTRA_SAN=100.x.x.x bash deploy.sh
```

After deployment, keep the frontend local and point it at the cluster aggregator endpoint from the UI connection panel.

## Folder Structure

```text
Kubernetes-Observability-Signal-Aggregator/
├── aggregator/
│   ├── clients/
│   ├── core/
│   ├── models/
│   ├── output/
│   ├── main.py
│   ├── config.py
│   ├── demo.py
│   ├── watchdog.py
│   └── notifier.py
├── frontend/
│   ├── css/
│   ├── js/
│   │   └── components/
│   └── index.html
├── demo/
│   ├── service-a/
│   ├── service-b/
│   ├── service-c/
│   ├── service-d/
│   └── scenario_*.sh
├── infra/
│   ├── prometheus.yml
│   ├── loki-config.yml
│   ├── promtail-config.yml
│   ├── nginx.conf
│   └── service-registry.yml
├── k8s/
├── tests/
├── docker-compose.yml
├── deploy.sh
├── pyproject.toml
└── README.md
```

## Project layout

Top-level directories:

- [aggregator](aggregator): FastAPI backend, clients, correlation, RCA, history, demo, watchdog.
- [frontend](frontend): Static UI with componentized panels and API integration.
- [infra](infra): Prometheus, Loki, Promtail, Nginx, service registry configuration.
- [demo](demo): Demo services and scenario scripts.
- [k8s](k8s): Kubernetes manifests.
- [tests](tests): Unit and integration-style tests with mocks.

## Common issues

Query returns empty panels:

**"Analyze with AI" shows an error or does nothing**
In default RCA mode, `ANTHROPIC_API_KEY` is missing/invalid in `.env` (then `docker compose
up -d aggregator` to reload) — or set it in the UI's **Config LLM** panel. In `RCA_MODE=hermes`,
check that `HERMES_API_URL` points at a running Hermes server. The RCA panel shows the error
inline; metrics/logs/traces still work regardless. If the "Code references" section is
missing or shows "No matches found", `GITHUB_TOKEN` isn't set (optional) — code references
only appear in scenarios that produce Python tracebacks (e.g. the crash scenario).

**Follow-up chat fails**
Follow-up requires a completed RCA first. Hermes is the primary follow-up provider; if it's
unavailable, the Anthropic fallback only works when `ANTHROPIC_API_KEY` is configured.

- Check ANTHROPIC_API_KEY for simple mode.
- Check RCA_MODE and Hermes connection values for hermes mode.

No code references in RCA:

- Check GITHUB_TOKEN and service registry metadata.
- Stack frame-based linking only appears when traceback-like logs exist.

Watchdog alerts not appearing:

- Ensure watchdog is started from the Watchdog panel or WATCHDOG_ENABLED=true.
- Ensure monitored services have enough traffic and anomaly confidence crosses threshold.

Port conflicts:

- Remap host ports via a local docker-compose.override.yml when needed.

## License

See repository license and organization policies for usage terms.
