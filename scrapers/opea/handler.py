"""AWS Lambda entrypoint for the Opea scraper."""

from __future__ import annotations

from shared.lambda_invoke import log_invoke_start
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

    if isinstance(event, dict) and event.get("action") == "truncate_all_tables":
        from shared.truncate_db import truncate_all_tables

        logger.info("truncate_all_start")
        summary = truncate_all_tables()
        payload = summary.__dict__
        logger.info("truncate_all_done", extra=payload)
        return {"statusCode": 200, "summary": payload}

    if isinstance(event, dict) and event.get("action") == "delete_fonte":
        from shared.truncate_db import delete_fonte_rows

        fonte = str(event.get("fonte") or "opea").strip().lower()
        logger.info("delete_fonte_start", extra={"fonte": fonte})
        summary = delete_fonte_rows(fonte)
        payload = summary.__dict__
        logger.info("delete_fonte_done", extra=payload)
        return {"statusCode": 200, "summary": payload}

    if isinstance(event, dict) and event.get("action") == "table_counts":
        from shared.table_counts import get_table_counts

        fonte = event.get("fonte")
        logger.info("table_counts_start", extra={"fonte": fonte})
        summary = get_table_counts(fonte=fonte)
        payload = summary.__dict__
        logger.info("table_counts_done", extra=payload)
        return {"statusCode": 200, "summary": payload}

    log_invoke_start(logger, context, "opea")
    scraper = OpeaScraper.from_env(context=context)
    summary = scraper.run()
    return {"statusCode": 200, "summary": summary}
