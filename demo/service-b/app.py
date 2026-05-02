"""
service-b — flaky downstream service.

Controlled via environment variables so failure modes can be toggled
from docker-compose without rebuilding the image.

FAILURE_RATE    float 0.0–1.0   fraction of requests that return 500
LATENCY_MS      int             added delay in milliseconds (simulates slow DB)
OOM_ENDPOINT    bool            expose /oom that leaks memory until crash
"""
import logging
import os
import random
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("service-b")

FAILURE_RATE = float(os.getenv("FAILURE_RATE", "0.0"))
LATENCY_MS   = int(os.getenv("LATENCY_MS", "0"))
OOM_ENDPOINT = os.getenv("OOM_ENDPOINT", "false").lower() == "true"

app = FastAPI(title="service-b", description="Flaky downstream service")

from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)

# Simulated in-memory data store (grows without bound when OOM_ENDPOINT enabled)
_leak_store: list[bytes] = []


@app.get("/health")
def health():
    return {"status": "ok", "service": "service-b"}


@app.get("/data")
def get_data():
    """Main data endpoint — subject to injected latency and random failures."""
    if LATENCY_MS:
        time.sleep(LATENCY_MS / 1000)

    if random.random() < FAILURE_RATE:
        logger.error(
            "DatabaseConnectionError: connection pool exhausted after 30s timeout",
            stack_info=False,
        )
        raise HTTPException(
            status_code=500,
            detail="Internal error: database connection pool exhausted",
        )

    logger.info("GET /data ok")
    return {"source": "service-b", "value": random.randint(1, 100)}


@app.get("/slow")
def slow_query():
    """Always-slow endpoint — simulates a missing index on a large table."""
    time.sleep(2.5)
    logger.warning("slow query completed: table scan on orders (no index on created_at)")
    return {"source": "service-b", "note": "slow query result"}


@app.get("/crash")
def crash():
    """Raises an unhandled exception to populate Jaeger with error spans."""
    logger.error(
        "NullPointerException: payment_processor.charge() received None for amount",
    )
    raise RuntimeError("payment_processor.charge() received None for amount")


if OOM_ENDPOINT:
    @app.get("/oom")
    def oom():
        """Leaks 10 MB per call — triggers memory alerts in Prometheus."""
        chunk = b"x" * (10 * 1024 * 1024)
        _leak_store.append(chunk)
        logger.warning("allocated 10 MB, total leaked: %d MB", len(_leak_store) * 10)
        return {"leaked_mb": len(_leak_store) * 10}
