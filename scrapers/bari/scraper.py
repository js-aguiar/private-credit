"""Bari scraper (https://barisec.com.br/emissoes).

Bari publishes series-level rows via a public Strapi CMS API. Documents are only exposed
on Next.js SSG detail pages embedded as ``__NEXT_DATA__``. All fetching uses httpx — no
headless browser needed.

Sources:
  List:   GET https://strapicms.bancobari.com.br/api/emissions?pagination[page]=N
  Detail: GET https://barisec.com.br/emissoes/{code}  → pageProps documents

Each Strapi row is one IF B3 code (série). ``id_origem`` is the ``code`` field because
``emissionNumber`` is not unique (many distinct operations share ``emissionNumber=1``).
"""

from __future__ import annotations

from shared.records import DetailResult
from shared.scraper_base import BaseScraper

from . import parsers


class BariScraper(BaseScraper):
    source_name = "bari"

    def list_series(self):
        try:
            rows = parsers.fetch_strapi_emissions(self.client)
        except Exception as exc:
            self.logger.warning("bari_list_error", extra={"error": str(exc)})
            return

        self.logger.info("bari_list_fetched", extra={"series_rows": len(rows)})

        for row in rows:
            data = parsers.map_list_item(row, fonte=self.source_name)
            if data is not None:
                yield data

    def fetch_detail(self, serie) -> DetailResult:
        extras = dict(serie.extras or {})
        code = serie.id_origem or (extras.get("code") or "").strip()
        documentos = []
        detail_accessible = False
        numero_emissao = serie.numero_emissao

        if code:
            detail_url = parsers.DETAIL_URL_TEMPLATE.format(code=code)
            try:
                html = self.client.get_text(detail_url)
                page_props = parsers.extract_page_props(html)
                documentos = parsers.map_documents(page_props, serie)
                detail_accessible = bool(page_props.get("emission") or documentos)

                emission_detail = page_props.get("emission")
                if isinstance(emission_detail, dict):
                    numero = emission_detail.get("emissionNumber")
                    if numero is not None:
                        numero_emissao = str(numero)
                        extras["detail_emission_number"] = numero
            except Exception as exc:
                self.logger.warning(
                    "bari_detail_error",
                    extra={"id_origem": serie.id_origem, "code": code, "error": str(exc)},
                )

        for doc in documentos:
            doc.numero_emissao = numero_emissao
            if serie.codigo_cetip and not doc.codigo_cetip:
                doc.codigo_cetip = serie.codigo_cetip

        serie_updates: dict = {
            "link": parsers.DETAIL_URL_TEMPLATE.format(code=code) if code else serie.link,
            "extras": {
                **extras,
                "detalhe_acessivel": detail_accessible,
            },
        }
        if numero_emissao is not None:
            serie_updates["numero_emissao"] = str(numero_emissao)

        return DetailResult(
            serie_updates=serie_updates,
            series=[],
            documentos=documentos,
        )
