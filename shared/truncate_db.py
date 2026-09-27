"""Delete all scraper table rows (documentos_series, documentos, series)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from .config import ScraperConfig
from .db import get_engine
from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TruncateSummary:
    series: int
    documentos: int
    documentos_series: int = 0


@dataclass
class DeleteFonteSummary:
    fonte: str
    series: int
    documentos: int
    documentos_series: int = 0


def delete_fonte_rows(
    fonte: str, config: ScraperConfig | None = None
) -> DeleteFonteSummary:
    """Delete all rows for one ``fonte`` (docs/junction cascade from series deletes)."""
    cfg = config or ScraperConfig.from_env("admin")
    engine = get_engine(cfg)
    fonte = (fonte or "").strip().lower()
    if not fonte:
        raise ValueError("fonte is required")

    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout = '60s'"))
        before = {
            "series": int(
                conn.execute(
                    text("SELECT COUNT(*) FROM series WHERE fonte = :fonte"),
                    {"fonte": fonte},
                ).scalar_one()
            ),
            "documentos": int(
                conn.execute(
                    text("SELECT COUNT(*) FROM documentos WHERE fonte = :fonte"),
                    {"fonte": fonte},
                ).scalar_one()
            ),
            "documentos_series": int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM documentos_series ds
                        JOIN series s ON s.serie_id = ds.serie_id
                        WHERE s.fonte = :fonte
                        """
                    ),
                    {"fonte": fonte},
                ).scalar_one()
            ),
        }
        # Delete documents for this fonte (junction rows cascade).
        conn.execute(text("DELETE FROM documentos WHERE fonte = :fonte"), {"fonte": fonte})
        conn.execute(text("DELETE FROM series WHERE fonte = :fonte"), {"fonte": fonte})

    summary = DeleteFonteSummary(fonte=fonte, **before)
    logger.info(
        "delete_fonte_done",
        extra={
            "fonte": fonte,
            "series_removed": summary.series,
            "documentos_removed": summary.documentos,
            "documentos_series_removed": summary.documentos_series,
        },
    )
    return summary


def truncate_all_tables(config: ScraperConfig | None = None) -> TruncateSummary:
    """Remove every row from the core tables and reset identity sequences."""
    cfg = config or ScraperConfig.from_env("admin")
    engine = get_engine(cfg)

    before = {"series": 0, "documentos": 0, "documentos_series": 0}
    last_error: Exception | None = None

    for attempt in range(1, 6):
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '30s'"))
                before = {
                    "series": int(
                        conn.execute(text("SELECT COUNT(*) FROM series")).scalar_one()
                    ),
                    "documentos": int(
                        conn.execute(text("SELECT COUNT(*) FROM documentos")).scalar_one()
                    ),
                    "documentos_series": int(
                        conn.execute(
                            text("SELECT COUNT(*) FROM documentos_series")
                        ).scalar_one()
                    ),
                }
                conn.execute(text("DELETE FROM documentos_series"))
                conn.execute(text("DELETE FROM documentos"))
                conn.execute(text("DELETE FROM series"))
                conn.execute(text("DELETE FROM isin_contestados"))
                conn.execute(text("ALTER SEQUENCE IF EXISTS series_serie_id_seq RESTART WITH 1"))
                conn.execute(
                    text("ALTER SEQUENCE IF EXISTS documentos_documento_id_seq RESTART WITH 1")
                )
            last_error = None
            break
        except OperationalError as exc:
            last_error = exc
            logger.warning(
                "truncate_retry",
                extra={"attempt": attempt, "error": str(exc).splitlines()[0]},
            )
            time.sleep(min(5 * attempt, 20))

    if last_error is not None:
        raise last_error

    summary = TruncateSummary(**before)
    logger.info(
        "truncate_all_done",
        extra={
            "series_removed": summary.series,
            "documentos_removed": summary.documentos,
            "documentos_series_removed": summary.documentos_series,
        },
    )
    return summary
