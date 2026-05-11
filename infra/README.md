# Infrastructure

This directory contains configuration files for the observability stack. The services
themselves run as Docker containers defined in `docker-compose.yml` at the project root —
these files are mounted into the containers at startup.

---

## What's in this directory

```
infra/
├── prometheus.yml        Prometheus scrape targets and global settings
├── loki-config.yml       Loki storage and ingester configuration
├── promtail-config.yml   Promtail log scraping and label rules
├── nginx.conf            Nginx reverse proxy config for the frontend
└── service-registry.yml  Per-service GitHub metadata for RCA code-reference links
```

---

## System overview

The stack has three layers: the demo microservices that generate signals, the observability
backends that collect them, and the aggregator that queries and correlates them.

```
┌─────────────────────────────────────────────────────────────────┐
│  Demo microservices                                             │
│                                                                 │
│   service-a :8001  ──────────────────►  service-b :8002        │
│   (upstream API)      HTTP calls         (flaky downstream)     │
│                                                                 │
│   service-c :8003                        service-d :8004        │
│   (payment processor)                    (inventory service)    │
└────────┬───────────────────────────────────────┬───────────────┘
         │ metrics (/metrics)                     │ metrics, logs, traces
         │ traces (OTLP :4317)                    │
         ▼                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  Observability backends                                         │
│                                                                 │
│  Prometheus :9090   Loki :3100   Jaeger :16686                 │
│       ▲                  ▲                                      │
│       │ scrape           │ push                                 │
│       │                  │                                      │
│  (pull model)      Promtail :9080                               │
│                    (reads Docker logs via socket)               │
└────────────────────────┬────────────────────────────────────────┘
                         │ query (HTTP)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Aggregator :8080                                               │
│  Queries all three backends, correlates signals, runs RCA       │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Frontend :8081  (nginx serving static HTML/JS)                 │
└─────────────────────────────────────────────────────────────────┘
```

### Signal collection model

Each observability backend uses a different collection pattern:

- **Prometheus** uses a **pull model** — it scrapes the `/metrics` endpoint on each service
  every 15 seconds. The services don't need to know about Prometheus.
- **Jaeger** uses a **push model** — the services send trace spans directly to Jaeger via
  the OTLP protocol on startup. No scraping involved.
- **Loki** uses a **push model via Promtail** — Promtail reads container stdout from the
  Docker socket and ships the lines to Loki. The services just write to stdout.

---

## Services

### Prometheus

**What it does:** Time-series metrics database. Scrapes HTTP endpoints on the demo services
and stores the resulting metrics for querying.

**Port:** `9090`  
**Config:** `infra/prometheus.yml`  
**Data:** persisted in the `prometheus_data` Docker volume

**Default scrape targets:**

| Job | Target | Endpoint | Interval |
|---|---|---|---|
| `service-a` | `service-a:8001` | `/metrics` | 15s |
| `service-b` | `service-b:8002` | `/metrics` | 15s |
| `service-c` | `service-c:8003` | `/metrics` | 15s |

Additional services (e.g. `service-d`) can be registered at runtime via the **☰ Services**
panel in the frontend, which appends a new `scrape_configs` entry to `prometheus.yml` and
triggers a hot-reload — no container restart required.

The demo services expose Prometheus-compatible metrics automatically via
`prometheus-fastapi-instrumentator`. This includes:
- `http_requests_total` — request count by method, status, and path
- `http_request_duration_seconds` — latency histogram

`http_error_rate` is not a raw metric from the services. It is the name the aggregator
assigns to the result of its PromQL expression
`rate(http_requests_total{status=~"5.."}[5m])`, which filters `http_requests_total` for
5xx responses. This distinction matters if you are querying Prometheus directly — you
will not find an `http_error_rate` metric there.

**How the aggregator queries it:** The Prometheus client uses the `query_range` HTTP API
to fetch metric series over a time window. Queries are parameterized by the target service
name matching the `job` label set during scraping.

---

### Loki

**What it does:** Log aggregation backend. Receives log streams from Promtail, stores them
with labels, and exposes a query API for retrieving them by label selector and time range.

**Port:** `3100`  
**Config:** `infra/loki-config.yml`  
**Data:** persisted in the `loki_data` Docker volume (BoltDB index, filesystem chunks)

**Key configuration choices:**
- `auth_enabled: false` — no authentication required (appropriate for local demo)
- `reject_old_samples_max_age: 168h` — rejects logs older than one week, preventing
  accidental ingestion of historical data
- `schema: v11` with BoltDB index — a simple single-node storage schema suitable for
  local development

**How the aggregator queries it:** The Loki client uses the `query_range` API with LogQL
label selectors. Two selectors are tried in order: `{job="<target>"}` and
`{service=~".*<target>.*"}`, returning on the first that yields results.

---

### Promtail

**What it does:** Log shipping agent. Reads container stdout/stderr from the Docker socket,
attaches metadata labels, and pushes log streams to Loki.

**Port:** `9080` (internal metrics/health only, not exposed to host)  
**Config:** `infra/promtail-config.yml`

Promtail is the link between container logs and Loki. Without it, Loki receives nothing
and the logs panel in the frontend stays empty.

**How labels are assigned:**

```yaml
relabel_configs:
  # The container name becomes the "service" label (e.g. "service-b")
  - source_labels: [__meta_docker_container_name]
    regex: /(.*)
    target_label: service

  # The Docker Compose service name becomes the "job" label (e.g. "service-b")
  - source_labels: [__meta_docker_container_label_com_docker_compose_service]
    target_label: job
```

The `job` label is what the aggregator's Loki client queries against. A query for
`service-a` translates to the LogQL selector `{job="service-a"}`.

