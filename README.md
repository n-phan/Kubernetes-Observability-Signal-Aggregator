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

Service management endpoints:

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

- Docker Desktop with `docker compose`
- Ports `8001`, `8002`, `8080`, `8081`, `9090`, `3100`, and `16686` available
- Optional for RCA in simple mode: `ANTHROPIC_API_KEY`
- Optional for GitHub code-reference enrichment: `GITHUB_TOKEN`

## Quick start with Docker

1. Create env file:

```bash
cp .env.example .env
```

2. Set at least one RCA key path:

- Default RCA path: set ANTHROPIC_API_KEY.
- Hermes path: set RCA_MODE=hermes and Hermes connection settings.

3. Start stack:

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

Each run emits SSE events and finishes with query_target, window_start, and window_end. The frontend reuses this exact window for the next query and RCA call.

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
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Key test files:

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

- Generate traffic first from Demo panel.
- Verify selected target service exists in /services.

RCA not running:

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
