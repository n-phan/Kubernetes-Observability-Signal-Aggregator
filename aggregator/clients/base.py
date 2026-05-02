import logging
import time
from typing import Any

import httpx

from aggregator.config import settings

logger = logging.getLogger(__name__)


class ObservabilityClientError(Exception):
    """Raised when a backend query fails."""

    def __init__(self, backend: str, message: str, status_code: int | None = None) -> None:
        self.backend = backend
        self.status_code = status_code
        super().__init__(f"[{backend}] {message}")


class BaseObservabilityClient:
    """
    Thin async HTTP wrapper shared by all backend clients.

    Provides:
    - Shared httpx.AsyncClient with configured timeout
    - Automatic retry with exponential backoff
    - Consistent error wrapping
    - Query timing
    """

    backend_name: str = "unknown"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BaseObservabilityClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], float]:
        """
        Perform a GET request with retry logic.

        Returns (response_json, duration_ms).
        Raises ObservabilityClientError on failure.
        """
        last_exc: Exception | None = None
        t0 = time.monotonic()

        for attempt in range(1, settings.http_max_retries + 1):
            try:
                resp = await self._client.get(path, params=params)
                duration_ms = (time.monotonic() - t0) * 1000

                if resp.status_code >= 400:
                    raise ObservabilityClientError(
                        self.backend_name,
                        f"HTTP {resp.status_code}: {resp.text[:200]}",
                        status_code=resp.status_code,
                    )

                return resp.json(), duration_ms

            except ObservabilityClientError:
                raise
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "[%s] timeout on attempt %d/%d",
                    self.backend_name,
                    attempt,
                    settings.http_max_retries,
                )
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning(
                    "[%s] request error on attempt %d/%d: %s",
                    self.backend_name,
                    attempt,
                    settings.http_max_retries,
                    exc,
                )

        duration_ms = (time.monotonic() - t0) * 1000
        raise ObservabilityClientError(
            self.backend_name,
            f"Failed after {settings.http_max_retries} attempts: {last_exc}",
        )
