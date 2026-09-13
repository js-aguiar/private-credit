"""Riza scraper (https://investidor.rizasec.com/emissoes).

Riza is a Next.js SPA backed by a public BFF on Virgo infrastructure. All data is
fetched with plain httpx calls — no headless browser needed.

API base: https://aks-prod.virgo.inc/mtr/bff-portal
  List:      GET /v1/operations?pageNumber={page}&pageSize={size}
             → {"content": [...], "metadata": {"pageNumber", "pageSize", "totalPages", ...}}
  Detail:    GET /v1/operations/{operationId}
             Detail ``series[]`` powers the portal "Outras informações" accordion
             (``GeneralInfoAccordion_toggler__FWYOA``): Código IF/ISIN, tipo, ICVM,
             dates, taxa, indexador, volume, quantidade, PU.
  Documents: GET /v1/operations/{operationId}/documents
             → [{id, type, emissionDate, description, download}, ...]
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from shared.config import ScraperConfig
from shared.http_client import PoliteClient
from shared.parsing import parse_br_date
from shared.records import DetailResult, DocumentoData, EmissaoData, SerieData
from shared.scraper_base import BaseScraper

_BFF_BASE = "https://aks-prod.virgo.inc/mtr/bff-portal"
_LIST_URL = f"{_BFF_BASE}/v1/operations"
_DETAIL_URL = f"{_BFF_BASE}/v1/operations/{{operation_id}}"
_DOCUMENTS_URL = f"{_BFF_BASE}/v1/operations/{{operation_id}}/documents"
_PAGE_SIZE = 50
_PORTAL_BASE = "https://investidor.rizasec.com/emissoes"
# Virgo BFF rejects requests without a portal Origin (403).
_BFF_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://investidor.rizasec.com",
    "Referer": "https://investidor.rizasec.com/",
}


class RizaScraper(BaseScraper):
    source_name = "riza"

    def __init__(self, config: ScraperConfig, context=None):
        super().__init__(config, context=context)
        self.client.close()
        self.client = PoliteClient(config, headers=_BFF_HEADERS)

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
            _normalize_serie_numero(s.get("number"))
            for s in series
            if isinstance(s, dict) and s.get("number") is not None
        ]
        series_numbers = [n for n in series_numbers if n]

        return EmissaoData(
            fonte=self.source_name,
            id_origem=operation_id,
            link=f"{_PORTAL_BASE}/{operation_id}",
            numero_emissao=str(record.get("issuanceNumber"))
            if record.get("issuanceNumber") is not None
            else None,
            codigos_cetip=" ".join(instrument_codes) or None,
            operacao=record.get("alias"),
            # List payload rarely includes counterparties; filled on detail.
            devedor=self._devedor_from_counterparties(record.get("counterparties")),
            tipo_ativo=record.get("type"),
            series_raw="-".join(series_numbers) if series_numbers else None,
            valor_total=_to_decimal(record.get("totalValue")),
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
            detail = (
                self.client.get_json(_DETAIL_URL.format(operation_id=emissao.id_origem))
                or {}
            )
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
            counterparties = detail.get("counterparties")
            roles = _counterparties_by_role(counterparties)
            quantidade_total = _sum_series_amounts(series)
            updates.update(
                {
                    "operacao": detail.get("alias") or emissao.operacao,
                    "devedor": self._devedor_from_counterparties(counterparties)
                    or emissao.devedor,
                    "tipo_ativo": detail.get("type") or emissao.tipo_ativo,
                    "valor_total": _to_decimal(detail.get("totalValue"))
                    or emissao.valor_total,
                    "data_emissao": parse_br_date(str(detail.get("emissionDate") or "")[:10])
                    or emissao.data_emissao,
                    "data_vencimento": parse_br_date(str(detail.get("dueDate") or "")[:10])
                    or emissao.data_vencimento,
                    "extras": {
                        "detalhe_acessivel": True,
                        "status": detail.get("status"),
                        # Portal "Outras informações" / emission summary.
                        "lastro": detail.get("assetType"),
                        "assetRisk": detail.get("assetRisk"),
                        "assetType": detail.get("assetType"),
                        "emissor": detail.get("emissor")
                        or _first_role_name(roles, "Emissor"),
                        "coordenador_lider": _first_role_name(
                            roles, "Coordenador Líder"
                        ),
                        "escriturador": _first_role_name(roles, "Escriturador"),
                        "agente_fiduciario": _first_role_name(
                            roles, "Agente Fiduciário"
                        ),
                        "quantidade_papeis": quantidade_total,
                        "counterparties": counterparties,
                        "outras_informacoes": {
                            "quantidade_papeis": quantidade_total,
                            "lastro": detail.get("assetType"),
                            "devedora": self._devedor_from_counterparties(counterparties),
                            "coordenador_lider": _first_role_name(
                                roles, "Coordenador Líder"
                            ),
                            "escriturador": _first_role_name(roles, "Escriturador"),
                        },
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
        """Map detail ``series[]`` including Outras informações accordion fields."""
        if not isinstance(series_payload, list):
            return []
        series: list[SerieData] = []
        for item in series_payload:
            if not isinstance(item, dict):
                continue
            numero = _normalize_serie_numero(item.get("number"))
            if not numero:
                continue
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
            indexador = _series_indexer(item, params)
            interest_rate = params.get("interestRate")
            amount = params.get("amount")
            unit_value = params.get("unitValue")
            total_value = params.get("totalValue")
            first_payment = parse_br_date(str(item.get("firstPaymentDate") or "")[:10])
            series.append(
                SerieData(
                    numero_serie=numero,
                    isin=(item.get("isinCode") or "").strip() or None,
                    numero_emissao=emissao.numero_emissao,
                    codigo_cetip=(item.get("instrumentCode") or "").strip() or None,
                    # Volume Total de Emissão.
                    valor=_to_decimal(total_value),
                    # Taxa Juros Pré/Spread (%) + indexador.
                    remuneracao=_format_remuneracao(indexador, interest_rate),
                    indexador=indexador,
                    # Data de Integralização.
                    data_emissao=first_payment,
                    data_vencimento=parse_br_date(str(item.get("dueDate") or "")[:10]),
                    # Quantidade de Papéis de Emissão.
                    quantidade=_to_int(amount),
                    extras={
                        "id": item.get("id"),
                        "status": item.get("status"),
                        "tipo": item.get("type"),
                        "type": item.get("type"),
                        "icvm": item.get("icvm"),
                        "data_integralizacao": first_payment.isoformat()
                        if first_payment
                        else None,
                        "taxa_juros_spread": interest_rate,
                        "valor_unitario": _to_decimal(unit_value),
                        "quantidade_papeis": _to_int(amount),
                        "volume_total": _to_decimal(total_value),
                        "params": params or None,
                        "nextAnniversary": item.get("nextAnniversary"),
                        "outras_informacoes": {
                            "codigo_if": (item.get("instrumentCode") or "").strip()
                            or None,
                            "codigo_isin": (item.get("isinCode") or "").strip() or None,
                            "tipo": item.get("type"),
                            "icvm": item.get("icvm"),
                            "data_integralizacao": first_payment.isoformat()
                            if first_payment
                            else None,
                            "data_vencimento": str(item.get("dueDate") or "")[:10]
                            or None,
                            "taxa_juros_spread": interest_rate,
                            "indexador": indexador,
                            "volume_total": _to_decimal(total_value),
                            "quantidade_papeis": _to_int(amount),
                            "valor_unitario": _to_decimal(unit_value),
                        },
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
            doc_id = item.get("id")
            id_origem_arquivo = str(doc_id).strip() if doc_id is not None else None
            doc_type = item.get("type")
            description = item.get("description")
            titulo = doc_type
            if description and doc_type:
                titulo = f"{doc_type} ({description})"
            docs.append(
                DocumentoData(
                    link_documento=url,
                    id_origem_arquivo=id_origem_arquivo or None,
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


def _normalize_serie_numero(value: Any) -> str:
    """Normalize API série numbers like ``1.0`` → ``1``."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        as_float = float(raw)
        if as_float == int(as_float):
            return str(int(as_float))
    except (ValueError, TypeError, OverflowError):
        pass
    return raw


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def _series_indexer(item: dict, params: dict) -> str | None:
    indexer = item.get("indexer")
    if isinstance(indexer, dict):
        name = indexer.get("name")
        return str(name).strip() if name else None
    if indexer not in (None, ""):
        return str(indexer).strip()
    name = params.get("indexer")
    return str(name).strip() if name not in (None, "") else None


