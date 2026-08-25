"""AWS Lambda entrypoint for the Ecoagro scraper."""

from __future__ import annotations

from sqlalchemy import select

from shared.db import session_scope
from shared.logging_config import configure_logging, get_logger
from shared.models import Emissao

from .scraper import EcoagroScraper


def handler(event, context):
    configure_logging()
    logger = get_logger("handler.ecoagro")

    if isinstance(event, dict) and event.get("action") == "refetch_detail":
        id_origem = str(event.get("id_origem") or "").strip()
        if not id_origem:
            return {"statusCode": 400, "error": "id_origem_required"}
        scraper = EcoagroScraper.from_env(context=context)
        summary = {
            "source": scraper.source_name,
            "action": "refetch_detail",
            "id_origem": id_origem,
            "detalhes_processados": 0,
            "series_gravadas": 0,
            "documentos_gravados": 0,
            "erros": 0,
        }
        try:
            with session_scope(scraper.config) as session:
                emissao = session.scalar(
                    select(Emissao).where(
                        Emissao.fonte == scraper.source_name,
                        Emissao.id_origem == id_origem,
                    )
                )
                if emissao is None:
                    return {"statusCode": 404, "error": "not_found", "id_origem": id_origem}
                scraper._process_single_detail(session, emissao, summary)
        finally:
            scraper.close()
        logger.info("refetch_detail_done", extra=summary)
        return {"statusCode": 200, "summary": summary}

    logger.info("invoke_start")
    scraper = EcoagroScraper.from_env(context=context)
    summary = scraper.run()
    return {"statusCode": 200, "summary": summary}
