"""AWS Lambda entrypoint for the Bari scraper."""

from __future__ import annotations

from shared.logging_config import configure_logging, get_logger

from .scraper import BariScraper


def handler(event, context):
    configure_logging()
    logger = get_logger("handler.bari")
    logger.info("invoke_start")
    scraper = BariScraper.from_env(context=context)
    summary = scraper.run()
    return {"statusCode": 200, "summary": summary}
