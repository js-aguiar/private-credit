#!/usr/bin/env python3
"""Create the database schema (tables + indexes) if it does not already exist.

Reads DB configuration from the same environment variables / Secrets Manager ARN the
scrapers use. Safe to run repeatedly (idempotent).
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared.config import ScraperConfig  # noqa: E402
from shared.db import ensure_schema  # noqa: E402
from shared.logging_config import configure_logging, get_logger  # noqa: E402


def main() -> int:
    configure_logging()
    logger = get_logger("init_db")
    config = ScraperConfig.from_env("init_db")
    ensure_schema(config)
    logger.info("schema_ready")
    print("Schema ensured (tables: emissoes, series, documentos).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
