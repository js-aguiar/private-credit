"""Opea scraper (https://app.opea.com.br/pt/emissoes).

Opea is a Vue/Vite SPA backed by a JSON API (observed store calls use ``codigoOpea`` and
paging params ``pagina`` / ``tamanhoPagina``). Because the exact API base/auth must be
confirmed against a live session, this scraper defaults to the browser-capture path and
lets operators inject the confirmed API endpoints via ``OPEA_API_LIST_URL`` /
``OPEA_API_DETAIL_URL`` (with ``{page}``/``{size}`` and ``{id}`` placeholders).

Field mappings below are tolerant (case-insensitive, multi-key) and should be tuned once
real payloads are captured.
"""

from __future__ import annotations

from shared.mapping import find_dicts_with_key, pick
from shared.parsing import parse_br_date, parse_int
from shared.records import EmissaoData, SerieData
from shared.spa_scraper import SpaScraper


class OpeaScraper(SpaScraper):
    source_name = "opea"

    listing_url = "https://app.opea.com.br/pt/emissoes"
    page_size = 50
    # Opea's API uses pagina/tamanhoPagina; set OPEA_API_LIST_URL to the confirmed
    # endpoint, e.g. "https://.../emissoes?pagina={page}&tamanhoPagina={size}".
    api_list_url_template = None
    api_detail_url_template = None

    def _detail_link(self, codigo: str) -> str:
        return f"https://app.opea.com.br/pt/emissoes/{codigo}"

    def map_emissao(self, record: dict) -> EmissaoData | None:
        codigo = pick(record, "codigoOpea", "codigo", "codigoOperacao", "id")
        isin = pick(record, "isin", "codigoIsin")
        numero = pick(record, "numeroEmissao", "emissao", "numero")
        id_origem = str(codigo or isin or numero or "").strip()
        if not id_origem:
            return None
        return EmissaoData(
            fonte=self.source_name,
            id_origem=id_origem,
            link=self._detail_link(str(codigo)) if codigo else self.listing_url,
            isin=isin,
            numero_emissao=str(numero) if numero is not None else None,
            codigos_cetip=pick(record, "codigoCetip", "cetip", "codCetip"),
            operacao=pick(record, "nomeOperacao", "operacao", "nome", "emissor", "devedor"),
            devedor=pick(record, "devedor", "emissor", "cedente"),
            ano_emissao=parse_int(str(pick(record, "anoEmissao", "ano") or "")),
            tipo_ativo=pick(record, "tipoAtivo", "ativo", "tipo", "classe"),
            valor_total=self.money(record, "valorEmissao", "valor", "valorTotal", "montante"),
            indexador=pick(record, "indexador", "index", "remuneracao"),
            data_emissao=parse_br_date(pick(record, "dataEmissao", "dtEmissao")),
            data_vencimento=parse_br_date(pick(record, "dataVencimento", "dtVencimento", "vencimento")),
            rating=pick(record, "rating", "classificacao"),
            extras=record,
        )

    def map_series(self, detail_payload, emissao) -> list[SerieData]:
        series: list[SerieData] = []
        seen: set[str] = set()
        for item in find_dicts_with_key(
            detail_payload, ["numeroSerie", "serie", "codigoCetip", "cetip"]
        ):
            numero_serie = str(
                pick(item, "numeroSerie", "serie", "numero", default="") or ""
            ).strip()
            isin = pick(item, "isin", "codigoIsin")
            cetip = pick(item, "codigoCetip", "cetip", "codCetip")
            dedup_key = isin or f"{numero_serie}:{cetip}"
            if not (numero_serie or isin or cetip) or dedup_key in seen:
                continue
            seen.add(dedup_key)
            series.append(
                SerieData(
                    numero_serie=numero_serie or (isin or cetip or ""),
                    isin=isin,
                    numero_emissao=emissao.numero_emissao,
                    codigo_cetip=cetip,
                    valor=self.money(item, "valor", "valorSerie", "montante"),
                    remuneracao=pick(item, "remuneracao", "taxa", "juros"),
                    indexador=pick(item, "indexador", "index"),
                    data_emissao=parse_br_date(pick(item, "dataEmissao", "dtEmissao")),
                    data_vencimento=parse_br_date(
                        pick(item, "dataVencimento", "vencimento", "dtVencimento")
                    ),
                    quantidade=parse_int(str(pick(item, "quantidade", "qtd") or "")),
                    rating=pick(item, "rating", "classificacao"),
                    extras=item,
                )
            )
        return series
