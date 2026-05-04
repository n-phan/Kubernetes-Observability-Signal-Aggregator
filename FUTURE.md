# Future Work

This document captures potential improvements and extensions to the K8s Observability
Signal Aggregator — both near-term usability improvements and longer-term architectural
directions.

---

## Monitoring external services

The aggregator is service-agnostic by design. It queries signals by label (`job` in
Prometheus and Loki, service name in Jaeger) and doesn't care what the underlying service
does. The demo services (`service-a`, `service-b`) are just a starting point.

### What any service needs to be monitored

| Signal | Requirement |
|---|---|
| Metrics | Service exposes a `/metrics` endpoint (Prometheus format), or an exporter scrapes it |
| Logs | Written to stdout (Promtail auto-captures all Docker container stdout via the Docker socket) |
| Traces | Service sends OpenTelemetry spans to Jaeger via OTLP on port 4317 |
| Identity | Consistent service name used as `job` label in Prometheus, Loki, and Jaeger |

### Adding a new service — current process (4 steps)

**Step 1: Add to `docker-compose.yml`**
```yaml
my-service:
  image: my-app:latest
  environment:
    OTEL_EXPORTER_OTLP_ENDPOINT: http://jaeger:4317
```

**Step 2: Add Prometheus scrape target in `infra/prometheus.yml`**
```yaml
- job_name: 'my-service'
  static_configs:
    - targets: ['my-service:8000']
```

**Step 3: Promtail picks up logs automatically** — no config change needed, Docker
service discovery handles it.

**Step 4: Query the aggregator**
```
POST /query  { "target": "my-service", "include_rca": true }
```

### Language and runtime support

The system is not Python-specific. Any service instrumented with OpenTelemetry can
participate:

| Language | Metrics | Traces |
|---|---|---|
| Python | `prometheus-fastapi-instrumentator`, `prometheus_client` | `opentelemetry-sdk` |
| Go | `prometheus/client_golang` | `go.opentelemetry.io/otel` |
| Node.js | `prom-client` | `@opentelemetry/sdk-node` |
| Java | Micrometer + Prometheus registry | OpenTelemetry Java agent |
| Ruby | `prometheus-client` | `opentelemetry-ruby` |

### Services outside Docker

Services running on VMs, bare metal, or cloud instances can be monitored with additional
configuration:

- **Prometheus**: Add the host's IP and metrics port as a static scrape target
- **Loki**: Configure Promtail on the remote host to tail log files and push to your Loki instance
- **Jaeger**: Configure the service's OTLP exporter to point to your Jaeger host IP

### Third-party and managed services

Services that don't expose native Prometheus metrics can be bridged via exporters:

- AWS CloudWatch → `prometheus/cloudwatch_exporter`
- GitHub Actions → `github-actions-exporter`
- PostgreSQL → `postgres_exporter`
- Redis → `redis_exporter`
- Kubernetes cluster → `kube-state-metrics`

---

## Potential improvements

### 1. Service registry in the frontend

**Current state:** Users type a service name into the query bar manually.

**Problem:** There's no discovery — users have to know what services exist. Typos return
empty results silently.

**Proposed improvement:** The aggregator could expose a `GET /services` endpoint that
queries Prometheus for all known `job` labels and returns the list. The frontend could
populate a dropdown or autocomplete from this list, making it impossible to query a
nonexistent service.

This also naturally solves onboarding — when a new service is added to the stack, it
appears in the dropdown automatically once Prometheus scrapes it.

---

### 2. Per-service GitHub repository configuration

**Current state:** `github_path_prefix` is hardcoded as `demo/service-b` in `config.py`,
and `GITHUB_REPO` is a single global setting. RCA code references always point to the
same repository regardless of which service is being analyzed.

**Problem:** In a multi-service environment, `service-a`, `service-b`, and any additional
services may live in different repositories. Code references for `service-a` should link
to its repo, not `service-b`'s.

