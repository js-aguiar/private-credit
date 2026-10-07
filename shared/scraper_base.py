"""Abstract base class implementing the shared discover + re-check workflow.

Concrete scrapers implement:
- ``source_name`` (class attribute)
- ``list_series()`` -> iterable of ``SerieData`` (catalog listing / discovery)
- ``fetch_detail(serie)`` -> ``DetailResult`` (one série's detail page)

The base class handles: upserting séries (with skip/warn rules), selecting which
séries to (re-)visit, respecting the time budget, upserting documents and M2M
links, marking re-check timestamps, and error isolation.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from typing import Any, Iterable

from .config import ScraperConfig
from .db import ensure_schema, session_scope
from .http_client import PoliteClient
from .logging_config import get_logger
from .models import Serie
from .records import DetailResult, SerieData
from .repository import (
    apply_serie_detail,
    count_series,
    link_documento_series,
    mark_serie_detailed,
    resolve_serie_ids_by_emissao_id,
    resolve_serie_ids_by_origem,
    select_series_para_detalhe,
    upsert_documento,
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


class DeadlineTimeBudget(TimeBudget):
    """Hard monotonic deadline for long-running hosts (e.g. EC2 backfill)."""

    def __init__(self, deadline_monotonic: float, reserve_ms: int = 90_000):
        super().__init__(context=None, reserve_ms=reserve_ms)
        self._deadline = deadline_monotonic

    def remaining_ms(self) -> float:
        return max(0.0, (self._deadline - time.monotonic()) * 1000.0)


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
    def list_series(self) -> Iterable[SerieData]:
        """Yield every série from the catalog listing (list-level fields)."""

    @abstractmethod
    def fetch_detail(self, serie: Serie) -> DetailResult:
        """Fetch one série's detail page: updates + sibling séries + documents."""

    # -- orchestration ------------------------------------------------------------
    def run(self) -> dict:
        summary = {
            "source": self.source_name,
            "descobertas": 0,
            "series_puladas": 0,
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
        """Fetch the listing and upsert every série that has ISIN and/or CETIP."""
        self.logger.info("discovery_start", extra={"source": self.source_name})
        with session_scope(self.config) as session:
            for data in self.list_series():
                try:
                    result = upsert_serie(session, data)
                    if result.status.startswith("skipped"):
                        summary["series_puladas"] += 1
                    else:
                        summary["descobertas"] += 1
                        summary["series_gravadas"] += 1
                except Exception as exc:
                    summary["erros"] += 1
                    self.logger.warning(
                        "discovery_upsert_error",
                        extra={"id_origem": data.id_origem, "error": str(exc)},
                    )
            total = count_series(session, self.source_name)
        self.logger.info(
            "discovery_done",
            extra={
                "descobertas": summary["descobertas"],
                "series_puladas": summary["series_puladas"],
                "total_no_banco": total,
            },
        )

    def _process_details(self, summary: dict) -> None:
        """Visit séries needing detail/re-check until the time budget runs out."""
        with session_scope(self.config) as session:
            # Full EC2 backfill drains never-detailed rows only; Lambdas also re-check.
            include_recheck = os.getenv("EXECUTION_MODE", "").strip().lower() != "ec2_backfill"
            pending = select_series_para_detalhe(
                session,
                self.source_name,
                limit=self.config.detail_batch_limit,
                include_recheck=include_recheck,
            )
            self.logger.info(
                "detail_queue",
                extra={"pendentes": len(pending), "include_recheck": include_recheck},
            )

            for serie in pending:
                if not self.budget.has_time():
                    summary["interrompido_por_tempo"] = True
                    self.logger.info(
                        "detail_time_budget_reached",
                        extra={"restante_ms": self.budget.remaining_ms()},
                    )
                    break
                self._process_single_detail(session, serie, summary)

    def _process_single_detail(self, session, serie: Serie, summary: dict) -> None:
        try:
            result = self.fetch_detail(serie)
        except Exception as exc:
            summary["erros"] += 1
            self.logger.warning(
                "detail_fetch_error",
                extra={
                    "serie_id": serie.serie_id,
                    "id_origem": serie.id_origem,
                    "error": str(exc),
                },
            )
            return

        try:
            sibling_ids: list[int] = []
            for sibling in result.series:
                upsert_result = upsert_serie(session, sibling)
                if upsert_result.serie_id is not None:
                    sibling_ids.append(upsert_result.serie_id)
                    summary["series_gravadas"] += 1
                elif upsert_result.status.startswith("skipped"):
                    summary["series_puladas"] += 1

            apply_serie_detail(session, serie.serie_id, result.serie_updates)
            # Sibling séries discovered in the same detail payload are also "seen".
            for sibling_id in sibling_ids:
                if sibling_id != serie.serie_id:
                    mark_serie_detailed(session, sibling_id)

            default_serie_ids = [serie.serie_id]
            for documento in result.documentos:
                if not documento.link_documento:
                    continue
                if not documento.emissao_id and serie.emissao_id:
                    documento.emissao_id = serie.emissao_id
                doc_id = upsert_documento(session, self.source_name, documento)
                if doc_id is None:
                    continue
                link_ids = default_serie_ids
                if documento.serie_id_origens:
                    resolved = resolve_serie_ids_by_origem(
                        session, self.source_name, documento.serie_id_origens
                    )
                    if resolved:
                        link_ids = resolved
                elif documento.emissao_id:
                    by_emissao = resolve_serie_ids_by_emissao_id(
                        session, self.source_name, documento.emissao_id
                    )
                    if by_emissao:
                        link_ids = by_emissao
                link_documento_series(session, doc_id, link_ids)
                summary["documentos_gravados"] += 1

            session.commit()
            summary["detalhes_processados"] += 1
        except Exception as exc:
            session.rollback()
            summary["erros"] += 1
            self.logger.warning(
                "detail_persist_error",
                extra={"serie_id": serie.serie_id, "error": str(exc)},
            )
