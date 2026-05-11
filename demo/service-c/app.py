"""
service-c — payment processor.

Demonstrates a three-frame call chain exception. When GATEWAY_FAIL is enabled,
a POST /pay walks pay() → process_payment() → validate_card() → charge_gateway()
and raises GatewayTimeoutError at the bottom of the chain. The full traceback is
logged to stdout so the aggregator can extract stack frames and link them to GitHub.

GATEWAY_FAIL                  bool   "1"/"true" makes every charge_gateway() call fail
OTEL_EXPORTER_OTLP_ENDPOINT   str    OTLP gRPC endpoint for traces (default jaeger:4317)
OTEL_SERVICE_NAME             str    service.name resource attribute (default service-c)
"""
import logging
import os
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
logger = logging.getLogger("service-c")


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


GATEWAY_FAIL = _truthy(os.getenv("GATEWAY_FAIL", "0"))
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "service-c")

app = FastAPI(title="service-c", description="Payment processor")

# Prometheus metrics
Instrumentator().instrument(app).expose(app)
payment_errors_total = Counter(
    "payment_errors_total", "Total number of failed payment attempts"
)

# OpenTelemetry — sends traces to Jaeger
_resource = Resource.create({"service.name": OTEL_SERVICE_NAME})
_provider = TracerProvider(resource=_resource)
_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(_provider)
FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider)

_tracer = trace.get_tracer("service-c")


class GatewayTimeoutError(Exception):
    """Raised when the upstream payment gateway does not respond in time."""


# ── Payment call chain ─────────────────────────────────────────────────────


def charge_gateway(amount: float) -> dict:
    """Call the external payment gateway. Fails when GATEWAY_FAIL is set."""
    with _tracer.start_as_current_span("charge_gateway") as span:
        span.set_attribute("payment.amount", amount)
        if GATEWAY_FAIL:
            err = GatewayTimeoutError(
                f"payment gateway did not respond within 30s while charging ${amount:.2f}"
            )
            span.set_status(Status(StatusCode.ERROR, str(err)))
            span.record_exception(err)
            raise err
        return {"gateway_txn_id": "txn_ok", "amount": amount}


def validate_card(card_number: str) -> None:
    """Validate the card number, then hand off to the gateway charge step."""
    with _tracer.start_as_current_span("validate_card") as span:
        span.set_attribute("payment.card_last4", card_number[-4:])
        if len(card_number) < 12:
            raise ValueError("card_number must be at least 12 digits")


def process_payment(amount: float, card_number: str) -> dict:
    """Top of the payment chain: validate the card, then charge the gateway."""
    with _tracer.start_as_current_span("process_payment") as span:
        span.set_attribute("payment.amount", amount)
        validate_card(card_number)
        return charge_gateway(amount)


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok", "service": "service-c"}


class ConfigureRequest(BaseModel):
    gateway_fail: bool | None = None


@app.get("/config")
def get_config():
    return {"gateway_fail": GATEWAY_FAIL}


@app.post("/configure")
def configure(req: ConfigureRequest):
    global GATEWAY_FAIL
    if req.gateway_fail is not None:
        GATEWAY_FAIL = req.gateway_fail
    logger.info("Runtime config updated: GATEWAY_FAIL=%s", GATEWAY_FAIL)
    return {"gateway_fail": GATEWAY_FAIL}


@app.post("/reset")
def reset_config():
    global GATEWAY_FAIL
    GATEWAY_FAIL = False
    logger.info("Runtime config reset to defaults")
    return {"gateway_fail": GATEWAY_FAIL}


class PayRequest(BaseModel):
    amount: float
    card_number: str


@app.post("/pay")
def pay(req: PayRequest):
    """Process a payment. Raises GatewayTimeoutError when GATEWAY_FAIL is set."""
    try:
        result = process_payment(req.amount, req.card_number)
    except GatewayTimeoutError as exc:
        payment_errors_total.inc()
        logger.error("Unhandled exception in payment processor:\n%s", traceback.format_exc())
        raise HTTPException(status_code=504, detail=f"GatewayTimeoutError: {exc}")
    except ValueError as exc:
        payment_errors_total.inc()
        logger.error("Invalid payment request: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info("POST /pay ok: charged $%.2f", req.amount)
    return {"status": "charged", "amount": req.amount, "gateway": result}
