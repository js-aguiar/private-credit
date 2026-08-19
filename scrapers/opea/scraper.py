"""Opea scraper (https://app.opea.com.br/pt/emissoes).

Opea is a Vue/Vite SPA backed by a BFF REST API that is publicly accessible without
authentication. All data is fetched with plain httpx calls — no headless browser needed.

API base: https://app.opea.com.br/bff/v1/api/
  List:   GET emissao/passivosoperacoes?pagina={page}&tamanhoPagina={size}
          → {"content": {"emissoes": {"lastPage": N, "totalCount": N, "items": [...]}}}
  Detail: GET emissao/passivosoperacoes/detalhe?codigoOpea={id}
          → {"content": {..., "idCedoc": "<guid>", ...}}
  Files:  GET cedoc/files?idCedoc={idCedoc}
          → {"children": [{name, url, categoryName, createdOn, ...}]}
          The file list is institution-wide; filter by emission code in the filename
          (e.g. "E0228" for codigoOpea "CRA.228.CIA.1").
"""

from __future__ import annotations

import re
from typing import Any

from shared.mapping import pick
from shared.opea_documents import normalize_opea_document_url, opea_file_id
from shared.parsing import parse_br_date
from shared.records import DetailResult, DocumentoData, EmissaoData, SerieData
from shared.scraper_base import BaseScraper

_BFF_BASE = "https://app.opea.com.br/bff/v1/api/"

# Matches the emission-number segment in cedoc filenames, e.g. "E0228" for emissao 228.
_EMISSAO_CODE_RE = re.compile(r"E(\d+)", re.IGNORECASE)


def _emission_file_code(numero_emissao: str | int | None) -> str | None:
    """Return the zero-padded emission code used in cedoc filenames, e.g. 228 → 'E0228'."""
    if numero_emissao is None:
        return None
    try:
        n = int(str(numero_emissao).strip())
        return f"E{n:04d}"
    except (ValueError, TypeError):
        return None


