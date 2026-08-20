"""AWS Lambda entrypoint for the VERT scraper."""

from __future__ import annotations

from shared.logging_config import configure_logging, get_logger

from .scraper import VertScraper


def handler(event, context):
    configure_logging()
    logger = get_logger("handler.vert")
    logger.info("invoke_start")
    scraper = VertScraper.from_env(context=context)
    summary = scraper.run()
    return {"statusCode": 200, "summary": summary}
