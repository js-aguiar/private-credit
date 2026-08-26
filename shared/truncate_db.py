"""Delete all scraper table rows (documentos, series, emissoes)."""

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
    emissoes: int
    series: int
    documentos: int


@dataclass
class DeleteFonteSummary:
    fonte: str
    emissoes: int
    series: int
    documentos: int


def delete_fonte_rows(
    fonte: str, config: ScraperConfig | None = None
) -> DeleteFonteSummary:
    """Delete all rows for one ``fonte`` (series/docs cascade from emissoes)."""
    cfg = config or ScraperConfig.from_env("admin")
    engine = get_engine(cfg)
    fonte = (fonte or "").strip().lower()
    if not fonte:
        raise ValueError("fonte is required")

    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout = '60s'"))
        before = {
            "emissoes": int(
                conn.execute(
                    text("SELECT COUNT(*) FROM emissoes WHERE fonte = :fonte"),
                    {"fonte": fonte},
                ).scalar_one()
            ),
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
        }
        conn.execute(text("DELETE FROM emissoes WHERE fonte = :fonte"), {"fonte": fonte})

    summary = DeleteFonteSummary(fonte=fonte, **before)
    logger.info(
        "delete_fonte_done",
        extra={
            "fonte": fonte,
            "emissoes_removed": summary.emissoes,
            "series_removed": summary.series,
            "documentos_removed": summary.documentos,
        },
    )
    return summary


def truncate_all_tables(config: ScraperConfig | None = None) -> TruncateSummary:
    """Remove every row from the three core tables and reset identity sequences."""
    cfg = config or ScraperConfig.from_env("admin")
    engine = get_engine(cfg)

    before = {"emissoes": 0, "series": 0, "documentos": 0}
    last_error: Exception | None = None

    for attempt in range(1, 6):
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '30s'"))
                before = {
                    "emissoes": int(
                        conn.execute(text("SELECT COUNT(*) FROM emissoes")).scalar_one()
                    ),
                    "series": int(
                        conn.execute(text("SELECT COUNT(*) FROM series")).scalar_one()
                    ),
                    "documentos": int(
                        conn.execute(text("SELECT COUNT(*) FROM documentos")).scalar_one()
                    ),
                }
                # FK ON DELETE CASCADE clears series + documentos with the parents.
                conn.execute(text("DELETE FROM emissoes"))
                conn.execute(text("DELETE FROM isin_contestados"))
                conn.execute(text("ALTER SEQUENCE IF EXISTS emissoes_emissao_id_seq RESTART WITH 1"))
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
            "emissoes_removed": summary.emissoes,
            "series_removed": summary.series,
            "documentos_removed": summary.documentos,
        },
    )
    return summary
