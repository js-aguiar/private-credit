"""Playwright headless-browser fallback for JavaScript SPAs.

Used only when the API-first path is unavailable. Playwright is an optional dependency
(imported lazily) so the module can be imported even in environments without it.
"""

from __future__ import annotations

import json
from typing import Any

from .config import ScraperConfig
from .logging_config import get_logger

logger = get_logger(__name__)


class BrowserUnavailableError(RuntimeError):
    """Raised when the browser fallback is requested but Playwright is not installed."""


class BrowserFetcher:
    """Render SPA pages and/or capture their XHR/fetch JSON responses."""

    def __init__(self, config: ScraperConfig):
        self.config = config
        self._playwright = None
        self._browser = None
        self._context = None

    def __enter__(self) -> "BrowserFetcher":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise BrowserUnavailableError(
                "Playwright is not installed; install the 'browser' extra."
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        self._context = self._browser.new_context(
            user_agent=self.config.user_agent,
            locale="pt-BR",
        )
        self._context.set_default_timeout(self.config.request_timeout_seconds * 1000)
        return self

    def __exit__(self, *exc) -> None:
        for closable in (self._context, self._browser, self._playwright):
            try:
                if closable is not None:
                    closable.close() if hasattr(closable, "close") else closable.stop()
            except Exception:  # pragma: no cover
                pass

    def render(
        self,
        url: str,
        wait_selector: str | None = None,
        wait_until: str = "networkidle",
    ) -> str:
        """Navigate to ``url`` and return the fully rendered HTML."""
        page = self._context.new_page()
        try:
            page.goto(url, wait_until=wait_until)
            if wait_selector:
                page.wait_for_selector(wait_selector)
            return page.content()
        finally:
            page.close()

    def capture_json(
        self,
        url: str,
        url_contains: str,
        wait_selector: str | None = None,
        trigger: Any = None,
    ) -> list[Any]:
        """Load ``url`` and return JSON bodies of responses whose URL contains a marker.

        Useful for SPAs that fetch their data from an internal API: we let the page make
        its own authenticated requests and simply read the JSON off the wire.
        """
        page = self._context.new_page()
        captured: list[Any] = []

        def _on_response(response) -> None:
            if url_contains in response.url:
                try:
                    captured.append(response.json())
                except Exception:
                    try:
                        captured.append(json.loads(response.text()))
                    except Exception:
                        pass

        page.on("response", _on_response)
        try:
            page.goto(url, wait_until="networkidle")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector)
                except Exception:
                    pass
            if callable(trigger):
                trigger(page)
                page.wait_for_load_state("networkidle")
            return captured
        finally:
            page.close()