class OpeaScraper(BaseScraper):
    source_name = "opea"

    _LIST_URL = f"{_BFF_BASE}emissao/passivosoperacoes"
    _DETAIL_URL = f"{_BFF_BASE}emissao/passivosoperacoes/detalhe"
    _FILES_URL = f"{_BFF_BASE}cedoc/files"
    _PAGE_SIZE = 50

    # ------------------------------------------------------------------ listing

    def list_emissoes(self):
        page = 1
        while True:
            try:
                payload = self.client.get_json(
                    self._LIST_URL,
                    params={"pagina": page, "tamanhoPagina": self._PAGE_SIZE},
                )
            except Exception as exc:
                self.logger.warning("opea_list_error", extra={"page": page, "error": str(exc)})
                break

            emissoes_block = (
                (payload or {}).get("content", {}).get("emissoes") or {}
            )
            items = emissoes_block.get("items") or []
            last_page = int(emissoes_block.get("lastPage") or 1)

            for item in items:
                data = self._map_list_item(item)
                if data is not None:
                    yield data

            self.logger.info(
                "opea_list_page",
                extra={"page": page, "last_page": last_page, "items": len(items)},
            )
            if page >= last_page or not items:
                break
            page += 1

    def _map_list_item(self, record: dict) -> EmissaoData | None:
        codigo_opea = (record.get("codigoOpea") or "").strip()
        if not codigo_opea:
            return None
        isin = (record.get("isin") or "").strip() or None
        cetip = (record.get("codigoIf") or "").strip() or None
        numero = record.get("emissao")
        return EmissaoData(
            fonte=self.source_name,
            id_origem=codigo_opea,
            link=f"https://app.opea.com.br/pt/emissoes/{codigo_opea}",
            isin=isin,
            numero_emissao=str(numero) if numero is not None else None,
            codigos_cetip=cetip,
            operacao=pick(record, "nomeDevedor", "apelidoOperacao"),
            devedor=pick(record, "nomeDevedor"),
            tipo_ativo=pick(record, "naturezaOperacao", "classe"),
            indexador=pick(record, "indexador"),
            data_vencimento=parse_br_date(str(record.get("dataVencimento") or "")[:10]),
            rating=pick(record, "rating"),
            extras=record,
        )

    # ------------------------------------------------------------------ detail

    def fetch_detail(self, emissao) -> DetailResult:
        # --- detail fields ---
        detail: dict = {}
        try:
            resp = self.client.get_json(
                self._DETAIL_URL, params={"codigoOpea": emissao.id_origem}
            )
            detail = (resp or {}).get("content") or {}
        except Exception as exc:
            self.logger.warning(
                "opea_detail_error",
                extra={"id_origem": emissao.id_origem, "error": str(exc)},
            )

        updates: dict = {
            "extras": {"detalhe_acessivel": bool(detail)},
        }
        if detail:
            updates.update(
                {
                    "isin": detail.get("codigoIsin") or emissao.isin,
                    "codigos_cetip": detail.get("codigoCetipBbb") or emissao.codigos_cetip,
                    "data_emissao": parse_br_date(str(detail.get("dataEmissaoSerie") or "")[:10]),
                    "data_vencimento": parse_br_date(
                        str(detail.get("dataVencimentoSerie") or "")[:10]
                    ),
                    "extras": {
                        "detalhe_acessivel": True,
                        "permissao_divulgacao": pick(
                            detail.get("permissaoDivulgacaoPortal") or {}, "raw", "value"
                        ),
                        "oferta": pick(detail.get("tipoOferta") or {}, "raw", "value"),
                        "emissor": pick(detail.get("emissor") or {}, "descricao"),
                        "idCedoc": detail.get("idCedoc"),
                    },
                }
            )

        series = self._extract_series(emissao, detail)
        documentos = self._fetch_documents(emissao, detail)

        return DetailResult(
            emissao_updates=updates,
            series=series,
            documentos=documentos,
        )

    def _extract_series(self, emissao, detail: dict) -> list[SerieData]:
        """Build one SerieData from the detail page (Opea is one série per codigoOpea)."""
        if not detail:
            return []
        isin = detail.get("codigoIsin") or emissao.isin
        cetip = (detail.get("codigoCetipBbb") or "").strip() or emissao.codigos_cetip
        numero = emissao.numero_emissao
        serie_num = str(detail.get("serie") or "").strip() or "1"
        if not (isin or cetip):
            return []
        remuneracao_obj = detail.get("remuneracao") or {}
        remuneracao_str = (
            remuneracao_obj.get("descricao") if isinstance(remuneracao_obj, dict) else None
        )
        return [
            SerieData(
                numero_serie=serie_num,
                isin=isin,
                numero_emissao=numero,
                codigo_cetip=cetip,
                data_emissao=parse_br_date(str(detail.get("dataEmissaoSerie") or "")[:10]),
                data_vencimento=parse_br_date(
                    str(detail.get("dataVencimentoSerie") or "")[:10]
                ),
                quantidade=_safe_int(detail.get("quantidadeEmitida")),
                remuneracao=remuneracao_str,
                extras={"precoUnitario": detail.get("precoUnitario")},
            )
        ]

    def _fetch_documents(self, emissao, detail: dict) -> list[DocumentoData]:
        """Fetch documents from cedoc/files and filter to this emission."""
        id_cedoc = detail.get("idCedoc")
        if not id_cedoc:
            return []
        emission_code = _emission_file_code(emissao.numero_emissao)
        if not emission_code:
            return []

        try:
            resp = self.client.get_json(self._FILES_URL, params={"idCedoc": id_cedoc})
        except Exception as exc:
            self.logger.warning(
                "opea_cedoc_error",
                extra={"id_origem": emissao.id_origem, "error": str(exc)},
            )
            return []

        children: list[dict] = (resp or {}).get("children") or []
        docs: list[DocumentoData] = []
        for child in children:
            name = child.get("name") or ""
            if emission_code not in name.upper():
                continue
            url = child.get("url")
            if not url:
                continue
            docs.append(
                DocumentoData(
                    link_documento=normalize_opea_document_url(url),
                    titulo=name,
                    tipo_documento=child.get("categoryName"),
                    data_documento=parse_br_date(str(child.get("createdOn") or "")[:10]),
                    numero_emissao=emissao.numero_emissao,
                    codigo_cetip=(emissao.codigos_cetip or "").split()[0]
                    if emissao.codigos_cetip
                    else None,
                    id_origem_arquivo=opea_file_id(child),
                    extras=child,
                )
            )
        return docs


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return None
