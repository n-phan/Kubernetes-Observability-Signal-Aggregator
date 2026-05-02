# Setup and Configuration Guide

This document walks through every credential and configuration variable you need to set
before running the system, and explains which ones are optional vs required.

---

## TL;DR — Quick start (minimal setup)

```bash
cp .env.example .env
# That's it. Run docker compose up -d and you're ready to query.
```

If you want RCA and GitHub linking, add these to `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
GITHUB_REPO=your-org/your-repo
```

---

## Full breakdown

### Layer 1: Local observability backends (no credentials needed)

These run entirely in Docker and need no external API keys. They're already configured in
`docker-compose.yml`.

| Service | Port | Credentials needed? | Purpose |
|---|---|---|---|
| Prometheus | 9090 | No | Scrapes metrics from demo services |
| Loki | 3100 | No | Collects logs from demo services |
| Jaeger | 16686 | No | Receives trace spans from demo services |

**Location:** `docker-compose.yml` has the URLs hardcoded:
```yaml
prometheus:
  ...
  ports: ["9090:9090"]

aggregator:
  environment:
    PROMETHEUS_URL: http://prometheus:9090
    LOKI_URL: http://loki:3100
    JAEGER_URL: http://jaeger:16686
```

These point to the Docker container hostnames (not localhost — that's important). No changes needed.

---

### Layer 2: Demo microservices (environment variables, no external credentials)

These run in Docker and are configured entirely via environment variables in `docker-compose.yml`.
No external credentials needed. You toggle failure modes here.

**Location:** `docker-compose.yml` under each service's `environment:` section.

#### service-b (downstream, port 8002)

Control what kind of failures it produces:

```yaml
service-b:
  environment:
    # Fraction of /data requests that fail with 500 (0.0 = none, 1.0 = all)
    FAILURE_RATE: "0.0"
    
    # Milliseconds of artificial delay per request (0 = none, 2000 = 2 sec)
    LATENCY_MS: "0"
    
    # Expose /oom endpoint that leaks 10 MB per call (true/false)
    OOM_ENDPOINT: "false"
```

**To trigger different demo scenarios**, edit these values and restart:
```bash
# Edit docker-compose.yml, then:
docker compose up -d --build service-b
```

#### service-a (upstream, port 8001)

Control how it responds to failures from service-b:

```yaml
service-a:
  environment:
    # Retry failed requests up to 3 times with exponential backoff (true/false)
    ENABLE_RETRY: "false"
    
    # Open circuit after 5 consecutive errors from service-b (true/false)
    ENABLE_CIRCUIT_BREAKER: "false"
```

---

### Layer 3: Aggregator API (port 8080)

The aggregator itself needs to know where to find the observability backends. These are
passed as environment variables or loaded from `.env`.

**Locations to set these:**
1. `.env` file (copied from `.env.example`)
2. Or directly in `docker-compose.yml` under `aggregator: environment:`
3. Or as shell env vars when running locally (`export PROMETHEUS_URL=...`)

#### Required settings (observability backends)

These default to localhost:port but inside Docker containers they need to use container
hostnames. The `docker-compose.yml` already has these set correctly:

```
PROMETHEUS_URL=http://prometheus:9090
LOKI_URL=http://loki:3100
JAEGER_URL=http://jaeger:16686
```

If running the aggregator **outside Docker** (e.g., `python -m aggregator.cli`), change these to:

```
PROMETHEUS_URL=http://localhost:9090
LOKI_URL=http://localhost:3100
JAEGER_URL=http://localhost:16686
```

#### Optional settings (aggregator behavior)

```
DEFAULT_LOOKBACK_MINUTES=30        # Default time window if not specified
MAX_LOG_LINES=500                  # Cap on log lines per query
MAX_TRACES=50                       # Cap on traces per query
HTTP_TIMEOUT_SECONDS=30             # Per-backend request timeout
LOG_LEVEL=info                      # debug, info, warn, error
```

These have sensible defaults in `pyproject.toml` and `.env.example`. Change them only if
you need to tune behavior.

---

### Layer 4: RCA and GitHub linking (external credentials required)

This is where you need actual API tokens. **These are completely optional** — the system
works fine without them, RCA just doesn't run.

#### Anthropic API key (required for RCA to work)

**What it is:** Access token to call Claude via the Anthropic API.

**How to get it:**
1. Go to https://console.anthropic.com
2. Sign up or log in
3. Click "API Keys" in the left sidebar
4. Click "Create Key"
5. Copy the key (starts with `sk-ant-`)

**Where to set it:**
```
# In .env:
ANTHROPIC_API_KEY=sk-ant-v7-abc123...
```

Or in `docker-compose.yml`:
```yaml
aggregator:
  environment:
    ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY:-}"
```

The `${ANTHROPIC_API_KEY:-}` syntax means "use the env var from the host machine, or leave
blank if not set."

**To set it from the command line before running docker compose:**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up -d
```

**What happens if it's missing:**
- RCA silently skips (the query still returns metrics/logs/traces, just no RCA)
- You'll see `"performed": false` in the RCA section of the output
- This is **not an error** — it's intentional graceful degradation

#### GitHub token (required for GitHub code search; optional for stack trace linking)

**What it is:** Personal access token to search and link code in your GitHub repo.

**How to get it:**
1. Go to https://github.com/settings/tokens?type=beta
2. Click "Generate new token"
3. Choose "Fine-grained personal access token"
4. Give it a name (e.g., "obs-aggregator")
5. Set expiration (90 days or longer recommended)
6. Select the target repository from the dropdown
7. Under "Repository permissions," grant `Contents: Read`
8. Click "Generate token"
9. Copy the token (starts with `ghp_`)

**Where to set it:**
```
# In .env:
GITHUB_TOKEN=ghp_abc123...
GITHUB_REPO=your-org/your-repo
GITHUB_DEFAULT_BRANCH=main
```

The `GITHUB_REPO` value must be exact: `owner/repo` (case-sensitive).

**What happens if it's missing:**
- Stack trace linking still works (pulls file:line references directly from log text)
- GitHub code search doesn't work (the `github_search_terms` from the LLM go unused)
- You'll still see code references if they came from stack traces
- **Not an error** — the system degrades gracefully

**Rate limiting:**
- Without a token: 30 requests per minute (usually enough for light testing)
- With a token: 5,000 requests per hour per repository

---

## Configuration checklist

### Minimal (just run the demo)

- [ ] Clone the repo
- [ ] `cp .env.example .env`
- [ ] `docker compose up -d --build`
- [ ] Run a query: `obs query service-a`

### With RCA (show LLM analysis)

- [ ] Get Anthropic API key from https://console.anthropic.com
- [ ] Add to `.env`: `ANTHROPIC_API_KEY=sk-ant-...`
- [ ] Restart: `docker compose restart aggregator`
- [ ] Generate errors: `FAILURE_RATE: "0.5"` in docker-compose.yml
- [ ] Query and see RCA: `obs query service-a --json | jq '.rca'`

### With GitHub linking (show code references)

- [ ] Get GitHub token from https://github.com/settings/tokens?type=beta
- [ ] Add to `.env`:
  ```
  GITHUB_TOKEN=ghp_...
  GITHUB_REPO=your-org/your-repo
  ```
- [ ] Restart: `docker compose restart aggregator`
- [ ] Query and see links: `obs query service-a --json | jq '.rca.code_references'`

---

## File-by-file configuration locations

| File | What it contains | Required? | Read at? |
|---|---|---|---|
| `.env` | API keys, backend URLs, tuning params | No (defaults exist) | Startup |
| `docker-compose.yml` | Service config, failure mode toggles | No (sensible defaults) | `docker compose up` |
| `aggregator/config.py` | Pydantic settings loader | No (auto-loaded) | Runtime |
| `pyproject.toml` | Python package metadata, tool config | No (static) | Install time |

### In `.env`

```
# Observability backends (defaults to localhost:port if running outside Docker)
PROMETHEUS_URL=http://prometheus:9090
LOKI_URL=http://loki:3100
JAEGER_URL=http://jaeger:16686

# Aggregator behavior (has sensible defaults)
DEFAULT_LOOKBACK_MINUTES=30
MAX_LOG_LINES=500
MAX_TRACES=50
HTTP_TIMEOUT_SECONDS=30
LOG_LEVEL=info

# RCA — Anthropic (optional)
ANTHROPIC_API_KEY=

# GitHub (optional)
GITHUB_TOKEN=
GITHUB_REPO=
GITHUB_DEFAULT_BRANCH=main
```

### In `docker-compose.yml`

For **demo services**, edit the `environment:` section of `service-a` and `service-b`:

```yaml
service-b:
  environment:
    FAILURE_RATE: "0.0"      # Toggle failure rate
    LATENCY_MS: "0"           # Toggle latency
    OOM_ENDPOINT: "false"     # Toggle OOM endpoint

service-a:
  environment:
    ENABLE_RETRY: "false"              # Toggle retry
    ENABLE_CIRCUIT_BREAKER: "false"    # Toggle circuit breaker
```

For **aggregator**, the API keys are passed through from your host machine:

```yaml
aggregator:
  environment:
    ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY:-}"   # Pulled from .env or export
    GITHUB_TOKEN: "${GITHUB_TOKEN:-}"             # Pulled from .env or export
    GITHUB_REPO: "${GITHUB_REPO:-}"               # Pulled from .env or export
