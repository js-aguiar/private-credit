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

    if isinstance(event, dict) and event.get("action") == "repair_documentos":
        from .repair_docs import run_repair_opea_documentos

        logger.info("repair_opea_documentos_start")
        summary = run_repair_opea_documentos(context=context)
        payload = {
            "action": summary.action,
            "emissoes": summary.emissoes,
            "cedoc_children": summary.cedoc_children,
            "id_cedoc": summary.id_cedoc,
            "documentos_removidos": summary.documentos_removidos,
            "documentos_gravados": summary.documentos_gravados,
            "emissoes_com_documentos": summary.emissoes_com_documentos,
            "emissoes_sem_documentos": summary.emissoes_sem_documentos,
            "sem_documentos": summary.sem_documentos,
            "erros": summary.erros,
        }
        logger.info(
            "repair_opea_documentos_done",
            extra={k: v for k, v in payload.items() if k != "sem_documentos"},
        )
        return {"statusCode": 200, "summary": payload}

    if isinstance(event, dict) and event.get("action") == "emissoes_sem_documentos":
        from .repair_docs import list_opea_emissoes_sem_documentos

        logger.info("emissoes_sem_documentos_start")
        payload = list_opea_emissoes_sem_documentos()
        logger.info(
            "emissoes_sem_documentos_done",
            extra={"total": payload.get("total")},
        )
        return {"statusCode": 200, "summary": payload}

    if isinstance(event, dict) and event.get("action") == "refetch_detail":
        from sqlalchemy import delete, select

        from shared.db import session_scope
        from shared.models import Documento, Emissao

        ids = event.get("id_origem") or event.get("id_origens") or []
        if isinstance(ids, str):
            ids = [ids]
        ids = [str(value).strip() for value in ids if str(value).strip()]
        if not ids:
            return {"statusCode": 400, "error": "id_origem_required"}
        reset_documentos = bool(event.get("reset_documentos"))

        scraper = OpeaScraper.from_env(context=context)
        summary = {
            "source": scraper.source_name,
            "action": "refetch_detail",
            "id_origens": ids,
            "reset_documentos": reset_documentos,
            "documentos_removidos": 0,
            "detalhes_processados": 0,
            "series_gravadas": 0,
            "documentos_gravados": 0,
            "erros": 0,
            "not_found": [],
        }
        try:
            with session_scope(scraper.config) as session:
                for id_origem in ids:
                    emissao = session.scalar(
                        select(Emissao).where(
                            Emissao.fonte == scraper.source_name,
                            Emissao.id_origem == id_origem,
                        )
                    )
                    if emissao is None:
                        summary["not_found"].append(id_origem)
                        continue
                    if reset_documentos:
                        result = session.execute(
                            delete(Documento).where(
                                Documento.emissao_id == emissao.emissao_id
                            )
                        )
                        summary["documentos_removidos"] += int(result.rowcount or 0)
                    scraper._process_single_detail(session, emissao, summary)
        finally:
            scraper.close()
        logger.info("refetch_detail_done", extra=summary)
        return {"statusCode": 200, "summary": summary}

    log_invoke_start(logger, context, "opea")
    scraper = OpeaScraper.from_env(context=context)
    summary = scraper.run()
    return {"statusCode": 200, "summary": summary}
