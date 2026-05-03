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
import traceback

from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("service-b")

FAILURE_RATE = float(os.getenv("FAILURE_RATE", "0.0"))
LATENCY_MS   = int(os.getenv("LATENCY_MS", "0"))
OOM_ENDPOINT = os.getenv("OOM_ENDPOINT", "false").lower() == "true"

app = FastAPI(title="service-b", description="Flaky downstream service")

# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# OpenTelemetry — sends traces to Jaeger
_resource = Resource.create({"service.name": "service-b"})
_provider = TracerProvider(resource=_resource)
_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://jaeger:4317", insecure=True))
)
trace.set_tracer_provider(_provider)
FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider)

# Tracer for manual spans
_tracer = trace.get_tracer("service-b")

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
    """Raises an unhandled exception with a full stack trace logged to stderr."""
    try:
        _process_payment(amount=None)
    except Exception:
        logger.error("Unhandled exception in payment processor:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail="payment_processor.charge() received None for amount")


def _process_payment(amount: float):
    """Simulate a payment processing call that fails on None input."""
    with _tracer.start_as_current_span("_process_payment") as span:
        span.set_attribute("payment.amount", str(amount))
        if amount is None:
            err = ValueError("payment_processor.charge() received None for amount")
            span.set_status(Status(StatusCode.ERROR, str(err)))
            span.record_exception(err)
            raise err
        return {"status": "ok", "amount": amount}


if OOM_ENDPOINT:
    @app.get("/oom")
    def oom():
        """Leaks 10 MB per call — triggers memory alerts in Prometheus."""
        chunk = b"x" * (10 * 1024 * 1024)
        _leak_store.append(chunk)
        logger.warning("allocated 10 MB, total leaked: %d MB", len(_leak_store) * 10)
        return {"leaked_mb": len(_leak_store) * 10}