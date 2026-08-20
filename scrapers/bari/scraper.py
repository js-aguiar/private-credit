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

    def list_emissoes(self):
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

    def fetch_detail(self, emissao) -> DetailResult:
        extras = emissao.extras or {}
        series_row = extras.get("series_row")
        if not isinstance(series_row, dict):
            series_row = {}

        code = emissao.id_origem or (series_row.get("code") or "").strip()
        series = parsers.map_series_from_rows([series_row] if series_row else [], emissao)
        documentos = []
        detail_accessible = False

        if code:
            detail_url = parsers.DETAIL_URL_TEMPLATE.format(code=code)
            try:
                html = self.client.get_text(detail_url)
                page_props = parsers.extract_page_props(html)
                documentos = parsers.map_documents(page_props, emissao)
                detail_accessible = bool(page_props.get("emission") or documentos)

                emission_detail = page_props.get("emission")
                if isinstance(emission_detail, dict):
                    numero = emission_detail.get("emissionNumber")
                    if numero is not None:
                        extras = {
                            **extras,
                            "detail_emission_number": numero,
                        }
            except Exception as exc:
                self.logger.warning(
                    "bari_detail_error",
                    extra={"id_origem": emissao.id_origem, "code": code, "error": str(exc)},
                )

        for doc in documentos:
            doc.numero_emissao = emissao.numero_emissao
            if emissao.codigos_cetip and not doc.codigo_cetip:
                doc.codigo_cetip = emissao.codigos_cetip

        emissao_updates: dict = {
            "link": parsers.DETAIL_URL_TEMPLATE.format(code=code) if code else emissao.link,
            "extras": {
                **extras,
                "detalhe_acessivel": detail_accessible,
                "series_row": series_row,
            },
        }
        detail_num = extras.get("detail_emission_number")
        if detail_num is not None:
            emissao_updates["numero_emissao"] = str(detail_num)

        return DetailResult(
            emissao_updates=emissao_updates,
            series=series,
            documentos=documentos,
        )
