"""Ecoagro scraper.

Ecoagro's /emissoes listing is server-rendered HTML, so discovery uses ``httpx`` +
BeautifulSoup. Each row exposes a ``data-id`` used as the stable ``id_origem`` and to
build the operation's detail URL. Séries are derived from the list-level série/CETIP
columns (so they are captured even if a detail page is unreachable) and enriched from the
detail page when available; documents come from the detail page.
"""

from __future__ import annotations

import os

from shared.records import DetailResult
from shared.scraper_base import BaseScraper

from .parsers import (
    build_series_from_emissao,
    find_max_page,
    parse_detail,
    parse_listing_rows,
)


class EcoagroScraper(BaseScraper):
    source_name = "ecoagro"

    BASE_URL = "https://ecoagro.agr.br"
    LIST_URL = "https://ecoagro.agr.br/emissoes"
    # The detail URL pattern is configurable because the site may change it; the
    # {id} placeholder is filled with the row's data-id.
    DEFAULT_DETAIL_TEMPLATE = "https://ecoagro.agr.br/emissoes/{id}"

    def __init__(self, config, context=None):
        super().__init__(config, context=context)
        self.detail_template = os.getenv(
            "ECOAGRO_DETAIL_URL_TEMPLATE", self.DEFAULT_DETAIL_TEMPLATE
        )

    def list_emissoes(self):
        first_html = self.client.get_text(self.LIST_URL)
        max_page = find_max_page(first_html)
        self.logger.info("ecoagro_pages", extra={"paginas": max_page})

        yield from parse_listing_rows(first_html, self.BASE_URL, self.detail_template)
        for page in range(2, max_page + 1):
            html = self.client.get_text(f"{self.LIST_URL}?page={page}")
            yield from parse_listing_rows(html, self.BASE_URL, self.detail_template)

    def fetch_detail(self, emissao) -> DetailResult:
        # Baseline séries from the list-level data (always available).
        series = build_series_from_emissao(
            emissao.numero_emissao, emissao.series_raw, emissao.codigos_cetip
        )

        detail_url = emissao.link or self.detail_template.format(id=emissao.id_origem)
        html = self._fetch_detail_html(detail_url)

        updates: dict = {"link": detail_url}
        documentos = []
        if html:
            page_updates, documentos = parse_detail(html, self.BASE_URL)
            extras = page_updates.pop("extras", None)
            updates.update(page_updates)
            merged_extras = {"detalhe_acessivel": True}
            if extras:
                merged_extras.update(extras)
            updates["extras"] = merged_extras
        else:
            updates["extras"] = {"detalhe_acessivel": False}

        # Tag documents with the emission's linking keys.
        for doc in documentos:
            doc.numero_emissao = emissao.numero_emissao
            if emissao.codigos_cetip and not doc.codigo_cetip:
                doc.codigo_cetip = emissao.codigos_cetip.split()[0]

        return DetailResult(emissao_updates=updates, series=series, documentos=documentos)

    def _fetch_detail_html(self, url: str) -> str | None:
        try:
            response = self.client.get(url)
            if response.status_code == 200 and response.text:
                return response.text
        except Exception as exc:
            self.logger.warning("ecoagro_detail_http_error", extra={"url": url, "error": str(exc)})

        # Optional headless fallback (in case the detail content is JS-rendered).
        if self.config.use_browser_fallback:
            try:
                from shared.browser import BrowserFetcher

                with BrowserFetcher(self.config) as browser:
                    return browser.render(url)
            except Exception as exc:
                self.logger.warning(
                    "ecoagro_detail_browser_error", extra={"url": url, "error": str(exc)}
                )
        return None
