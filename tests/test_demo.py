from __future__ import annotations

import json
from datetime import datetime

import pytest

from aggregator import demo as demo_module


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(
        self,
        url: str,
        json: dict | None = None,
        timeout: float | None = None,
    ) -> _FakeResponse:
        if url.endswith("/configure"):
            return _FakeResponse(
                payload={
                    "failure_rate": json["failure_rate"] if json else 0.0,
                    "latency_ms": json["latency_ms"] if json else 0,
                }
            )
        return _FakeResponse()

    async def get(self, url: str, timeout: float | None = None) -> _FakeResponse:
        return _FakeResponse(status_code=200, payload={"ok": True})


@pytest.mark.asyncio
async def test_demo_done_event_includes_query_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        demo_module,
        "SCENARIOS",
        {
            "tiny": {
                "label": "Tiny",
                "config": {"failure_rate": 0.7, "latency_ms": 0},
                "target_url": "{service_a}/api/data",
                "count": 1,
                "query_target": "service-a",
            }
        },
    )

    response = await demo_module.run_scenario("tiny")
    done_event = None
    async for chunk in response.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        event = json.loads(text.removeprefix("data: ").strip())
        if event["type"] == "done":
            done_event = event

    assert done_event is not None
    assert done_event["query_target"] == "service-a"

    window_start = datetime.fromisoformat(done_event["window_start"])
    window_end = datetime.fromisoformat(done_event["window_end"])
    assert window_start.tzinfo is not None
    assert window_end.tzinfo is not None
    assert window_start <= window_end
