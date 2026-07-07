"""Riza scraper (https://investidor.rizasec.com/emissoes).

Riza is a Next.js SPA whose data is fetched from a JSON API. As with the other SPAs, the
confirmed API endpoints can be injected via ``RIZA_API_LIST_URL`` / ``RIZA_API_DETAIL_URL``;
otherwise the scraper captures the JSON the app requests via headless Chromium.
"""

from __future__ import annotations

from shared.mapping import find_dicts_with_key, pick
from shared.parsing import parse_br_date, parse_int
from shared.records import EmissaoData, SerieData
from shared.spa_scraper import SpaScraper


class RizaScraper(SpaScraper):
    source_name = "riza"

    listing_url = "https://investidor.rizasec.com/emissoes"
    page_size = 50
    api_list_url_template = None
    api_detail_url_template = None

    def _detail_link(self, identifier: str) -> str:
        return f"https://investidor.rizasec.com/emissoes/{identifier}"

    def map_emissao(self, record: dict) -> EmissaoData | None:
        identifier = pick(record, "id", "codigo", "slug", "codigoOperacao")
        isin = pick(record, "isin", "codigoIsin")
        numero = pick(record, "numeroEmissao", "emissao", "numero")
        id_origem = str(identifier or isin or numero or "").strip()
        if not id_origem:
            return None
        return EmissaoData(
            fonte=self.source_name,
            id_origem=id_origem,
            link=self._detail_link(id_origem),
            isin=isin,
            numero_emissao=str(numero) if numero is not None else None,
            codigos_cetip=pick(record, "codigoCetip", "cetip", "codCetip"),
            operacao=pick(record, "nome", "nomeOperacao", "operacao", "emissor", "devedor"),
            devedor=pick(record, "devedor", "emissor", "cedente", "originador"),
            ano_emissao=parse_int(str(pick(record, "anoEmissao", "ano") or "")),
            tipo_ativo=pick(record, "tipoAtivo", "tipo", "ativo", "classe", "produto"),
            valor_total=self.money(record, "valorEmissao", "valor", "valorTotal", "montante"),
            indexador=pick(record, "indexador", "index", "remuneracao"),
            data_emissao=parse_br_date(pick(record, "dataEmissao", "dtEmissao")),
            data_vencimento=parse_br_date(
                pick(record, "dataVencimento", "vencimento", "dtVencimento")
            ),
            rating=pick(record, "rating", "classificacao"),
            extras=record,
        )

    def map_series(self, detail_payload, emissao) -> list[SerieData]:
        series: list[SerieData] = []
        seen: set[str] = set()
        for item in find_dicts_with_key(
            detail_payload, ["numeroSerie", "serie", "codigoCetip", "cetip", "isin"]
        ):
            numero_serie = str(pick(item, "numeroSerie", "serie", "numero", default="") or "").strip()
            isin = pick(item, "isin", "codigoIsin")
            cetip = pick(item, "codigoCetip", "cetip", "codCetip")
            if not (numero_serie or isin or cetip):
                continue
            dedup_key = isin or f"{numero_serie}:{cetip}"
            if dedup_key in seen:
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