**Proposed improvement:** Introduce a service-to-repository mapping, either as a config
file or as a `GET /services/{name}/config` endpoint:

```yaml
# infra/service-registry.yml
services:
  service-a:
    github_repo: my-org/service-a
    github_path_prefix: src
  service-b:
    github_repo: my-org/service-b
    github_path_prefix: demo/service-b
  payment-service:
    github_repo: my-org/payments
    github_path_prefix: app
```

The `GitHubLinker` would look up the target service's repo config at query time rather
than using global settings.

---

### 3. Service onboarding UI

**Current state:** Adding a new service requires manually editing `docker-compose.yml`
and `infra/prometheus.yml`, then restarting containers.

**Proposed improvement:** A service registration panel in the frontend where users can:

- Enter a service name, metrics endpoint, and GitHub repo
- The aggregator writes the Prometheus scrape config and docker-compose service entry
- A "Test connection" button verifies that metrics, logs, and traces are reachable before
  saving

This lowers the barrier significantly for users who aren't comfortable editing YAML.

---

### 4. Alert thresholds per service

**Current state:** The correlator uses fixed thresholds for all services:
- Error rate > 1%
- p99 latency > 1000ms
- Log error burst > 5%

**Problem:** These thresholds are appropriate for the demo services but not universally
applicable. A batch processing service might have tolerable error rates of 5%; a payment
service might require alerting at 0.1%.

**Proposed improvement:** Allow per-service threshold overrides in the service registry
config, falling back to global defaults when not specified.

---

### 5. Historical query and trend analysis

**Current state:** Every query is an independent point-in-time snapshot of the last N
minutes. There's no persistence, no comparison over time, no trending.

**Proposed improvement:** Store query results (or at minimum RCA summaries) in a
lightweight database (SQLite for local, Postgres for production). This enables:

- "Show me how this service's error rate has changed over the past week"
- "Has this root cause been identified before?"
- Incident timeline: a log of when each anomaly was first detected

---

### 6. Correlator ML upgrade

**Current state:** The correlator uses fixed rule-based thresholds. The code already
contains `# ML-HOOK` comments at each decision point marking where a model could be
substituted.

**Proposed improvement:** Train a lightweight anomaly detection model on historical signal
data (e.g. using Isolation Forest or LSTM-based time series anomaly detection). This would
allow the correlator to detect subtle anomalies that don't cross fixed thresholds — for
example, a gradual memory leak that never triggers a restart but shows a steady upward
trend.

---

### 7. Slack / PagerDuty / webhook notifications

**Current state:** RCA results are only visible in the frontend. Users have to actively
query to discover incidents.

**Proposed improvement:** Add a background polling mode where the aggregator periodically
queries registered services and sends RCA summaries to a webhook (Slack, PagerDuty, Teams)
when anomalies are detected. This moves the tool from reactive (check when you suspect a
problem) to proactive (get notified when something goes wrong).

---

### 8. Multi-environment support

**Current state:** The stack assumes a single local Docker environment.

**Proposed improvement:** Allow the aggregator to be pointed at remote observability
backends (a staging or production Prometheus/Loki/Jaeger) via environment variable
overrides. This would make the same tool usable across local, staging, and production
environments without code changes — just a different `.env` file per environment.

---

## Known limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| GitHub code references only work for services with Python tracebacks in logs | Other languages don't produce `File "..."` stack frame format | Extend `_link_stack_frames` in `github.py` to parse Go, Java, Node.js stack trace formats |
| Single global `GITHUB_REPO` | Multi-service repos get wrong code links | See improvement #2 above |
| Correlator thresholds are fixed | False positives/negatives for non-demo services | See improvement #4 above |
| No persistence | Query history is lost on page refresh | See improvement #5 above |
| Safari opens GitHub links in same tab | Minor UX friction | Known Safari ITP limitation with cross-site fragments in new tabs — Cmd+Click opens in new tab and preserves the line anchor |