```

---

## Common setup mistakes

**"docker: permission denied"**
You need to be in the `docker` group or run with `sudo`. On macOS, Docker Desktop
handles this. On Linux: `sudo usermod -aG docker $USER` then restart your shell.

**"port 8080 already allocated"**
Something else is using port 8080. Either kill it (`lsof -i :8080`) or change the
aggregator port in docker-compose.yml:
```yaml
aggregator:
  ports:
    - "8081:8080"  # host:container, so use 8081 locally
```

**"Anthropic API key invalid"**
Keys expire or can be revoked. Go to https://console.anthropic.com and create a new one.

**"GitHub token unauthorized"**
The token might be for a different repo, or lack `Contents: Read` permission. Go to
https://github.com/settings/tokens and verify the token's permissions and scope.

**"GITHUB_REPO value wrong"**
Must be exact: `owner/repo` (case-sensitive, no leading slash, no `.git` suffix).
Check: `git remote -v` on the target repo to see the exact format.

**"Observability stack won't start"**
Wait 15–30 seconds after `docker compose up -d`. Use `docker compose ps` to check
health status. If a service is crashing, check logs: `docker compose logs prometheus`.

---

## Environment variable reference

All variables are loaded from `.env` at runtime. Here's the complete list:

| Variable | Default | Type | Purpose | Required? |
|---|---|---|---|---|
| `PROMETHEUS_URL` | `http://localhost:9090` | URL | Prometheus base URL | No |
| `LOKI_URL` | `http://localhost:3100` | URL | Loki base URL | No |
| `JAEGER_URL` | `http://localhost:16686` | URL | Jaeger query API URL | No |
| `DEFAULT_LOOKBACK_MINUTES` | `30` | int | Default time window | No |
| `MAX_LOG_LINES` | `500` | int | Cap on log lines | No |
| `MAX_TRACES` | `50` | int | Cap on traces | No |
| `HTTP_TIMEOUT_SECONDS` | `30.0` | float | Per-backend timeout | No |
| `API_HOST` | `0.0.0.0` | str | API listen address | No |
| `API_PORT` | `8080` | int | API listen port | No |
| `LOG_LEVEL` | `info` | str | Logging verbosity | No |
| `ANTHROPIC_API_KEY` | (empty) | str | Claude API access token | No* |
| `RCA_ENABLED` | `true` | bool | Enable/disable RCA entirely | No |
| `GITHUB_TOKEN` | (empty) | str | GitHub PAT for code search | No* |
| `GITHUB_REPO` | (empty) | str | `owner/repo` to link against | No* |
| `GITHUB_DEFAULT_BRANCH` | `main` | str | Branch for blob URLs | No |

