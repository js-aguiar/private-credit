"""AWS Lambda entrypoint for the Ecoagro scraper."""

from __future__ import annotations

from shared.lambda_invoke import log_invoke_start
from shared.logging_config import configure_logging, get_logger

from .scraper import EcoagroScraper


def handler(event, context):
    configure_logging()
    logger = get_logger("handler.ecoagro")
    log_invoke_start(logger, context, "ecoagro")
    scraper = EcoagroScraper.from_env(context=context)
    summary = scraper.run()
    return {"statusCode": 200, "summary": summary}
