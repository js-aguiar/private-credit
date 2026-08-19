"""Riza scraper (https://investidor.rizasec.com/emissoes).

Riza is a Next.js SPA backed by a public BFF on Virgo infrastructure. All data is fetched
with plain httpx calls — no headless browser needed.

API base: https://aks-prod.virgo.inc/mtr/bff-portal
  List:      GET /v1/operations?pageNumber={page}&pageSize={size}
             → {"content": [...], "metadata": {"pageNumber", "pageSize", "totalPages", ...}}
  Detail:    GET /v1/operations/{operationId}
  Documents: GET /v1/operations/{operationId}/documents
             → [{id, type, emissionDate, description, download}, ...]
"""

from __future__ import annotations

from typing import Any

from shared.parsing import parse_br_date
from shared.records import DetailResult, DocumentoData, EmissaoData, SerieData
from shared.scraper_base import BaseScraper

_BFF_BASE = "https://aks-prod.virgo.inc/mtr/bff-portal"
_LIST_URL = f"{_BFF_BASE}/v1/operations"
_DETAIL_URL = f"{_BFF_BASE}/v1/operations/{{operation_id}}"
_DOCUMENTS_URL = f"{_BFF_BASE}/v1/operations/{{operation_id}}/documents"
_PAGE_SIZE = 50
_PORTAL_BASE = "https://investidor.rizasec.com/emissoes"


