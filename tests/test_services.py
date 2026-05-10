"""
Tests for the service registration endpoints:
  POST /services/test    — probe a metrics URL
  POST /services/register — append a scrape target to prometheus.yml

All external HTTP calls are mocked via pytest-httpx; no running containers required.
File I/O uses pytest's tmp_path fixture to avoid touching the real prometheus.yml.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from aggregator.config import settings
from aggregator.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_PROMETHEUS_YML = textwrap.dedent("""\
    global:
      scrape_interval: 15s

    scrape_configs:
      - job_name: service-a
        static_configs:
          - targets:
              - 'service-a:8001'
        metrics_path: /metrics
      - job_name: service-b
        static_configs:
          - targets:
              - 'service-b:8002'
        metrics_path: /metrics
""")


def _write_prom_yml(path: Path, content: str = MINIMAL_PROMETHEUS_YML) -> Path:
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def prom_yml(tmp_path: Path) -> Path:
    """A temporary prometheus.yml pre-populated with service-a and service-b."""
    return _write_prom_yml(tmp_path / "prometheus.yml")


@pytest.fixture(autouse=True)
def _patch_settings(prom_yml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect config paths to temp files so tests never touch real infra/."""
    monkeypatch.setattr(settings, "prometheus_config_path", str(prom_yml))
    monkeypatch.setattr(settings, "service_registry_path", str(tmp_path / "service-registry.yml"))


# ---------------------------------------------------------------------------
# POST /services/test
# ---------------------------------------------------------------------------

class TestTestEndpoint:

    async def test_returns_ok_true_for_reachable_url(self, httpx_mock) -> None:
        httpx_mock.add_response(
            method="GET",
            url="http://my-service:8003/metrics",
            status_code=200,
            text="# prometheus metrics",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/services/test", json={"url": "http://my-service:8003/metrics"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "error" not in body

    async def test_returns_ok_false_for_http_error(self, httpx_mock) -> None:
        httpx_mock.add_response(
            method="GET",
            url="http://bad-service:9999/metrics",
            status_code=404,
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/services/test", json={"url": "http://bad-service:9999/metrics"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "404" in body["error"]

    async def test_returns_ok_false_for_connection_error(self, httpx_mock) -> None:
        import httpx as _httpx
        httpx_mock.add_exception(
            _httpx.ConnectError("Connection refused"),
            url="http://unreachable:1234/metrics",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/services/test", json={"url": "http://unreachable:1234/metrics"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]  # some error message is present


# ---------------------------------------------------------------------------
# POST /services/register
# ---------------------------------------------------------------------------

class TestRegisterEndpoint:

    def _mock_prom_reload(self, httpx_mock) -> None:
        """Add a permissive mock for the Prometheus hot-reload call."""
        httpx_mock.add_response(
            method="POST",
            url=f"{settings.prometheus_url}/-/reload",
            status_code=200,
        )

    async def test_appends_new_scrape_target(self, prom_yml: Path, httpx_mock) -> None:
        self._mock_prom_reload(httpx_mock)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/services/register", json={
                "name": "my-service",
                "metrics_url": "http://my-service:8003/metrics",
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["name"] == "my-service"

        # Verify the YAML file was updated
        data = yaml.safe_load(prom_yml.read_text())
        job_names = [j["job_name"] for j in data["scrape_configs"]]
        assert "my-service" in job_names

        new_job = next(j for j in data["scrape_configs"] if j["job_name"] == "my-service")
        assert new_job["static_configs"][0]["targets"] == ["my-service:8003"]
        assert new_job["metrics_path"] == "/metrics"

    async def test_returns_409_if_job_already_exists(self, httpx_mock) -> None:
        # service-a is already in MINIMAL_PROMETHEUS_YML
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/services/register", json={
                "name": "service-a",
                "metrics_url": "http://service-a:8001/metrics",
            })

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    async def test_writes_service_registry_when_github_provided(
        self, prom_yml: Path, tmp_path: Path, httpx_mock
    ) -> None:
        self._mock_prom_reload(httpx_mock)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/services/register", json={
                "name": "svc-with-github",
                "metrics_url": "http://svc-with-github:8004/metrics",
                "github_repo": "my-org/svc-with-github",
                "github_branch": "main",
                "github_path_prefix": "src",
            })

        assert resp.status_code == 200

        registry_path = tmp_path / "service-registry.yml"
        assert registry_path.exists(), "service-registry.yml should have been created"

        registry = yaml.safe_load(registry_path.read_text())
        svc = registry["services"]["svc-with-github"]
        assert svc["github_repo"] == "my-org/svc-with-github"
        assert svc["github_branch"] == "main"
        assert svc["github_path_prefix"] == "src"

    async def test_skips_registry_when_no_github_fields(
        self, prom_yml: Path, tmp_path: Path, httpx_mock
    ) -> None:
        self._mock_prom_reload(httpx_mock)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/services/register", json={
                "name": "no-github-svc",
                "metrics_url": "http://no-github-svc:8005/metrics",
            })

        registry_path = tmp_path / "service-registry.yml"
        assert not registry_path.exists(), "service-registry.yml should NOT be created without GitHub fields"

    async def test_preserves_existing_jobs_in_prometheus_yml(
        self, prom_yml: Path, httpx_mock
    ) -> None:
        self._mock_prom_reload(httpx_mock)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/services/register", json={
                "name": "new-svc",
                "metrics_url": "http://new-svc:9000/metrics",
            })

        data = yaml.safe_load(prom_yml.read_text())
        job_names = [j["job_name"] for j in data["scrape_configs"]]
        # Original jobs must still be present
        assert "service-a" in job_names
        assert "service-b" in job_names
        assert "new-svc" in job_names
