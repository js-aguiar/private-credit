"""Return row counts for scraper tables (admin / verification helper)."""

from __future__ import annotations

from dataclasses import dataclass

from shared.config import ScraperConfig
from shared.db import session_scope
from shared.repository import count_table_rows


@dataclass
class TableCounts:
    fonte: str | None
    emissoes: int
    series: int
    documentos: int


def get_table_counts(fonte: str | None = None) -> TableCounts:
    config = ScraperConfig.from_env(fonte or "admin")
    with session_scope(config) as session:
        counts = count_table_rows(session, fonte=fonte)
    return TableCounts(fonte=fonte, **counts)
