import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from aggregator import history
from aggregator.cluster import router as cluster_router
from aggregator.config import settings
from aggregator.core.aggregator import SignalAggregator
from aggregator.demo import router as demo_router
from aggregator.models.followup import FollowUpRequest, FollowUpResponse
from aggregator.models.query import QueryRequest
from aggregator.models.result import UnifiedResult
from aggregator.watchdog import AutoWatchdog

_INFRA_SERVICES: frozenset[str] = frozenset(
    {"prometheus", "loki", "jaeger", "promtail", "aggregator", "node", "node-exporter"}
)

_PROTECTED_SERVICES: frozenset[str] = frozenset(
    {"prometheus", "loki", "jaeger", "promtail", "aggregator", "node", "node-exporter",
     "service-a", "service-b"}
)

# ── Service health badge constants ─────────────────────────────────────────────

# Must be ≥ 2m: circuit breaker on service-a causes error spikes to complete in
# <1 second, so a 30s window expires before the 30s badge refresh fires.
_HEALTH_WINDOW = "2m"

# Services that use custom error counters instead of http_requests_total{status=~"5.."}
_SERVICE_ERROR_METRICS: dict[str, str] = {
    "service-c": "payment_errors_total",
    "service-d": "inventory_lookup_errors_total",
}


async def _compute_service_health(client: httpx.AsyncClient, svc: str) -> str:
    """Return 'healthy', 'degraded', 'critical', or 'unknown' for one service."""
    try:
        error_metric = _SERVICE_ERROR_METRICS.get(svc)
        if error_metric:
            error_expr = f'increase({error_metric}{{job="{svc}"}}[{_HEALTH_WINDOW}])'
        else:
            error_expr = f'increase(http_requests_total{{job="{svc}",status=~"5.."}}[{_HEALTH_WINDOW}])'
        total_expr = f'increase(http_requests_total{{job="{svc}"}}[{_HEALTH_WINDOW}])'

        errors_resp, total_resp = await asyncio.gather(
            client.get(f"{settings.prometheus_url}/api/v1/query", params={"query": error_expr}),
            client.get(f"{settings.prometheus_url}/api/v1/query", params={"query": total_expr}),
        )

        def _scalar(data: dict) -> float:
            results = data.get("data", {}).get("result", [])
            if not results:
                return 0.0
            try:
                return max(0.0, float(results[0]["value"][1]))
            except (ValueError, TypeError, IndexError):
                return 0.0

        errors = _scalar(errors_resp.json())
        total = _scalar(total_resp.json())

        if total <= 0:
            # No traffic in window → idle but healthy; "unknown" is reserved for
            # Prometheus unreachable (caught by the outer except).
            return "healthy"
        rate = errors / total
        if rate >= 0.20:
            return "critical"
        if rate >= 0.01:
            return "degraded"
        return "healthy"
    except Exception:
        return "unknown"

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)

_aggregator: SignalAggregator | None = None
_watchdog: AutoWatchdog | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _aggregator, _watchdog
    await history.init_db()
    _aggregator = SignalAggregator()
    _watchdog = AutoWatchdog(aggregator_getter=lambda: _aggregator)
    if settings.watchdog_enabled:
        await _watchdog.start(
            interval_seconds=settings.watchdog_interval_seconds,
            lookback_minutes=settings.watchdog_lookback_minutes,
            anomaly_threshold=settings.watchdog_anomaly_threshold,
        )
    logger.info("Signal aggregator started")
    yield
    if _watchdog:
        await _watchdog.stop()
    if _aggregator:
        await _aggregator.close()
    logger.info("Signal aggregator shut down")


