"""AWS Lambda entrypoint for the Riza scraper."""

from __future__ import annotations

from shared.logging_config import configure_logging, get_logger

from .scraper import RizaScraper


def handler(event, context):
    configure_logging()
    logger = get_logger("handler.riza")
    logger.info("invoke_start")
    scraper = RizaScraper.from_env(context=context)
    summary = scraper.run()
    return {"statusCode": 200, "summary": summary}
