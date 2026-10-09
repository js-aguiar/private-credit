"""VERT scraper (https://data.vert-capital.app/).

VERT Data exposes a public JSON API behind the React SPA. All listing, series, and document
metadata is fetched with plain httpx calls — no headless browser needed.

API base: https://data.vert-capital.app
  List:      GET /api/emission-table?page={page}
             → {"registros": [...], "totalPaginas", "paginaAtual", ...}
  Documents: GET /api/documents-table/{emission_id}?category={name}&page={page}&page_size={size}

Document GETs are emission-scoped. Sibling séries share ``emissao_id``, so detail work
fetches docs once per emission (in-process cache + sibling ``detalhes_coletados`` marks).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from shared.parsing import parse_br_date
from shared.records import DetailResult, DocumentoData, SerieData
from shared.scraper_base import BaseScraper

_API_BASE = "https://data.vert-capital.app"
_LIST_URL = f"{_API_BASE}/api/emission-table"
_DOCUMENTS_URL = f"{_API_BASE}/api/documents-table/{{emission_id}}"
_PORTAL_BASE = f"{_API_BASE}/emissao"
_DOCUMENT_PAGE_SIZE = 50
FONTE = "vert"

_DOCUMENT_CATEGORIES = (
    "Relatórios",
    "Comunicados",
    "Assembléias",
    "Documentos da oferta",
    "Termo de Securitização, Aditamentos e Escrituras",
    "Garantias",
    "Índices",
    "Regulamentos",
    "Carteira",
)


def _date_to_iso(value: date | None) -> str | None:
    return value.isoformat() if isinstance(value, date) else None


class VertScraper(BaseScraper):
    source_name = "vert"

    def __init__(self, config, context=None):
        super().__init__(config, context=context)
        # Documents are emission-scoped; cache once per emission per process.
        self._docs_by_emission: dict[str, list[DocumentoData]] = {}

    def list_series(self):
        page = 1
        while True:
            try:
                payload = self.client.get_json(_LIST_URL, params={"page": page}) or {}
            except Exception as exc:
                self.logger.warning("vert_list_error", extra={"page": page, "error": str(exc)})
                break

            items: list[dict] = payload.get("registros") or []
            total_pages = int(payload.get("totalPaginas") or 1)

            for item in items:
                for serie in self._map_list_item_to_series(item):
                    yield serie

            self.logger.info(
                "vert_list_page",
                extra={"page": page, "total_pages": total_pages, "items": len(items)},
            )
            if not items or page >= total_pages:
                break
            page += 1

    def _map_list_item_to_series(self, record: dict) -> list[SerieData]:
        emission_id = record.get("id")
        if emission_id is None:
            return []
        id_emissao = str(emission_id).strip()
        if not id_emissao:
            return []

        numero_emissao = (
            str(record.get("number")) if record.get("number") is not None else None
        )
        common = {
            "fonte": FONTE,
            "emissao_id": id_emissao,
            "link": f"{_PORTAL_BASE}/{id_emissao}/referencia/default/documentos",
            "numero_emissao": numero_emissao,
            "operacao": record.get("name"),
            "devedor": record.get("originator"),
            "tipo_ativo": record.get("financialTitle"),
            "valor_total": self._parse_decimal(record.get("volume")),
            "data_emissao": parse_br_date(str(record.get("date") or "")[:10]),
        }
        extras_base = {
            "external_id": record.get("external_id"),
            "concentration": record.get("concentration"),
            "lastReport": record.get("lastReport"),
            "emission_id": id_emissao,
        }

        out: list[SerieData] = []
        for item in record.get("series") or []:
            if not isinstance(item, dict):
                continue
            numero = _normalize_serie_numero(item.get("seriesNumber"))
            if not numero:
                continue
            isin = (item.get("codeIsin") or "").strip() or None
            cetip = (item.get("codeCetip") or "").strip() or None
            if not (isin or cetip):
                continue
            id_origem = f"{id_emissao}:{cetip or isin or numero}"
            out.append(
                SerieData(
                    id_origem=id_origem,
                    numero_serie=numero,
                    isin=isin,
                    codigo_cetip=cetip,
                    remuneracao=(
                        str(item.get("tax")).strip() if item.get("tax") is not None else None
                    ),
                    indexador=(item.get("typeName") or item.get("taxType") or "").strip()
                    or None,
                    data_vencimento=parse_br_date(str(item.get("due_date") or "")[:10]),
                    extras={
                        **extras_base,
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "seriesClass": item.get("seriesClass"),
                        "financialTitle": item.get("financialTitle"),
                        "isClosed": item.get("isClosed"),
                    },
                    **common,
                )
            )

        # Stash siblings so fetch_detail can mark them detailed without re-fetching docs.
        sibling_series = [self._serie_to_sibling_payload(serie) for serie in out]
        for serie in out:
            serie.extras = {**(serie.extras or {}), "sibling_series": sibling_series}
        return out

    @staticmethod
    def _serie_to_sibling_payload(serie: SerieData) -> dict:
        extras = dict(serie.extras or {})
        extras.pop("sibling_series", None)
        return {
            "id_origem": serie.id_origem,
            "numero_serie": serie.numero_serie,
            "isin": serie.isin,
            "codigo_cetip": serie.codigo_cetip,
            "emissao_id": serie.emissao_id,
            "link": serie.link,
            "numero_emissao": serie.numero_emissao,
            "operacao": serie.operacao,
            "devedor": serie.devedor,
            "tipo_ativo": serie.tipo_ativo,
            "valor_total": str(serie.valor_total) if serie.valor_total is not None else None,
            "remuneracao": serie.remuneracao,
            "indexador": serie.indexador,
            "data_emissao": _date_to_iso(serie.data_emissao),
            "data_vencimento": _date_to_iso(serie.data_vencimento),
            "extras": extras,
        }

    def _sibling_payload_to_serie(self, payload: dict) -> SerieData | None:
        if not isinstance(payload, dict):
            return None
        id_origem = (payload.get("id_origem") or "").strip()
        if not id_origem:
            return None
        valor_raw = payload.get("valor_total")
        return SerieData(
            fonte=FONTE,
            id_origem=id_origem,
            numero_serie=str(payload.get("numero_serie") or ""),
            isin=(payload.get("isin") or None),
            codigo_cetip=(payload.get("codigo_cetip") or None),
            emissao_id=(payload.get("emissao_id") or None),
            link=payload.get("link"),
            numero_emissao=payload.get("numero_emissao"),
            operacao=payload.get("operacao"),
            devedor=payload.get("devedor"),
            tipo_ativo=payload.get("tipo_ativo"),
            valor_total=self._parse_decimal(valor_raw),
            remuneracao=payload.get("remuneracao"),
            indexador=payload.get("indexador"),
            data_emissao=parse_br_date(str(payload.get("data_emissao") or "")[:10]),
            data_vencimento=parse_br_date(str(payload.get("data_vencimento") or "")[:10]),
            extras=dict(payload.get("extras") or {}),
        )

    def fetch_detail(self, serie) -> DetailResult:
        emission_id = serie.emissao_id or serie.id_origem.split(":", 1)[0]
        extras = serie.extras or {}
        series: list[SerieData] = []
        sibling_source = extras.get("sibling_series") if isinstance(extras, dict) else None
        if isinstance(sibling_source, list):
            for payload in sibling_source:
                sibling = self._sibling_payload_to_serie(payload)
                if sibling is None or sibling.id_origem == serie.id_origem:
                    continue
                series.append(sibling)

        documentos = self._fetch_documents(emission_id, serie)
        documentos = self._append_last_report(
            documentos, extras.get("lastReport") if isinstance(extras, dict) else None, serie
        )

        return DetailResult(
            serie_updates={
                "extras": {
                    "detalhe_acessivel": bool(documentos),
                    "external_id": extras.get("external_id")
                    if isinstance(extras, dict)
                    else None,
                    "lastReport": extras.get("lastReport")
                    if isinstance(extras, dict)
                    else None,
                }
            },
            series=series,
            documentos=documentos,
        )

    def _fetch_documents(self, emission_id: str, serie) -> list[DocumentoData]:
        cache_key = str(emission_id).strip()
        cached = self._docs_by_emission.get(cache_key)
        if cached is not None:
            self.logger.info(
                "vert_docs_cache_hit",
                extra={"emissao_id": cache_key, "documentos": len(cached)},
            )
            # Shallow copy so _append_last_report cannot mutate the cache entry.
            return list(cached)

        docs: list[DocumentoData] = []
        seen: set[str] = set()
        pages_fetched = 0

        for category in _DOCUMENT_CATEGORIES:
            page = 0
            while True:
                try:
                    payload = self.client.get_json(
                        _DOCUMENTS_URL.format(emission_id=emission_id),
                        params={
                            "category": category,
                            "page": page,
                            "page_size": _DOCUMENT_PAGE_SIZE,
                        },
                    ) or {}
                except Exception as exc:
                    self.logger.warning(
                        "vert_documents_error",
                        extra={
                            "id_origem": emission_id,
                            "category": category,
                            "page": page,
                            "error": str(exc),
                        },
                    )
                    break

                pages_fetched += 1
                if payload.get("error"):
                    break

                rows: list[dict] = payload.get("registros") or []
                total_pages = int(payload.get("totalPaginas") or 0)

                for item in rows:
                    doc = self._map_document(item, serie, emission_id)
                    if doc is None:
                        continue
                    link_key = doc.link_documento or ""
                    id_key = str((doc.extras or {}).get("vert_document_id") or "")
                    if (link_key and link_key in seen) or (id_key and id_key in seen):
                        continue
                    if link_key:
                        seen.add(link_key)
                    if id_key:
                        seen.add(id_key)
                    docs.append(doc)

                if not rows or page + 1 >= total_pages:
                    break
                page += 1

        self._docs_by_emission[cache_key] = docs
        self.logger.info(
            "vert_docs_cache_miss",
            extra={
                "emissao_id": cache_key,
                "documentos": len(docs),
                "categories": len(_DOCUMENT_CATEGORIES),
                "pages_fetched": pages_fetched,
            },
        )
        return docs

    def _map_document(self, item: dict, serie, emission_id: str) -> DocumentoData | None:
        if item.get("isNeedInfoDownload"):
            return None

        url = self._canonical_document_url(item.get("s3PublicUrl"))
        if not url:
            return None

        doc_id = item.get("id")
        id_origem_arquivo = str(doc_id).strip() if doc_id is not None else None
        return DocumentoData(
            link_documento=url,
            id_origem_arquivo=id_origem_arquivo or None,
            titulo=item.get("name"),
            tipo_documento=item.get("category"),
            data_documento=parse_br_date(str(item.get("referenceDate") or "")[:10]),
            numero_emissao=serie.numero_emissao,
            codigo_cetip=serie.codigo_cetip,
            emissao_id=emission_id,
            extras={**item, "vert_document_id": doc_id},
        )

    def _append_last_report(
        self,
        documentos: list[DocumentoData],
        last_report: Any,
        serie,
    ) -> list[DocumentoData]:
        if not isinstance(last_report, dict):
            return documentos

        url = self._canonical_document_url(last_report.get("path"))
        if not url:
            return documentos

        if any(doc.link_documento == url for doc in documentos):
            return documentos

        report_id = last_report.get("id")
        id_origem_arquivo = str(report_id).strip() if report_id is not None else None

        documentos.append(
            DocumentoData(
                link_documento=url,
                id_origem_arquivo=id_origem_arquivo or None,
                titulo=last_report.get("name"),
                tipo_documento="Relatórios",
                data_documento=parse_br_date(str(last_report.get("referenceDate") or "")[:10]),
                numero_emissao=serie.numero_emissao,
                codigo_cetip=serie.codigo_cetip,
                emissao_id=serie.emissao_id,
                extras={**last_report, "vert_document_id": report_id},
            )
        )
        return documentos

    @staticmethod
    def _parse_decimal(value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _canonical_document_url(url: str | None) -> str | None:
        if not url or not isinstance(url, str):
            return None
        parts = urlsplit(url.strip())
        if not parts.scheme or not parts.netloc:
            return url.strip()
        host = parts.netloc.replace(".s3.sa-east-1.amazonaws.com", ".s3.amazonaws.com")
        return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _normalize_serie_numero(value: Any) -> str:
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