app = FastAPI(
    title="K8s Observability Signal Aggregator",
    description="Unified query interface for Prometheus, Loki, and Jaeger",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(demo_router)
app.include_router(cluster_router)
app.include_router(history.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/query", response_model=UnifiedResult)
async def query(request: QueryRequest) -> UnifiedResult:
    if _aggregator is None:
        raise HTTPException(status_code=503, detail="Aggregator not initialised")
    return await _aggregator.query(request)


@app.get("/services")
async def list_services() -> list[str]:
    """Return monitored service names from prometheus.yml, excluding infrastructure jobs."""
    config_path = Path(settings.prometheus_config_path)
    try:
        with config_path.open() as f:
            prom_cfg: dict = yaml.safe_load(f) or {}
        return [
            j["job_name"]
            for j in prom_cfg.get("scrape_configs", [])
            if "job_name" in j and j["job_name"] not in _INFRA_SERVICES
        ]
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("Failed to read prometheus.yml for service list: %s", exc)
        return []


# ── Service registration request/response models ──────────────────────────────

class TestEndpointRequest(BaseModel):
    url: str


class RegisterServiceRequest(BaseModel):
    name: str
    metrics_url: str
    github_repo: str | None = None
    github_branch: str | None = None
    github_path_prefix: str | None = None


class UpdateServiceGithubRequest(BaseModel):
    github_repo: str | None = None
    github_branch: str | None = None
    github_path_prefix: str | None = None


class EnvironmentRequest(BaseModel):
    name: str  # local | staging | production


class WatchdogRequest(BaseModel):
    enabled: bool
    interval_seconds: int = 60
    lookback_minutes: int = 15
    anomaly_threshold: float = 0.7


# ── Service registration endpoints ────────────────────────────────────────────

@app.post("/services/test")
async def test_metrics_endpoint(request: TestEndpointRequest) -> dict[str, object]:
    """
    Probe a metrics URL and return whether it is reachable.
    Uses a 3-second timeout so the UI doesn't hang.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(request.url)
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/services/register")
async def register_service(request: RegisterServiceRequest) -> dict[str, object]:
    """
    Append a new scrape target to prometheus.yml, trigger Prometheus hot-reload,
    and optionally record GitHub metadata in service-registry.yml.
    """
    parsed = urlparse(request.metrics_url)
    target = parsed.netloc          # e.g. "my-service:8003"
    metrics_path = parsed.path or "/metrics"

    config_path = Path(settings.prometheus_config_path)

    # ── Load and duplicate-check ───────────────────────────────────────────
    try:
        with config_path.open() as f:
            prom_cfg: dict = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"prometheus.yml not found at {config_path}")

    existing = {job["job_name"] for job in prom_cfg.get("scrape_configs", [])}
    if request.name in existing:
        raise HTTPException(status_code=409, detail=f"Job '{request.name}' already exists")

    # ── Append new job ─────────────────────────────────────────────────────
    prom_cfg.setdefault("scrape_configs", []).append({
        "job_name": request.name,
        "static_configs": [{"targets": [target]}],
        "metrics_path": metrics_path,
    })

    with config_path.open("w") as f:
        yaml.dump(prom_cfg, f, default_flow_style=False, sort_keys=False)

    logger.info("Registered new service '%s' → %s%s", request.name, target, metrics_path)

    # ── Prometheus hot-reload ──────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{settings.prometheus_url}/-/reload")
    except Exception as exc:
        logger.warning("Prometheus reload failed (service registered but reload skipped): %s", exc)

    # ── Optional: service-registry.yml ────────────────────────────────────
    if request.github_repo:
        registry_path = Path(settings.service_registry_path)
        if registry_path.exists():
            with registry_path.open() as f:
                registry: dict = yaml.safe_load(f) or {}
        else:
            registry = {}

        entry: dict[str, str] = {"github_repo": request.github_repo}
        if request.github_branch:
            entry["github_branch"] = request.github_branch
        if request.github_path_prefix:
            entry["github_path_prefix"] = request.github_path_prefix

        registry.setdefault("services", {})[request.name] = entry
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        with registry_path.open("w") as f:
            yaml.dump(registry, f, default_flow_style=False, sort_keys=False)

    return {"ok": True, "name": request.name}


@app.get("/services/registry")
async def get_service_registry() -> dict:
    """Return service-registry.yml contents as JSON."""
    registry_path = Path(settings.service_registry_path)
    try:
        with registry_path.open() as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/services/health")
async def services_health() -> dict[str, str]:
    """Return health status for every non-infra service based on Prometheus error rates."""
    config_path = Path(settings.prometheus_config_path)
    try:
        with config_path.open() as f:
            prom_cfg: dict = yaml.safe_load(f) or {}
        service_names = [
            j["job_name"]
            for j in prom_cfg.get("scrape_configs", [])
            if "job_name" in j and j["job_name"] not in _INFRA_SERVICES
        ]
    except Exception:
        service_names = []

    if not service_names:
        return {}

    async with httpx.AsyncClient(timeout=5.0) as client:
        statuses = await asyncio.gather(
            *[_compute_service_health(client, svc) for svc in service_names]
        )

    return dict(zip(service_names, statuses))


@app.delete("/services/{name}")
async def delete_service(name: str) -> dict[str, object]:
    """
    Remove a service: deletes its scrape job from prometheus.yml, triggers a
    Prometheus hot-reload, and removes its entry from service-registry.yml.
    """
    if name in _PROTECTED_SERVICES:
        raise HTTPException(
            status_code=409,
            detail=f"Service '{name}' is protected and cannot be removed",
        )

    config_path = Path(settings.prometheus_config_path)
    try:
        with config_path.open() as f:
            prom_cfg: dict = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"prometheus.yml not found at {config_path}")

    scrape_configs = prom_cfg.get("scrape_configs", [])
    new_configs = [j for j in scrape_configs if j.get("job_name") != name]
    if len(new_configs) == len(scrape_configs):
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    prom_cfg["scrape_configs"] = new_configs
    with config_path.open("w") as f:
        yaml.dump(prom_cfg, f, default_flow_style=False, sort_keys=False)

    logger.info("Removed service '%s' from Prometheus config", name)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{settings.prometheus_url}/-/reload")
    except Exception as exc:
        logger.warning(
            "Prometheus reload failed after removal (service removed but reload skipped): %s",
            exc,
        )

    registry_path = Path(settings.service_registry_path)
    if registry_path.exists():
        try:
            with registry_path.open() as f:
                registry: dict = yaml.safe_load(f) or {}
            services = registry.get("services", {})
            if name in services:
                del services[name]
                registry["services"] = services
                with registry_path.open("w") as f:
                    yaml.dump(registry, f, default_flow_style=False, sort_keys=False)
        except Exception as exc:
            logger.warning("Failed to remove '%s' from service registry: %s", name, exc)

    return {"ok": True, "name": name}


@app.put("/services/{name}")
async def update_service_github(
    name: str, request: UpdateServiceGithubRequest
) -> dict[str, object]:
    """
    Update GitHub metadata for an existing service in service-registry.yml.
    The service must already exist in prometheus.yml.
    Sending an empty string for a field removes it from the registry entry.
    Sending null leaves it unchanged.
    """
    config_path = Path(settings.prometheus_config_path)
    try:
        with config_path.open() as f:
            prom_cfg: dict = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"prometheus.yml not found at {config_path}")

    existing = {j["job_name"] for j in prom_cfg.get("scrape_configs", [])}
    if name not in existing:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    registry_path = Path(settings.service_registry_path)
    if registry_path.exists():
        with registry_path.open() as f:
            registry: dict = yaml.safe_load(f) or {}
    else:
        registry = {}

    services = registry.setdefault("services", {})
    entry: dict = dict(services.get(name, {}))

    for field, value in [
        ("github_repo",         request.github_repo),
        ("github_branch",       request.github_branch),
        ("github_path_prefix",  request.github_path_prefix),
    ]:
        if value is None:
            continue
        if value:
            entry[field] = value
        else:
            entry.pop(field, None)

    services[name] = entry
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with registry_path.open("w") as f:
        yaml.dump(registry, f, default_flow_style=False, sort_keys=False)

    return {"ok": True, "name": name, "entry": entry}


@app.post("/rca/followup", response_model=FollowUpResponse)
async def rca_followup(request: FollowUpRequest) -> FollowUpResponse:
    if _aggregator is None:
        raise HTTPException(status_code=503, detail="Aggregator not initialised")
    if not request.incident.rca.performed:
        raise HTTPException(
            status_code=400,
            detail="RCA must be performed before asking follow-up questions",
        )
    return await _aggregator.follow_up(
        incident=request.incident,
        question=request.question,
        history=request.history,
        rca_backend=request.rca_backend,
        llm=request.llm,
    )


@app.get("/config")
async def config_view() -> dict[str, object]:
    """Return non-sensitive configuration for debugging."""
    return {
        "prometheus_url": settings.prometheus_url,
        "loki_url": settings.loki_url,
        "jaeger_url": settings.jaeger_url,
        "default_lookback_minutes": settings.default_lookback_minutes,
        "max_log_lines": settings.max_log_lines,
        "max_traces": settings.max_traces,
    }


def _env_urls(name: str) -> dict[str, str]:
    env = name.lower()
    mapping = {
        "local": {
            "prometheus_url": settings.local_prometheus_url,
            "loki_url": settings.local_loki_url,
            "jaeger_url": settings.local_jaeger_url,
        },
        "staging": {
            "prometheus_url": settings.staging_prometheus_url,
            "loki_url": settings.staging_loki_url,
            "jaeger_url": settings.staging_jaeger_url,
        },
        "production": {
            "prometheus_url": settings.production_prometheus_url,
            "loki_url": settings.production_loki_url,
            "jaeger_url": settings.production_jaeger_url,
        },
    }
    if env not in mapping:
        raise HTTPException(status_code=400, detail="Environment must be local, staging, or production")
    urls = mapping[env]
    missing = [k for k, v in urls.items() if not v]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing URLs for {env}: {', '.join(missing)}",
        )
    return {k: v.rstrip("/") for k, v in urls.items()}


@app.get("/api/environment")
async def get_environment() -> dict[str, object]:
    return {
        "current": settings.environment_name,
        "current_urls": {
            "prometheus_url": settings.prometheus_url,
            "loki_url": settings.loki_url,
            "jaeger_url": settings.jaeger_url,
        },
        "available": ["local", "staging", "production"],
    }


@app.post("/api/environment")
async def set_environment(request: EnvironmentRequest) -> dict[str, object]:
    global _aggregator
    urls = _env_urls(request.name)

    settings.environment_name = request.name.lower()
    settings.prometheus_url = urls["prometheus_url"]
    settings.loki_url = urls["loki_url"]
    settings.jaeger_url = urls["jaeger_url"]

    if _aggregator:
        await _aggregator.close()
    _aggregator = SignalAggregator()

    return {
        "ok": True,
        "environment": settings.environment_name,
        "urls": urls,
    }


@app.get("/api/watchdog")
async def watchdog_status() -> dict[str, object]:
    if _watchdog is None:
        raise HTTPException(status_code=503, detail="Watchdog not initialised")
    return _watchdog.status()


@app.post("/api/watchdog")
async def watchdog_toggle(request: WatchdogRequest) -> dict[str, object]:
    if _watchdog is None:
        raise HTTPException(status_code=503, detail="Watchdog not initialised")
    if request.enabled:
        return await _watchdog.start(
            interval_seconds=request.interval_seconds,
            lookback_minutes=request.lookback_minutes,
            anomaly_threshold=request.anomaly_threshold,
        )
    return await _watchdog.stop()


@app.get("/api/watchdog/alerts")
async def watchdog_alerts() -> dict[str, object]:
    if _watchdog is None:
        raise HTTPException(status_code=503, detail="Watchdog not initialised")
    return {"alerts": _watchdog.get_alerts()}


@app.delete("/api/watchdog/alerts")
async def clear_watchdog_alerts() -> dict[str, object]:
    if _watchdog is None:
        raise HTTPException(status_code=503, detail="Watchdog not initialised")
    _watchdog.clear_alerts()
    return {"ok": True}


# ── Notification config ───────────────────────────────────────────────────────


@app.get("/api/watchdog/notifications")
async def watchdog_notifications_get() -> dict:
    if _watchdog is None:
        raise HTTPException(status_code=503, detail="Watchdog not initialised")
    return _watchdog.get_notification_config()


@app.post("/api/watchdog/notifications")
async def watchdog_notifications_set(payload: dict) -> dict:
    if _watchdog is None:
        raise HTTPException(status_code=503, detail="Watchdog not initialised")
    return _watchdog.update_notification_config(payload)


@app.post("/api/watchdog/notifications/test")
async def watchdog_notifications_test() -> dict:
    if _watchdog is None:
        raise HTTPException(status_code=503, detail="Watchdog not initialised")
    return await _watchdog.test_notification()
