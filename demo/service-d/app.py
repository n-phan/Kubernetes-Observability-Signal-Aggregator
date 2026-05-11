"""
service-d — inventory service.

Demonstrates a two-frame call chain database exception. When DB_FAIL is enabled,
a GET /stock/{item_id} walks get_stock() → lookup_inventory() → query_database()
and raises DatabaseConnectionError, simulating a refused PostgreSQL connection.
The full traceback is logged to stdout for stack-frame extraction.

DB_FAIL                       bool   "1"/"true" makes every query_database() call fail
OTEL_EXPORTER_OTLP_ENDPOINT   str    OTLP gRPC endpoint for traces (default jaeger:4317)
OTEL_SERVICE_NAME             str    service.name resource attribute (default service-d)
"""
import logging
import os
import random
import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("service-d")


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


DB_FAIL = _truthy(os.getenv("DB_FAIL", "0"))
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "service-d")

app = FastAPI(title="service-d", description="Inventory service")

# Prometheus metrics
Instrumentator().instrument(app).expose(app)
inventory_lookup_errors_total = Counter(
    "inventory_lookup_errors_total", "Total number of failed inventory lookups"
)

# OpenTelemetry — sends traces to Jaeger
_resource = Resource.create({"service.name": OTEL_SERVICE_NAME})
_provider = TracerProvider(resource=_resource)
_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(_provider)
FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider)

_tracer = trace.get_tracer("service-d")

_DB_HOST = "inventory-db.internal"
_DB_PORT = 5432


class DatabaseConnectionError(Exception):
    """Raised when the inventory database refuses the connection."""


# ── Inventory call chain ───────────────────────────────────────────────────


def query_database(item_id: str) -> int:
    """Query the inventory table for an item's stock level. Fails when DB_FAIL is set."""
    with _tracer.start_as_current_span("query_database") as span:
        span.set_attribute("db.system", "postgresql")
        span.set_attribute("db.statement", "SELECT qty FROM inventory WHERE item_id = %s")
        if DB_FAIL:
            err = DatabaseConnectionError(
                f"could not connect to server: Connection refused ({_DB_HOST}:{_DB_PORT})"
            )
            span.set_status(Status(StatusCode.ERROR, str(err)))
            span.record_exception(err)
            raise err
        return random.randint(0, 250)


def lookup_inventory(item_id: str) -> dict:
    """Look up an item's stock level via the database layer."""
    with _tracer.start_as_current_span("lookup_inventory") as span:
        span.set_attribute("inventory.item_id", item_id)
        qty = query_database(item_id)
        return {"item_id": item_id, "in_stock": qty}


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok", "service": "service-d"}


class ConfigureRequest(BaseModel):
    db_fail: bool | None = None


@app.get("/config")
def get_config():
    return {"db_fail": DB_FAIL}


@app.post("/configure")
def configure(req: ConfigureRequest):
    global DB_FAIL
    if req.db_fail is not None:
        DB_FAIL = req.db_fail
    logger.info("Runtime config updated: DB_FAIL=%s", DB_FAIL)
    return {"db_fail": DB_FAIL}


@app.post("/reset")
def reset_config():
    global DB_FAIL
    DB_FAIL = False
    logger.info("Runtime config reset to defaults")
    return {"db_fail": DB_FAIL}


@app.get("/stock/{item_id}")
def get_stock(item_id: str):
    """Return the stock level for an item. Raises DatabaseConnectionError when DB_FAIL is set."""
    try:
        result = lookup_inventory(item_id)
    except DatabaseConnectionError as exc:
        inventory_lookup_errors_total.inc()
        logger.error("Inventory lookup failed:\n%s", traceback.format_exc())
        raise HTTPException(status_code=503, detail=f"DatabaseConnectionError: {exc}")

    logger.info("GET /stock/%s ok: %d in stock", item_id, result["in_stock"])
    return {"source": "service-d", **result}
