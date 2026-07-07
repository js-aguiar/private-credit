#!/usr/bin/env python3
"""Run any scraper locally with no Lambda time limit.

Ideal for the initial full backfill and for forced full re-checks. Uses the exact same
scraper code that runs in Lambda.

Examples:
    python scripts/run_local.py ecoagro --create-schema
    python scripts/run_local.py vert --max-items 20
"""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared.config import ScraperConfig  # noqa: E402
from shared.logging_config import configure_logging, get_logger  # noqa: E402

# Registry of available scrapers: name -> (module path, class name).
SCRAPERS: dict[str, tuple[str, str]] = {
    "ecoagro": ("scrapers.ecoagro.scraper", "EcoagroScraper"),
    "opea": ("scrapers.opea.scraper", "OpeaScraper"),
    "riza": ("scrapers.riza.scraper", "RizaScraper"),
    "vert": ("scrapers.vert.scraper", "VertScraper"),
}


def _load_scraper_class(name: str):
    module_path, class_name = SCRAPERS[name]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a securitization scraper locally.")
    parser.add_argument("source", choices=sorted(SCRAPERS), help="Which scraper to run.")
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Cap the number of detail pages processed this run.",
    )
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="Create the DB schema before running if it does not exist.",
    )
    args = parser.parse_args()

    configure_logging()
    logger = get_logger("run_local")

    klass = _load_scraper_class(args.source)
    config = ScraperConfig.from_env(klass.source_name)
    if args.create_schema:
        config.auto_create_schema = True
    if args.max_items is not None:
        config.detail_batch_limit = args.max_items

    logger.info("run_local_start", extra={"source": args.source})
    scraper = klass(config, context=None)
    summary = scraper.run()
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
