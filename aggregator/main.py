import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from aggregator.clients.prometheus import PrometheusClient
from aggregator.config import settings
from aggregator.core.aggregator import SignalAggregator
from aggregator.demo import router as demo_router
from aggregator.models.query import QueryRequest
from aggregator.models.result import UnifiedResult

_INFRA_SERVICES: frozenset[str] = frozenset(
    {"prometheus", "loki", "jaeger", "promtail", "aggregator"}
)

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)

_aggregator: SignalAggregator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _aggregator
    _aggregator = SignalAggregator()
    logger.info("Signal aggregator started")
    yield
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
    """Return monitored service names from Prometheus, excluding infrastructure jobs."""
    client = PrometheusClient()
    try:
        jobs = await client.get_label_values("job")
        return [j for j in jobs if j not in _INFRA_SERVICES]
    except Exception as exc:
        logger.warning("Failed to fetch service list from Prometheus: %s", exc)
        return []
    finally:
        await client.close()


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