"""Polite, resilient HTTP client.

Enforces a per-request minimum interval (delay + jitter and a hard requests-per-minute
cap), retries transient failures (429 / 5xx / network errors) with exponential backoff,
and always identifies itself with a descriptive User-Agent. Concurrency is intentionally
1 (a single client instance is used serially per run).
"""

from __future__ import annotations

import random
import time

import httpx

from .config import ScraperConfig
from .logging_config import get_logger

logger = get_logger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}


class PoliteClient:
    """A thin wrapper around ``httpx.Client`` adding delay, rate limiting, and retries."""

    def __init__(self, config: ScraperConfig, base_url: str = "", headers: dict | None = None):
        self.config = config
        default_headers = {
            "User-Agent": config.user_agent,
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        }
        if headers:
            default_headers.update(headers)
        self._client = httpx.Client(
            base_url=base_url,
            headers=default_headers,
            timeout=config.request_timeout_seconds,
            follow_redirects=True,
        )
        # Minimum spacing between requests: the larger of the configured delay and the
        # rate cap (60 / rpm). This is the "sufficient" pause to avoid overloading sites.
        rpm_interval = 60.0 / config.max_requests_per_minute if config.max_requests_per_minute else 0
        self._min_interval = max(config.request_delay_seconds, rpm_interval)
        self._last_request_ts: float | None = None

    # -- context manager ----------------------------------------------------------
    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- politeness ---------------------------------------------------------------
    def _throttle(self) -> None:
        if self._last_request_ts is not None:
            elapsed = time.monotonic() - self._last_request_ts
            wait = self._min_interval - elapsed
            if wait > 0:
                time.sleep(wait)
        # Add jitter on top so request timing is not perfectly periodic.
        if self.config.request_jitter_seconds > 0:
            time.sleep(random.uniform(0, self.config.request_jitter_seconds))
        self._last_request_ts = time.monotonic()

    # -- requests -----------------------------------------------------------------
    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        attempts = self.config.max_retries + 1
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            self._throttle()
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "http_network_error",
                    extra={"url": url, "attempt": attempt, "error": str(exc)},
                )
            else:
                if response.status_code in _RETRY_STATUS and attempt < attempts:
                    logger.warning(
                        "http_retry_status",
                        extra={
                            "url": url,
                            "status": response.status_code,
                            "attempt": attempt,
                        },
                    )
                else:
                    return response
            # Backoff before the next attempt.
            if attempt < attempts:
                backoff = self.config.backoff_base_seconds * (2 ** (attempt - 1))
                time.sleep(backoff + random.uniform(0, 1))
        if last_exc is not None:
            raise last_exc
        raise httpx.HTTPError(f"Exhausted retries for {url}")

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def get_json(self, url: str, **kwargs) -> dict | list:
        response = self.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str, **kwargs) -> str:
        response = self.get(url, **kwargs)
        response.raise_for_status()
        return response.text