**No* = No, but recommended for full functionality**

---

## Running outside Docker (for development)

If you want to run the aggregator CLI without docker-compose (e.g., to debug locally):

```bash
# Start just the observability backends in Docker
docker compose up -d prometheus loki jaeger

# In your terminal, set backend URLs to localhost (not container names)
export PROMETHEUS_URL=http://localhost:9090
export LOKI_URL=http://localhost:3100
export JAEGER_URL=http://localhost:16686

# Activate your venv and run
source .venv/bin/activate
obs query service-a --lookback 30
```

The aggregator will query the backends running in Docker via `localhost` ports.

---

## Verifying your setup

Once you've set everything up, run these checks:

```bash
# 1. Check .env exists and has the values you set
cat .env | grep -v "^#" | grep -v "^$"

# 2. Check all containers are healthy
docker compose ps

# 3. Check aggregator can reach Prometheus
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets | length'

# 4. Try a query against the demo services
obs query service-a --json | jq '.meta'

# 5. If you set Anthropic key, generate errors and check RCA
FAILURE_RATE: "0.5" docker compose up -d --build service-b
for i in $(seq 1 40); do curl -s http://localhost:8001/api/data; done
sleep 60
obs query service-a --json | jq '.rca.performed'
```

If any of these fail, check the "Common setup mistakes" section above or the README's
"Common issues" section.