Promtail uses Docker service discovery (`docker_sd_configs`) to automatically find all
running containers, so adding a new service to `docker-compose.yml` is enough for its
logs to appear in Loki — no Promtail configuration changes needed.

---

### Jaeger

**What it does:** Distributed tracing backend. Receives trace spans from instrumented
services and stores them for querying. Provides a built-in web UI for exploring individual
traces.

**Ports:**
- `16686` — Jaeger UI and HTTP query API
- `4317` — OTLP gRPC receiver (used by the demo services to send spans)
- `4318` — OTLP HTTP receiver
- `6831/udp` — Jaeger compact Thrift (legacy, not used by demo services)

**Image:** `jaegertracing/all-in-one` — runs the collector, query service, and UI in a
single container. Suitable for local development; production deployments separate these.

**How traces are sent:** The demo services use OpenTelemetry auto-instrumentation to
capture trace spans for every HTTP request. These are exported to Jaeger via OTLP on
port `4317`. The `COLLECTOR_OTLP_ENABLED: "true"` environment variable enables Jaeger's
OTLP receiver.

**How the aggregator queries it:** The Jaeger client uses the HTTP query API at port
`16686` to retrieve traces by service name and time range. Spans are assembled into
`Trace` objects, error spans are identified by the `error=true` tag, and p99 latency is
computed across all returned traces.

**Jaeger UI:** Accessible at `http://localhost:16686` — useful for manually exploring
individual traces and their spans during development.

---

### node-exporter

**What it does:** Exposes host-level metrics (CPU, memory, load, filesystems, network) on
`:9100/metrics`. Prometheus scrapes it as the `node` job; the aggregator's
`GET /cluster/status` summarizes those metrics for the homepage **Cluster Status** panel.

**Port:** `9100` (container only — not published to the host; Prometheus reaches it over
the Docker network)  
**Config:** none — command-line flags only (`--path.procfs` / `--path.sysfs` /
`--path.rootfs` so it reads the host's `/proc`, `/sys`, `/` rather than the container's;
see `docker-compose.yml`).

> In a real Kubernetes cluster, `kube-prometheus-stack` runs `node-exporter` as a DaemonSet
> (one per node) plus `kube-state-metrics` for pod↔node mapping, so the Cluster Status panel
> can show real per-node resources and per-pod status. See the repo root README's
> *Deploying to Kubernetes* section and the `k8s/` manifests.

---

### Nginx (frontend)

**What it does:** Serves the static frontend files (HTML, CSS, JS) on port 8081.

**Port:** `8081` (host) → `80` (container)  
**Config:** `infra/nginx.conf`

The frontend is a set of static HTML, CSS, and JavaScript files in `frontend/`. Nginx
serves them directly from a volume mount — no build step required, and changes to frontend
files are visible immediately on browser refresh without restarting the container.

**nginx does not proxy API traffic.** The browser calls the aggregator REST API directly
on port 8080. If you are looking for API routing rules, there are none — see
`docker-compose.yml` for the aggregator's port mapping.

SSE buffering is handled by the aggregator itself: `aggregator/demo.py` sets the
`X-Accel-Buffering: no` response header on streaming endpoints, which instructs any
upstream nginx proxy to pass chunks through immediately. Because there is no proxy here,
the header has no effect in the demo stack but is included for compatibility if nginx is
placed in front of the aggregator in a real deployment.

---

## Service registry

`infra/service-registry.yml` maps service names to their GitHub repository metadata. The
aggregator reads this file when enriching an RCA result with code-reference links — it
determines which repo and file path to build the GitHub URL from.

```yaml
services:
  # Service hosted inside the aggregator repo — needs a path prefix
  # so the linker can construct demo/service-a/main.py → GitHub blob URL.
  service-a:
    github_repo: owner/repo
    github_branch: main
    github_path_prefix: demo/service-a

  # Service with its own dedicated repo — main.py lives at the repo root,
  # so no path prefix is needed.
  service-c:
    github_repo: owner/service-c
```

**Fields:**

| Field | Required | Default | Description |
|---|---|---|---|
| `github_repo` | yes | — | `owner/repo` — the GitHub repository containing the service's source |
| `github_branch` | no | `main` | Branch used when building blob URLs |
| `github_path_prefix` | no | _(none)_ | Directory prefix prepended to stack-trace file paths (e.g. `demo/service-a`). Omit when the service has its own repo and `main.py` lives at the root. |

**How it works:**

When the RCA analyzer produces a result that includes Python stack frames (e.g. `File
"main.py", line 84`), the GitHub linker looks up the queried service in this file. It
uses `github_repo` and `github_branch` to form the base URL, and prepends
`github_path_prefix` to the file path so that `/app/main.py` resolves to the correct
location in the repository (e.g. `demo/service-a/main.py`). Services with their own
dedicated repos don't need a prefix because their `main.py` is already at the root.

**Editing:** The registry can be edited directly in this file, or through the **Edit**
button in the **☰ Services → Registered** panel. Changes take effect immediately — no
container restart required.

---

## Ports at a glance

| Service | Host port | Purpose |
|---|---|---|
| service-a | 8001 | Demo upstream API (calls service-b) |
| service-b | 8002 | Demo downstream API (flaky — all failure injection here) |
| service-c | 8003 | Demo payment processor |
| service-d | 8004 | Demo inventory service |
| aggregator | 8080 | Observability query API |
| frontend | 8081 | Web UI |
| Prometheus | 9090 | Metrics storage + UI |
| Loki | 3100 | Log storage API |
| Jaeger | 16686 | Trace storage + UI |
| node-exporter | — | Host metrics (`:9100`, not published — Prometheus scrapes it internally) |

Prometheus and Jaeger each have their own web UIs accessible at their respective ports,
which can be useful when debugging what data is actually being collected.
