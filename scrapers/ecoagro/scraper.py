"""Ecoagro scraper.

Ecoagro's /emissoes listing is server-rendered HTML, so discovery uses ``httpx`` +
BeautifulSoup. Each listing row expands into one or more séries (CETIP keys from the
list). Detail pages enrich ISIN/remuneration and supply documents.
"""

from __future__ import annotations

import os

from shared.records import DetailResult, SerieData
from shared.scraper_base import BaseScraper

from .parsers import (
    find_max_page,
    merge_series_from_detail,
    parse_detail,
    parse_listing_rows,
    parse_series_from_detail,
)


class EcoagroScraper(BaseScraper):
    source_name = "ecoagro"

    BASE_URL = "https://ecoagro.agr.br"
    LIST_URL = "https://ecoagro.agr.br/emissoes"
    DEFAULT_DETAIL_TEMPLATE = "https://ecoagro.agr.br/emissoes-integra/{id}"

    def __init__(self, config, context=None):
        super().__init__(config, context=context)
        self.detail_template = os.getenv(
            "ECOAGRO_DETAIL_URL_TEMPLATE", self.DEFAULT_DETAIL_TEMPLATE
        )

    def list_series(self):
        first_html = self.client.get_text(self.LIST_URL)
        max_page = find_max_page(first_html)
        self.logger.info("ecoagro_pages", extra={"paginas": max_page})

        yield from parse_listing_rows(first_html, self.BASE_URL, self.detail_template)
        for page in range(2, max_page + 1):
            html = self.client.get_text(f"{self.LIST_URL}?page={page}")
            yield from parse_listing_rows(html, self.BASE_URL, self.detail_template)

    def fetch_detail(self, serie) -> DetailResult:
        emissao_id = serie.emissao_id or (
            (serie.extras or {}).get("listing_data_id") if isinstance(serie.extras, dict) else None
        )
        detail_url = serie.link or (
            self.detail_template.format(id=emissao_id) if emissao_id else None
        )
        html = self._fetch_detail_html(detail_url) if detail_url else None

        updates: dict = {"link": detail_url} if detail_url else {}
        documentos = []
        sibling_series = []

        if html:
            page_updates, documentos = parse_detail(html, self.BASE_URL)
            detail_series = parse_series_from_detail(html, emissao_id=emissao_id)
            baseline_data = [
                SerieData(
                    fonte=serie.fonte,
                    id_origem=serie.id_origem,
                    emissao_id=serie.emissao_id,
                    link=serie.link,
                    numero_serie=serie.numero_serie or "",
                    isin=serie.isin,
                    codigo_cetip=serie.codigo_cetip,
                    numero_emissao=serie.numero_emissao,
                    operacao=serie.operacao,
                    devedor=serie.devedor,
                    ano_emissao=serie.ano_emissao,
                    tipo_ativo=serie.tipo_ativo,
                    series_raw=serie.series_raw,
                    valor_total=serie.valor_total,
                    extras=dict(serie.extras or {}),
                )
            ]
            sibling_series = merge_series_from_detail(baseline_data, detail_series)
            extras = page_updates.pop("extras", None)
            updates.update(page_updates)
            merged_extras = {"detalhe_acessivel": True}
            if extras:
                merged_extras.update(extras)
            updates["extras"] = merged_extras

            # Apply ISIN/CETIP/remuneration for the série being detailed.
            for candidate in sibling_series:
                if candidate.id_origem == serie.id_origem or (
                    candidate.codigo_cetip and candidate.codigo_cetip == serie.codigo_cetip
                ):
                    if candidate.isin:
                        updates["isin"] = candidate.isin
                    if candidate.codigo_cetip:
                        updates["codigo_cetip"] = candidate.codigo_cetip
                    if candidate.remuneracao:
                        updates["remuneracao"] = candidate.remuneracao
                    break
        else:
            updates["extras"] = {"detalhe_acessivel": False}

        for doc in documentos:
            doc.numero_emissao = serie.numero_emissao
            doc.emissao_id = emissao_id
            if serie.codigo_cetip and not doc.codigo_cetip:
                doc.codigo_cetip = serie.codigo_cetip

        # Do not re-upsert the série being detailed as a sibling (apply_serie_detail handles it).
        siblings = [
            s
            for s in sibling_series
            if s.id_origem != serie.id_origem
            and not (s.codigo_cetip and s.codigo_cetip == serie.codigo_cetip)
        ]

        return DetailResult(
            serie_updates=updates,
            series=siblings,
            documentos=documentos,
        )

    def _fetch_detail_html(self, url: str) -> str | None:
        try:
            response = self.client.get(url)
            if response.status_code == 200 and response.text:
                return response.text
        except Exception as exc:
            self.logger.warning("ecoagro_detail_http_error", extra={"url": url, "error": str(exc)})

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
