"""
Demo runner router.

Exposes endpoints that the frontend Demo panel calls to:
  - Read the current service-b configuration   GET  /demo/config
  - Apply a pre-defined failure scenario        POST /demo/run/{scenario}
  - Reset service-b to clean defaults           POST /demo/reset

Scenarios stream their output as Server-Sent Events (SSE) so the browser
can display each request result as it happens rather than waiting for the
whole run to finish.

SSE event shapes:
  {"type": "status",  "message": "..."}
  {"type": "config",  "failure_rate": 0.7, "latency_ms": 0}
  {"type": "request", "index": 1, "total": 30, "status_code": 200, "elapsed_ms": 45}
  {"type": "done",    "success": 9, "failed": 21, "total": 30, "query_target": "service-a"}
  {"type": "error",   "message": "..."}
"""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from aggregator.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["demo"])

# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------
# Each scenario describes what to configure on service-b and how many requests
# to fire (and against which URL). "config" is None for crash — /crash always
# works and no failure injection is needed.

SCENARIOS: dict[str, dict] = {
    "healthy": {
        "label":        "Normal operation",
        "config":       {"failure_rate": 0.0, "latency_ms": 0},
        "target_url":   "{service_a}/api/data",
        "count":        20,
        "query_target": "service-a",
    },
    "errors": {
        "label":        "High error rate",
        "config":       {"failure_rate": 0.7, "latency_ms": 0},
        "target_url":   "{service_a}/api/data",
        "count":        30,
        "query_target": "service-a",
    },
    "slow": {
        "label":        "Latency spike",
        "config":       {"failure_rate": 0.0, "latency_ms": 2000},
        "target_url":   "{service_b}/data",
        "count":        10,
        "query_target": "service-b",
    },
    "crash": {
        "label":        "Payment crash",
        "config":       None,           # /crash endpoint is always enabled
        "target_url":   "{service_b}/crash",
        "count":        15,
        "query_target": "service-b",
    },
    "payment_crash": {
        "label":           "Gateway timeout",
        "reset_service_b": False,                   # scenario uses service-c, not service-b
        "reset_url":       "{service_c}/reset",     # reset service-c to clean state first
        "configure_url":   "{service_c}/configure", # then enable the failure flag
        "config":          {"gateway_fail": True},
        "target_url":      "{service_c}/pay",
        "method":          "POST",
        "body":            {"amount": 99.99, "card_number": "4242424242424242"},
        "count":           15,
        "query_target":    "service-c",
    },
    "inventory_crash": {
        "label":           "DB connection lost",
        "reset_service_b": False,                   # scenario uses service-d, not service-b
        "reset_url":       "{service_d}/reset",     # reset service-d to clean state first
        "configure_url":   "{service_d}/configure", # then enable the failure flag
        "config":          {"db_fail": True},
        "target_url":      "{service_d}/stock/widget-001",
        "count":           15,
        "query_target":    "service-d",
    },
}

# Per-request timeout for demo traffic. Must be longer than LATENCY_MS (2000ms)
# plus network overhead, so 5 seconds is safe for all three scenarios.
_REQUEST_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(event: dict) -> str:
    """Format a dict as a single SSE data line."""
    return f"data: {json.dumps(event)}\n\n"


