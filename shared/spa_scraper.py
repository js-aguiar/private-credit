"""Base class for SPA (JavaScript single-page-app) sources.

Implements the hybrid strategy shared by opea/riza/vert:

1. API-first: if an API URL template is configured (constant or via env var), page
   through it with the polite HTTP client.
2. Browser fallback: otherwise (or on failure), load the page in headless Chromium and
   capture the JSON the SPA fetches for itself off the network.

Subclasses provide the site specifics: listing/detail URLs, API templates, pagination
parameter names, and the field mapping (``map_emissao`` is required).
"""

from __future__ import annotations

import os
from abc import abstractmethod
from typing import Any

from .mapping import as_records, find_document_dicts, pick
from .parsing import parse_br_date, parse_brl_amount
from .records import DetailResult, DocumentoData, EmissaoData, SerieData
from .scraper_base import BaseScraper


class SpaScraper(BaseScraper):
    # -- site specifics (override in subclasses) ---------------------------------
    listing_url: str = ""
    #: API URL template for the listing; use ``{page}`` and ``{size}`` placeholders.
    api_list_url_template: str | None = None
    #: API URL template for a detail page; use ``{id}`` placeholder.
    api_detail_url_template: str | None = None
    #: Substring identifying data responses when capturing via the browser. Empty
    #: string captures all responses; the richest JSON payload is then selected.
    json_marker: str = ""
    page_size: int = 50
    max_pages: int = 200
    #: One-based first page number for the API.
    first_page: int = 1

    def __init__(self, config, context=None):
        super().__init__(config, context=context)
        prefix = self.source_name.upper()
        # Allow operators to inject the confirmed API endpoints without code changes.
        self.api_list_url_template = os.getenv(
            f"{prefix}_API_LIST_URL", self.api_list_url_template
        )
        self.api_detail_url_template = os.getenv(
            f"{prefix}_API_DETAIL_URL", self.api_detail_url_template
        )
        self.json_marker = os.getenv(f"{prefix}_JSON_MARKER", self.json_marker)

    # -- required mapping hook ----------------------------------------------------
    @abstractmethod
    def map_emissao(self, record: dict) -> EmissaoData | None:
        """Map one listing record (dict) into an EmissaoData (or None to skip)."""

    # -- optional mapping hooks ---------------------------------------------------
    def map_series(self, detail_payload: Any, emissao) -> list[SerieData]:
        """Override to extract per-série rows from a detail payload."""
        return []

    def build_documento(self, doc: dict, emissao) -> DocumentoData | None:
        url = pick(doc, "url", "link", "arquivo", "href", "path", "downloadUrl", "urlArquivo")
        if not url:
            return None
        return DocumentoData(
            link_documento=url,
            titulo=pick(doc, "titulo", "title", "nome", "name", "descricao", "label"),
            tipo_documento=pick(doc, "tipo", "type", "categoria", "category", "tipoDocumento"),
            data_documento=parse_br_date(
                pick(doc, "data", "date", "dataDocumento", "dataPublicacao", "dtDocumento")
            ),
            numero_emissao=emissao.numero_emissao,
            extras=doc,
        )

    def map_detail(self, emissao, detail_payload: Any) -> DetailResult:
        documentos: list[DocumentoData] = []
        for doc in find_document_dicts(detail_payload):
            built = self.build_documento(doc, emissao)
            if built:
                documentos.append(built)
        series = self.map_series(detail_payload, emissao)
        updates = {"extras": {"detalhe_fonte": "api_or_browser"}}
        return DetailResult(emissao_updates=updates, series=series, documentos=documentos)

    # -- orchestration: listing ---------------------------------------------------
    def list_emissoes(self):
        for record in self._collect_list_records():
            data = self.map_emissao(record)
            if data is not None:
                yield data

    def _collect_list_records(self) -> list[dict]:
        if self.api_list_url_template:
            try:
                records = self._list_via_api()
                if records:
                    return records
                self.logger.info("api_list_empty_fallback_browser")
            except Exception as exc:
                self.logger.warning("api_list_failed", extra={"error": str(exc)})
        if self.config.use_browser_fallback:
            try:
                return self._list_via_browser()
            except Exception as exc:
                self.logger.warning("browser_list_failed", extra={"error": str(exc)})
        return []

    def _list_via_api(self) -> list[dict]:
        records: list[dict] = []
        for page in range(self.first_page, self.first_page + self.max_pages):
            url = self.api_list_url_template.format(page=page, size=self.page_size)
            payload = self.client.get_json(url)
            page_records = as_records(payload)
            if not page_records:
                break
            records.extend(page_records)
            if len(page_records) < self.page_size:
                break
        self.logger.info("api_list_ok", extra={"registros": len(records)})
        return records

    def _list_via_browser(self) -> list[dict]:
        from .browser import BrowserFetcher

        with BrowserFetcher(self.config) as browser:
            payloads = browser.capture_json(self.listing_url, self.json_marker)
        # Pick the captured JSON response that yields the most records (the listing),
        # instead of blindly merging every response (which may include unrelated JSON).
        best: list[dict] = []
        for payload in payloads:
            candidate = as_records(payload)
            if len(candidate) > len(best):
                best = candidate
        self.logger.info("browser_list_ok", extra={"registros": len(best)})
        return best

    # -- orchestration: detail ----------------------------------------------------
    def fetch_detail(self, emissao) -> DetailResult:
        payload = self._collect_detail_payload(emissao)
        if payload is None:
            # Nothing retrieved this run; keep it resumable but don't crash.
            return DetailResult(emissao_updates={"extras": {"detalhe_acessivel": False}})
        return self.map_detail(emissao, payload)

    def _collect_detail_payload(self, emissao) -> Any:
        if self.api_detail_url_template:
            try:
                url = self.api_detail_url_template.format(id=emissao.id_origem)
                return self.client.get_json(url)
            except Exception as exc:
                self.logger.warning(
                    "api_detail_failed",
                    extra={"id_origem": emissao.id_origem, "error": str(exc)},
                )
        if self.config.use_browser_fallback and emissao.link:
            try:
                from .browser import BrowserFetcher

                with BrowserFetcher(self.config) as browser:
                    payloads = browser.capture_json(emissao.link, self.json_marker)
                return payloads
            except Exception as exc:
                self.logger.warning(
                    "browser_detail_failed",
                    extra={"id_origem": emissao.id_origem, "error": str(exc)},
                )
        return None

    # -- shared mapping helpers ---------------------------------------------------
    @staticmethod
    def money(record: dict, *keys: str):
        value = pick(record, *keys)
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        return parse_brl_amount(str(value))
