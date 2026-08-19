"""AWS Lambda entrypoint for the Opea scraper."""

from __future__ import annotations

from shared.logging_config import configure_logging, get_logger

from .scraper import OpeaScraper


def handler(event, context):
    configure_logging()
    logger = get_logger("handler.opea")
    if isinstance(event, dict) and event.get("action") == "dedupe_opea_documents":
        from shared.dedupe_opea import run_dedupe

        logger.info("dedupe_opea_start")
        summary = run_dedupe()
        payload = summary.__dict__
        logger.info("dedupe_opea_done", extra=payload)
        return {"statusCode": 200, "summary": payload}

    logger.info("invoke_start")
    scraper = OpeaScraper.from_env(context=context)
    summary = scraper.run()
    return {"statusCode": 200, "summary": summary}