async def _configure(client: httpx.AsyncClient, url: str, config: dict) -> dict:
    """POST config to a service's /configure endpoint and return the response body."""
    resp = await client.post(url, json=config, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


async def _fire_request(
    client: httpx.AsyncClient,
    url: str,
    method: str = "GET",
    body: dict | None = None,
) -> tuple[int, float]:
    """
    Fire a GET or POST request and return (http_status_code, elapsed_ms).
    Treats connection errors as status 0 so the stream keeps going.
    """
    t0 = time.monotonic()
    try:
        if method == "POST":
            resp = await client.post(url, json=body, timeout=_REQUEST_TIMEOUT)
        else:
            resp = await client.get(url, timeout=_REQUEST_TIMEOUT)
        return resp.status_code, (time.monotonic() - t0) * 1000
    except httpx.TimeoutException:
        return 0, (time.monotonic() - t0) * 1000
    except Exception as exc:
        logger.warning("Demo request to %s failed: %s", url, exc)
        return 0, (time.monotonic() - t0) * 1000


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/config")
async def demo_config() -> dict:
    """Return the current service-b runtime configuration."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{settings.demo_service_b_url}/config",
                timeout=3.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Could not fetch service-b config: %s", exc)
            return {"failure_rate": None, "latency_ms": None, "error": str(exc)}


@router.post("/reset")
async def demo_reset() -> dict:
    """Reset service-b to its default configuration (no failure injection)."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{settings.demo_service_b_url}/reset",
                timeout=3.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Could not reset service-b: %s", exc)
            return {"error": str(exc)}


@router.post("/run/{scenario}")
async def run_scenario(scenario: str) -> StreamingResponse:
    """
    Run a named demo scenario and stream progress as SSE.

    Each yielded event is a JSON object on a `data:` line, terminated by
    a blank line (standard SSE format). The browser reads these as they
    arrive and updates the Demo panel output in real time.
    """
    if scenario not in SCENARIOS:
        names = ", ".join(SCENARIOS)
        return StreamingResponse(
            iter([_sse({"type": "error", "message": f"Unknown scenario '{scenario}'. Valid: {names}"})]),
            media_type="text/event-stream",
        )

    spec = SCENARIOS[scenario]
    service_a = settings.demo_service_a_url
    service_b = settings.demo_service_b_url
    service_c = settings.demo_service_c_url
    service_d = settings.demo_service_d_url

    _fmt = dict(service_a=service_a, service_b=service_b,
                service_c=service_c, service_d=service_d)

    target_url    = spec["target_url"].format(**_fmt)
    configure_url = spec.get("configure_url", "{service_b}/configure").format(**_fmt)
    reset_url     = spec.get("reset_url",     "{service_b}/reset").format(**_fmt)
    req_method    = spec.get("method", "GET")
    req_body      = spec.get("body")

    async def generate() -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient() as client:

            # Reset the target service to clear any prior state.
            # For scenarios that use service-b, the spec key `reset_service_b`
            # controls this; for service-c/d the reset_url points at those services.
            reset_label = "service-b" if spec.get("reset_service_b", True) else spec.get("query_target", "service")
            yield _sse({"type": "status", "message": f"Resetting {reset_label} to clean state..."})
            try:
                await client.post(reset_url, timeout=3.0)
            except Exception as exc:
                yield _sse({"type": "error", "message": f"Could not reset {reset_label}: {exc}"})
                return

            # Step 1: configure the target service (if this scenario needs it)
            if spec.get("config") is not None:
                cfg = spec["config"]
                yield _sse({"type": "status", "message": f"Configuring {reset_label}..."})
                try:
                    applied = await _configure(client, configure_url, cfg)
                    # Emit the legacy "config" event only when it's service-b (the
                    # badge strip in the frontend expects failure_rate + latency_ms).
                    if "failure_rate" in applied and "latency_ms" in applied:
                        yield _sse({
                            "type": "config",
                            "failure_rate": applied["failure_rate"],
                            "latency_ms":   applied["latency_ms"],
                        })
                    else:
                        parts = ", ".join(f"{k}={v}" for k, v in applied.items())
                        yield _sse({"type": "status", "message": f"✓ Configured: {parts}"})
                except Exception as exc:
                    yield _sse({"type": "error", "message": f"Could not configure {reset_label}: {exc}"})
                    return
            else:
                yield _sse({"type": "status", "message": "No configuration change needed."})

            # Step 2: fire the requests one at a time, streaming each result
            total = spec["count"]
            yield _sse({"type": "status", "message": f"Sending {total} requests to {target_url}..."})

            success = 0
            failed = 0

            for i in range(1, total + 1):
                status_code, elapsed_ms = await _fire_request(client, target_url, req_method, req_body)
                ok = 200 <= status_code < 300

                if ok:
                    success += 1
                else:
                    failed += 1

                yield _sse({
                    "type":        "request",
                    "index":       i,
                    "total":       total,
                    "status_code": status_code,
                    "elapsed_ms":  round(elapsed_ms),
                    "ok":          ok,
                })

            # Step 3: emit the summary
            yield _sse({
                "type":         "done",
                "success":      success,
                "failed":       failed,
                "total":        total,
                "query_target": spec["query_target"],
            })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",   # prevent nginx from buffering the stream
        },
    )