def _format_remuneracao(indexador: str | None, interest_rate: Any) -> str | None:
    """Build a human-readable remuneration string for the accordion taxa field."""
    rate = _to_decimal(interest_rate)
    if rate is None and not indexador:
        return None
    if rate is None:
        return indexador
    rate_txt = f"{rate:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if indexador:
        return f"{indexador} + {rate_txt}% a.a."
    return f"{rate_txt}% a.a."


def _counterparties_by_role(counterparties: Any) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    if not isinstance(counterparties, list):
        return roles
    for entry in counterparties:
        if not isinstance(entry, dict):
            continue
        role = (entry.get("type") or "").strip()
        name = (entry.get("businessName") or "").strip()
        if not role or not name:
            continue
        bucket = roles.setdefault(role, [])
        if name not in bucket:
            bucket.append(name)
    return roles


def _first_role_name(roles: dict[str, list[str]], role: str) -> str | None:
    names = roles.get(role) or []
    return names[0] if names else None


def _sum_series_amounts(series: list[SerieData]) -> int | None:
    total = 0
    found = False
    for serie in series:
        qty = serie.quantidade
        if qty is None and isinstance(serie.extras, dict):
            qty = serie.extras.get("quantidade_papeis")
        if qty is None:
            continue
        try:
            total += int(qty)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None
