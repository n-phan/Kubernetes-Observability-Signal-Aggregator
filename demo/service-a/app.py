"""
service-a — upstream API.

Calls service-b for data. Demonstrates how upstream services propagate
and (optionally) absorb downstream failures.

ENABLE_RETRY            bool    retry failed requests to service-b (up to 3×)
ENABLE_CIRCUIT_BREAKER  bool    stop calling service-b after 5 consecutive errors
SERVICE_B_URL           str     base URL of service-b
"""
import logging
import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("service-a")

SERVICE_B_URL          = os.getenv("SERVICE_B_URL", "http://service-b:8002")
ENABLE_RETRY           = os.getenv("ENABLE_RETRY", "false").lower() == "true"
ENABLE_CIRCUIT_BREAKER = os.getenv("ENABLE_CIRCUIT_BREAKER", "false").lower() == "true"

MAX_RETRIES   = 3
CB_THRESHOLD  = 5     # consecutive errors before opening the circuit
CB_RESET_SECS = 30   # seconds before attempting to close the circuit

app = FastAPI(title="service-a", description="Upstream API")

# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# OpenTelemetry — sends traces to Jaeger
_resource = Resource.create({"service.name": "service-a"})
_provider = TracerProvider(resource=_resource)
_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://jaeger:4317", insecure=True))
)
trace.set_tracer_provider(_provider)
FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider)

# Instrument httpx so outgoing calls to service-b carry trace context headers.
# This links service-a and service-b spans into a single distributed trace.
HTTPXClientInstrumentor().instrument()

# --- Simple in-process circuit breaker state ---
_cb_error_count  = 0
_cb_opened_at: float | None = None


def _circuit_open() -> bool:
    global _cb_opened_at
    if _cb_opened_at is None:
        return False
    if time.monotonic() - _cb_opened_at > CB_RESET_SECS:
        logger.info("circuit breaker: attempting reset")
        _cb_opened_at = None
        return False
    return True


def _record_success():
    global _cb_error_count, _cb_opened_at
    _cb_error_count = 0
    _cb_opened_at   = None


def _record_error():
    global _cb_error_count, _cb_opened_at
    _cb_error_count += 1
    if ENABLE_CIRCUIT_BREAKER and _cb_error_count >= CB_THRESHOLD:
        if _cb_opened_at is None:
            _cb_opened_at = time.monotonic()
            logger.error(
                "circuit breaker OPEN after %d consecutive errors from service-b",
                _cb_error_count,
            )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "service-a",
        "circuit_open": _circuit_open(),
        "retry_enabled": ENABLE_RETRY,
        "circuit_breaker_enabled": ENABLE_CIRCUIT_BREAKER,
    }


@app.get("/api/data")
def api_data():
    """Fetch data from service-b, with optional retry and circuit breaker."""
    if ENABLE_CIRCUIT_BREAKER and _circuit_open():
        logger.warning("circuit breaker is OPEN — returning 503 without calling service-b")
        raise HTTPException(status_code=503, detail="Circuit breaker open — service-b unavailable")

    attempts = MAX_RETRIES if ENABLE_RETRY else 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.get(f"{SERVICE_B_URL}/data", timeout=5.0)
            resp.raise_for_status()
            _record_success()
            data = resp.json()
            logger.info("GET /api/data ok (attempt %d)", attempt)
            return {"source": "service-a", "upstream": data}

        except httpx.HTTPStatusError as exc:
            last_error = exc
            _record_error()
            logger.error(
                "service-b returned %d on attempt %d/%d",
                exc.response.status_code, attempt, attempts,
            )
        except httpx.RequestError as exc:
            last_error = exc
            _record_error()
            logger.error("service-b unreachable on attempt %d/%d: %s", attempt, attempts, exc)

        if attempt < attempts:
            backoff = 0.2 * (2 ** (attempt - 1))
            logger.info("retrying in %.1fs", backoff)
            time.sleep(backoff)

    raise HTTPException(
        status_code=502,
        detail=f"service-b failed after {attempts} attempt(s): {last_error}",
    )


@app.get("/api/slow")
def api_slow():
    """Proxies to service-b /slow — useful for generating high-latency traces."""
    try:
        resp = httpx.get(f"{SERVICE_B_URL}/slow", timeout=10.0)
        resp.raise_for_status()
        return {"source": "service-a", "upstream": resp.json()}
    except Exception as exc:
        logger.error("slow request to service-b failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))