class RizaScraper(BaseScraper):
    source_name = "riza"

    def list_emissoes(self):
        page = 0
        while True:
            try:
                payload = self.client.get_json(
                    _LIST_URL,
                    params={"pageNumber": page, "pageSize": _PAGE_SIZE},
                )
            except Exception as exc:
                self.logger.warning("riza_list_error", extra={"page": page, "error": str(exc)})
                break

            items: list[dict] = (payload or {}).get("content") or []
            metadata: dict = (payload or {}).get("metadata") or {}
            total_pages = int(metadata.get("totalPages") or 1)

            for item in items:
                data = self._map_list_item(item)
                if data is not None:
                    yield data

            self.logger.info(
                "riza_list_page",
                extra={
                    "page": page,
                    "total_pages": total_pages,
                    "items": len(items),
                },
            )
            if page + 1 >= total_pages or not items:
                break
            page += 1

    def _map_list_item(self, record: dict) -> EmissaoData | None:
        operation_id = (record.get("id") or "").strip()
        if not operation_id:
            return None

        series = record.get("series") or []
        instrument_codes = [
            str(s.get("instrumentCode")).strip()
            for s in series
            if isinstance(s, dict) and s.get("instrumentCode")
        ]
        series_numbers = [
            str(s.get("number")).strip()
            for s in series
            if isinstance(s, dict) and s.get("number") is not None
        ]

        return EmissaoData(
            fonte=self.source_name,
            id_origem=operation_id,
            link=f"{_PORTAL_BASE}/{operation_id}",
            numero_emissao=str(record.get("issuanceNumber"))
            if record.get("issuanceNumber") is not None
            else None,
            codigos_cetip=" ".join(instrument_codes) or None,
            operacao=record.get("alias"),
            devedor=self._devedor_from_counterparties(record.get("counterparties")),
            tipo_ativo=record.get("type"),
            series_raw="-".join(series_numbers) if series_numbers else None,
            valor_total=record.get("totalValue"),
            data_emissao=parse_br_date(str(record.get("emissionDate") or "")[:10]),
            data_vencimento=parse_br_date(str(record.get("dueDate") or "")[:10]),
            extras={
                "status": record.get("status"),
                "assetRisk": record.get("assetRisk"),
                "assetType": record.get("assetType"),
                "emissor": record.get("emissor"),
                "fiduciaryAgent": record.get("fiduciaryAgent"),
                "series": series,
            },
        )

    def fetch_detail(self, emissao) -> DetailResult:
        detail: dict = {}
        try:
            detail = self.client.get_json(
                _DETAIL_URL.format(operation_id=emissao.id_origem)
            ) or {}
        except Exception as exc:
            self.logger.warning(
                "riza_detail_error",
                extra={"id_origem": emissao.id_origem, "error": str(exc)},
            )

        series_source = (detail or {}).get("series") or (emissao.extras or {}).get("series") or []
        series = self._extract_series(series_source, emissao)
        documentos = self._fetch_documents(emissao)

        updates: dict = {"extras": {"detalhe_acessivel": bool(detail)}}
        if detail:
            updates.update(
                {
                    "operacao": detail.get("alias") or emissao.operacao,
                    "devedor": self._devedor_from_counterparties(detail.get("counterparties"))
                    or emissao.devedor,
                    "tipo_ativo": detail.get("type") or emissao.tipo_ativo,
                    "valor_total": detail.get("totalValue") or emissao.valor_total,
                    "data_emissao": parse_br_date(str(detail.get("emissionDate") or "")[:10])
                    or emissao.data_emissao,
                    "data_vencimento": parse_br_date(str(detail.get("dueDate") or "")[:10])
                    or emissao.data_vencimento,
                    "extras": {
                        "detalhe_acessivel": True,
                        "status": detail.get("status"),
                        "assetRisk": detail.get("assetRisk"),
                        "assetType": detail.get("assetType"),
                        "emissor": detail.get("emissor"),
                        "counterparties": detail.get("counterparties"),
                    },
                }
            )
        elif not documentos:
            updates["extras"] = {"detalhe_acessivel": False}

        return DetailResult(
            emissao_updates=updates,
            series=series,
            documentos=documentos,
        )

    def _extract_series(self, series_payload: Any, emissao) -> list[SerieData]:
        if not isinstance(series_payload, list):
            return []
        series: list[SerieData] = []
        for item in series_payload:
            if not isinstance(item, dict):
                continue
            numero = item.get("number")
            if numero is None:
                continue
            indexer = item.get("indexer")
            if isinstance(indexer, dict):
                indexador = indexer.get("name")
            else:
                params = item.get("params") or {}
                indexador = params.get("indexer") if isinstance(params, dict) else None
            series.append(
                SerieData(
                    numero_serie=str(numero),
                    isin=(item.get("isinCode") or "").strip() or None,
                    numero_emissao=emissao.numero_emissao,
                    codigo_cetip=(item.get("instrumentCode") or "").strip() or None,
                    indexador=str(indexador).strip() if indexador else None,
                    data_vencimento=parse_br_date(str(item.get("dueDate") or "")[:10]),
                    extras={
                        "id": item.get("id"),
                        "status": item.get("status"),
                        "type": item.get("type"),
                        "params": item.get("params"),
                    },
                )
            )
        return series

    def _fetch_documents(self, emissao) -> list[DocumentoData]:
        try:
            payload = self.client.get_json(
                _DOCUMENTS_URL.format(operation_id=emissao.id_origem)
            )
        except Exception as exc:
            self.logger.warning(
                "riza_documents_error",
                extra={"id_origem": emissao.id_origem, "error": str(exc)},
            )
            return []

        if not isinstance(payload, list):
            return []

        docs: list[DocumentoData] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            url = item.get("download")
            if not url:
                continue
            doc_type = item.get("type")
            description = item.get("description")
            titulo = doc_type
            if description and doc_type:
                titulo = f"{doc_type} ({description})"
            docs.append(
                DocumentoData(
                    link_documento=url,
                    titulo=titulo,
                    tipo_documento=doc_type,
                    data_documento=parse_br_date(str(item.get("emissionDate") or "")[:10]),
                    numero_emissao=emissao.numero_emissao,
                    codigo_cetip=(emissao.codigos_cetip or "").split()[0]
                    if emissao.codigos_cetip
                    else None,
                    extras=item,
                )
            )
        return docs

    @staticmethod
    def _devedor_from_counterparties(counterparties: Any) -> str | None:
        if not isinstance(counterparties, list):
            return None
        names: list[str] = []
        for entry in counterparties:
            if not isinstance(entry, dict):
                continue
            role = (entry.get("type") or "").lower()
            name = (entry.get("businessName") or "").strip()
            if not name:
                continue
            if "devedor" in role or "tomador" in role or "cedente" in role:
                names.append(name)
        if names:
            return "; ".join(dict.fromkeys(names))
        return None
