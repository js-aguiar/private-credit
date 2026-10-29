"""ANBIMA Data características scraper via Playwright response interception.

The public SPA requires a reCAPTCHA token for BFF calls. We load the
características portal URL in Chromium, let the site mint the token, and read
the JSON off the wire (one response per asset — no pagination).
"""

from __future__ import annotations

import csv
import random
import time
from pathlib import Path
from typing import Any, Iterable

from shared.browser import BrowserUnavailableError
from shared.config import ScraperConfig
from shared.db import ensure_schema, session_scope
from shared.logging_config import get_logger
from shared.series_anbima_repository import upsert_series_anbima

from .parser import parse_caracteristicas_payload
from .types import CetipAsset, infer_tipo_from_cetip, normalize_tipo

logger = get_logger("scraper.anbima_caracteristicas")

_DEFAULT_DELAY_SECONDS = 6.0
_DEFAULT_JITTER_SECONDS = 1.0
_DEFAULT_MAX_RPM = 10.0


def load_cetip_csv(path: str | Path) -> list[CetipAsset]:
    """Load ``codigo_cetip,tipo`` rows from CSV (header required)."""
    path = Path(path)
    assets: list[CetipAsset] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        cetip_key = fields.get("codigo_cetip") or fields.get("cetip") or fields.get("codigo")
        tipo_key = fields.get("tipo") or fields.get("type") or fields.get("asset_type")
        if not cetip_key:
            raise ValueError("CSV must include a codigo_cetip column")

        for row in reader:
            codigo = (row.get(cetip_key) or "").strip().upper()
            if not codigo:
                continue
            raw_tipo = (row.get(tipo_key) or "").strip() if tipo_key else ""
            tipo = normalize_tipo(raw_tipo) or infer_tipo_from_cetip(codigo)
            if tipo is None:
                raise ValueError(
                    f"Cannot determine asset tipo for {codigo!r} (got {raw_tipo!r})"
                )
            assets.append(CetipAsset(codigo_cetip=codigo, tipo=tipo))
    return assets


class AnbimaCaracteristicasScraper:
    """Scrape ANBIMA características for a list of CETIP assets."""

    def __init__(self, config: ScraperConfig | None = None):
        self.config = config or ScraperConfig.from_env("anbima_caracteristicas")
        self.config.request_delay_seconds = max(
            float(self.config.request_delay_seconds or 0), _DEFAULT_DELAY_SECONDS
        )
        self.config.max_requests_per_minute = min(
            float(self.config.max_requests_per_minute or _DEFAULT_MAX_RPM),
            _DEFAULT_MAX_RPM,
        )
        if float(self.config.request_jitter_seconds or 0) <= 0:
            self.config.request_jitter_seconds = _DEFAULT_JITTER_SECONDS
        self._last_request_ts: float | None = None
        self.logger = logger

    def _throttle(self) -> None:
        rpm_interval = (
            60.0 / self.config.max_requests_per_minute
            if self.config.max_requests_per_minute
            else 0.0
        )
        min_interval = max(self.config.request_delay_seconds, rpm_interval)
        if self._last_request_ts is not None:
            elapsed = time.monotonic() - self._last_request_ts
            wait = min_interval - elapsed
            if wait > 0:
                time.sleep(wait)
        if self.config.request_jitter_seconds > 0:
            time.sleep(random.uniform(0, self.config.request_jitter_seconds))
        self._last_request_ts = time.monotonic()

    def scrape_asset(self, asset: CetipAsset, *, context: Any) -> dict[str, Any]:
        """Return one características row for a CETIP using a shared browser context."""
        marker = asset.bff_caracteristicas_path_fragment
        portal = asset.portal_caracteristicas_url
        captured: list[Any] = []
        page = context.new_page()

        def _on_response(response, _marker=marker, _bucket=captured) -> None:
            if _marker not in response.url:
                return
            try:
                _bucket.append(response.json())
            except Exception:
                pass

        page.on("response", _on_response)
        self._throttle()
        self.logger.info(
            "anbima_caracteristicas_fetch",
            extra={
                "codigo_cetip": asset.codigo_cetip,
                "tipo": asset.tipo,
                "url": portal,
            },
        )
        try:
            # Prefer domcontentloaded: networkidle often hangs on this SPA.
            page.goto(portal, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            # Retry once on empty capture (token / timing flakiness).
            if not captured:
                self.logger.warning(
                    "anbima_caracteristicas_retry",
                    extra={"codigo_cetip": asset.codigo_cetip},
                )
                self._throttle()
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
        finally:
            page.close()

        if not captured:
            raise RuntimeError(
                f"No BFF características response for {asset.codigo_cetip}"
            )

        payload = captured[-1]
        row = parse_caracteristicas_payload(
            payload, codigo_cetip=asset.codigo_cetip, tipo=asset.tipo
        )
        return row

    def run(
        self,
        assets: Iterable[CetipAsset],
        *,
        create_schema: bool = False,
        max_items: int | None = None,
    ) -> dict[str, Any]:
        """Scrape assets and upsert into ``series_anbima``. Returns a summary dict."""
        summary: dict[str, Any] = {
            "source": "anbima_caracteristicas",
            "assets": 0,
            "assets_ok": 0,
            "rows": 0,
            "erros": 0,
            "interrompido_por_tempo": False,
            "by_cetip": {},
        }
        asset_list = list(assets)
        if max_items is not None:
            asset_list = asset_list[: max(0, int(max_items))]

        if create_schema or self.config.auto_create_schema:
            ensure_schema(self.config)

        if not asset_list:
            self.logger.info("anbima_caracteristicas_run_complete", extra=summary)
            return summary

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise BrowserUnavailableError(
                "Playwright is not installed; install the 'browser' extra."
            ) from exc

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = browser.new_context(
                user_agent=self.config.user_agent,
                locale="pt-BR",
            )
            context.set_default_timeout(self.config.request_timeout_seconds * 1000)
            try:
                for asset in asset_list:
                    summary["assets"] += 1
                    try:
                        row = self.scrape_asset(asset, context=context)
                        with session_scope(self.config) as session:
                            wrote = upsert_series_anbima(session, row)
                        if wrote:
                            summary["rows"] += 1
                            summary["assets_ok"] += 1
                            summary["by_cetip"][asset.codigo_cetip] = {
                                "serie": row.get("serie"),
                                "isin": row.get("isin"),
                                "empresa": row.get("empresa"),
                            }
                        else:
                            summary["erros"] += 1
                            summary["by_cetip"][asset.codigo_cetip] = "error:empty_row"
                        self.logger.info(
                            "anbima_caracteristicas_asset_done",
                            extra={
                                "codigo_cetip": asset.codigo_cetip,
                                "tipo": asset.tipo,
                                "isin": row.get("isin"),
                                "empresa": row.get("empresa"),
                            },
                        )
                    except Exception as exc:
                        summary["erros"] += 1
                        summary["by_cetip"][asset.codigo_cetip] = f"error:{exc}"
                        self.logger.warning(
                            "anbima_caracteristicas_asset_error",
                            extra={
                                "codigo_cetip": asset.codigo_cetip,
                                "tipo": asset.tipo,
                                "error": str(exc),
                            },
                        )
            finally:
                context.close()
                browser.close()

        self.logger.info("anbima_caracteristicas_run_complete", extra=summary)
        return summary
