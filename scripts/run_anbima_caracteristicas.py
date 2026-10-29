#!/usr/bin/env python3
"""Run the ANBIMA características scraper against a CETIP CSV list."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scrapers.anbima_caracteristicas.scraper import (  # noqa: E402
    AnbimaCaracteristicasScraper,
    load_cetip_csv,
)
from shared.config import ScraperConfig  # noqa: E402
from shared.logging_config import bind_run_context, configure_logging, get_logger  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape ANBIMA Data características into Postgres."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="CSV with columns codigo_cetip,tipo (Debenture|CRA|CRI).",
    )
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="Ensure DB schema (including series_anbima) before scraping.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Optional cap on number of CETIP assets to process.",
    )
    args = parser.parse_args()

    os.environ.setdefault("EXECUTION_MODE", "anbima_caracteristicas")
    configure_logging()
    bind_run_context(source="anbima_caracteristicas")
    logger = get_logger("run_anbima_caracteristicas")

    config = ScraperConfig.from_env("anbima_caracteristicas")
    config.use_browser_fallback = True
    if args.create_schema:
        config.auto_create_schema = True

    assets = load_cetip_csv(args.input)
    logger.info(
        "anbima_caracteristicas_start",
        extra={"input": args.input, "assets": len(assets), "max_items": args.max_items},
    )

    scraper = AnbimaCaracteristicasScraper(config)
    summary = scraper.run(
        assets,
        create_schema=args.create_schema or config.auto_create_schema,
        max_items=args.max_items,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 1 if summary.get("erros") else 0


if __name__ == "__main__":
    raise SystemExit(main())
