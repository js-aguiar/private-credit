"""VERT scraper (https://data.vert-capital.app/).

VERT Data exposes a public JSON API behind the React SPA. All listing, series, and document
metadata is fetched with plain httpx calls — no headless browser needed.

API base: https://data.vert-capital.app
  List:      GET /api/emission-table?page={page}
             → {"registros": [...], "totalPaginas", "paginaAtual", ...}
  Documents: GET /api/documents-table/{emission_id}?category={name}&page={page}&page_size={size}
             → {"registros": [{id, name, s3PublicUrl, referenceDate, category, ...}], ...}

Document categories use Portuguese labels (e.g. ``Relatórios``), not enum codes.
Pagination is 0-based for documents and 1-based for the emission table.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from shared.parsing import parse_br_date
from shared.records import DetailResult, DocumentoData, EmissaoData, SerieData
from shared.scraper_base import BaseScraper

_API_BASE = "https://data.vert-capital.app"
_LIST_URL = f"{_API_BASE}/api/emission-table"
_DOCUMENTS_URL = f"{_API_BASE}/api/documents-table/{{emission_id}}"
_PORTAL_BASE = f"{_API_BASE}/emissao"
_DOCUMENT_PAGE_SIZE = 50

# Categories exposed on the documents tab (``Estoque`` is email-only — no API rows).
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


class VertScraper(BaseScraper):
    source_name = "vert"

    def list_emissoes(self):
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
                data = self._map_list_item(item)
                if data is not None:
                    yield data

            self.logger.info(
                "vert_list_page",
                extra={"page": page, "total_pages": total_pages, "items": len(items)},
            )
            if not items or page >= total_pages:
                break
            page += 1

    def _map_list_item(self, record: dict) -> EmissaoData | None:
        emission_id = record.get("id")
        if emission_id is None:
            return None
        id_origem = str(emission_id).strip()
        if not id_origem:
            return None

        series = record.get("series") or []
        cetip_codes = [
            str(s.get("codeCetip")).strip()
            for s in series
            if isinstance(s, dict) and s.get("codeCetip")
        ]
        isin_codes = [
            str(s.get("codeIsin")).strip()
            for s in series
            if isinstance(s, dict) and s.get("codeIsin")
        ]
        series_numbers = [
            _normalize_serie_numero(s.get("seriesNumber"))
            for s in series
            if isinstance(s, dict) and s.get("seriesNumber") is not None
        ]
        series_numbers = [n for n in series_numbers if n]

        return EmissaoData(
            fonte=self.source_name,
            id_origem=id_origem,
            link=f"{_PORTAL_BASE}/{id_origem}/referencia/default/documentos",
            isin=isin_codes[0] if isin_codes else None,
            numero_emissao=str(record.get("number")) if record.get("number") is not None else None,
            codigos_cetip=" ".join(cetip_codes) or None,
            operacao=record.get("name"),
            devedor=record.get("originator"),
            tipo_ativo=record.get("financialTitle"),
            series_raw="-".join(series_numbers) if series_numbers else None,
            valor_total=self._parse_decimal(record.get("volume")),
            data_emissao=parse_br_date(str(record.get("date") or "")[:10]),
            extras={
                "external_id": record.get("external_id"),
                "concentration": record.get("concentration"),
                "series": series,
                "lastReport": record.get("lastReport"),
            },
        )

    def fetch_detail(self, emissao) -> DetailResult:
        extras = emissao.extras or {}
        series_source = extras.get("series") or []
        series = self._extract_series(series_source, emissao)
        documentos = self._fetch_documents(emissao.id_origem, emissao)
        documentos = self._append_last_report(documentos, extras.get("lastReport"), emissao)

        return DetailResult(
            emissao_updates={
                "extras": {
                    "detalhe_acessivel": bool(series or documentos),
                    "external_id": extras.get("external_id"),
                    "lastReport": extras.get("lastReport"),
                }
            },
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
            numero = _normalize_serie_numero(item.get("seriesNumber"))
            if not numero:
                continue
            series.append(
                SerieData(
                    numero_serie=numero,
                    isin=(item.get("codeIsin") or "").strip() or None,
                    numero_emissao=emissao.numero_emissao,
                    codigo_cetip=(item.get("codeCetip") or "").strip() or None,
                    remuneracao=(
                        str(item.get("tax")).strip() if item.get("tax") is not None else None
                    ),
                    indexador=(item.get("typeName") or item.get("taxType") or "").strip() or None,
                    data_vencimento=parse_br_date(str(item.get("due_date") or "")[:10]),
                    extras={
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "seriesClass": item.get("seriesClass"),
                        "financialTitle": item.get("financialTitle"),
                        "isClosed": item.get("isClosed"),
                    },
                )
            )
        return series

    def _fetch_documents(self, emission_id: str, emissao) -> list[DocumentoData]:
        docs: list[DocumentoData] = []
        seen: set[str] = set()

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

                if payload.get("error"):
                    break

                rows: list[dict] = payload.get("registros") or []
                total_pages = int(payload.get("totalPaginas") or 0)

                for item in rows:
                    doc = self._map_document(item, emissao)
                    if doc is None:
                        continue
                    # Prefer canonical URL for in-run dedupe: the same S3 object can appear
                    # under multiple Vert document ids / categories.
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

        return docs

    def _map_document(self, item: dict, emissao) -> DocumentoData | None:
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
            numero_emissao=emissao.numero_emissao,
            codigo_cetip=(
                (emissao.codigos_cetip or "").split()[0] if emissao.codigos_cetip else None
            ),
            extras={**item, "vert_document_id": doc_id},
        )

    def _append_last_report(
        self,
        documentos: list[DocumentoData],
        last_report: Any,
        emissao,
    ) -> list[DocumentoData]:
        if not isinstance(last_report, dict):
            return documentos

        url = self._canonical_document_url(last_report.get("path"))
        if not url:
            return documentos

        if any(doc.link_documento == url for doc in documentos):
            return documentos

        # lastReport often lacks a numeric document id; fall back to link-only dedupe.
        report_id = last_report.get("id")
        id_origem_arquivo = str(report_id).strip() if report_id is not None else None

        documentos.append(
            DocumentoData(
                link_documento=url,
                id_origem_arquivo=id_origem_arquivo or None,
                titulo=last_report.get("name"),
                tipo_documento="Relatórios",
                data_documento=parse_br_date(str(last_report.get("referenceDate") or "")[:10]),
                numero_emissao=emissao.numero_emissao,
                codigo_cetip=(emissao.codigos_cetip or "").split()[0]
                if emissao.codigos_cetip
                else None,
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
