"""Abstract base class implementing the shared discover + re-check workflow.

Concrete scrapers implement three things:
- ``source_name`` (class attribute)
- ``list_emissoes()`` -> iterable of ``EmissaoData`` (the catalog listing)
- ``fetch_detail(emissao)`` -> ``DetailResult`` (one operation's detail page)

The base class handles: upserting the list, selecting which operations to (re-)visit,
respecting the Lambda time budget, upserting séries/documents, marking re-check
timestamps, and error isolation so one bad operation never aborts the whole run.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Iterable

from .config import ScraperConfig
from .db import ensure_schema, session_scope
from .http_client import PoliteClient
from .logging_config import get_logger
from .models import Emissao
from .records import DetailResult, EmissaoData
from .repository import (
    apply_emissao_detail,
    count_emissoes,
    select_emissoes_para_detalhe,
    upsert_documento,
    upsert_emissao,
    upsert_serie,
)


class TimeBudget:
    """Abstracts the remaining execution time (Lambda-aware, unlimited locally)."""

    def __init__(self, context: Any = None, reserve_ms: int = 90_000):
        self._context = context
        self._reserve_ms = reserve_ms
        self._start = time.monotonic()

    def remaining_ms(self) -> float:
        if self._context is not None and hasattr(self._context, "get_remaining_time_in_millis"):
            return float(self._context.get_remaining_time_in_millis())
        return float("inf")

    def has_time(self) -> bool:
        return self.remaining_ms() > self._reserve_ms


class BaseScraper(ABC):
    source_name: str = "base"

    def __init__(self, config: ScraperConfig, context: Any = None):
        self.config = config
        self.context = context
        self.budget = TimeBudget(context, reserve_ms=config.time_reserve_ms)
        self.logger = get_logger(f"scraper.{self.source_name}")
        self.client = PoliteClient(config)

    # -- lifecycle ----------------------------------------------------------------
    @classmethod
    def from_env(cls, context: Any = None) -> "BaseScraper":
        config = ScraperConfig.from_env(cls.source_name)
        return cls(config, context=context)

    def close(self) -> None:
        self.client.close()

    # -- abstract API to implement per site --------------------------------------
    @abstractmethod
    def list_emissoes(self) -> Iterable[EmissaoData]:
        """Yield every emission from the catalog listing (list-level fields)."""

    @abstractmethod
    def fetch_detail(self, emissao: Emissao) -> DetailResult:
        """Fetch one operation's detail page: updates + séries + documents."""

    # -- orchestration ------------------------------------------------------------
    def run(self) -> dict:
        summary = {
            "source": self.source_name,
            "descobertas": 0,
            "detalhes_processados": 0,
            "series_gravadas": 0,
            "documentos_gravados": 0,
            "erros": 0,
            "interrompido_por_tempo": False,
        }
        try:
            if self.config.auto_create_schema:
                ensure_schema(self.config)

            self._discover(summary)
            self._process_details(summary)
        finally:
            self.close()

        self.logger.info("run_complete", extra=summary)
        return summary

    def _discover(self, summary: dict) -> None:
        """Fetch the listing and upsert every emission (new + existing)."""
        self.logger.info("discovery_start", extra={"source": self.source_name})
        with session_scope(self.config) as session:
            for data in self.list_emissoes():
                try:
                    upsert_emissao(session, data)
                    summary["descobertas"] += 1
                except Exception as exc:
                    summary["erros"] += 1
                    self.logger.warning(
                        "discovery_upsert_error",
                        extra={"id_origem": data.id_origem, "error": str(exc)},
                    )
            total = count_emissoes(session, self.source_name)
        self.logger.info(
            "discovery_done",
            extra={"descobertas": summary["descobertas"], "total_no_banco": total},
        )

    def _process_details(self, summary: dict) -> None:
        """Visit operations needing detail/re-check until the time budget runs out."""
        with session_scope(self.config) as session:
            pending = select_emissoes_para_detalhe(
                session, self.source_name, limit=self.config.detail_batch_limit
            )
            self.logger.info("detail_queue", extra={"pendentes": len(pending)})

            for emissao in pending:
                if not self.budget.has_time():
                    summary["interrompido_por_tempo"] = True
                    self.logger.info(
                        "detail_time_budget_reached",
                        extra={"restante_ms": self.budget.remaining_ms()},
                    )
                    break
                self._process_single_detail(session, emissao, summary)

    def _process_single_detail(self, session, emissao: Emissao, summary: dict) -> None:
        try:
            result = self.fetch_detail(emissao)
        except Exception as exc:
            summary["erros"] += 1
            self.logger.warning(
                "detail_fetch_error",
                extra={
                    "emissao_id": emissao.emissao_id,
                    "id_origem": emissao.id_origem,
                    "error": str(exc),
                },
            )
            return

        try:
            for serie in result.series:
                upsert_serie(session, emissao.emissao_id, self.source_name, serie)
                summary["series_gravadas"] += 1
            for documento in result.documentos:
                if not documento.link_documento:
                    continue
                upsert_documento(session, emissao.emissao_id, self.source_name, documento)
                summary["documentos_gravados"] += 1

            apply_emissao_detail(session, emissao.emissao_id, result.emissao_updates)
            session.commit()
            summary["detalhes_processados"] += 1
        except Exception as exc:
            session.rollback()
            summary["erros"] += 1
            self.logger.warning(
                "detail_persist_error",
                extra={"emissao_id": emissao.emissao_id, "error": str(exc)},
            )
