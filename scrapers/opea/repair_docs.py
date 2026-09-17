"""One-shot Opea document repair: clear stolen attachments and rebind from cedoc once.

Cedoc is institution-wide (~17k files). Re-fetching it per emission during a normal
detail backfill is too slow for conflict repair. This module downloads the file list
once, deletes all ``fonte=opea`` document rows, then re-attaches each file to at most
one parent emission using the vehicle-aware filter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, func, select

from shared.config import ScraperConfig
from shared.db import session_scope
from shared.logging_config import get_logger
from shared.models import Documento, Emissao
from shared.opea_documents import normalize_opea_document_url, opea_file_id
from shared.parsing import parse_br_date
from shared.records import DocumentoData
from shared.repository import upsert_documento

from .scraper import (
    OpeaScraper,
    document_matches_emission,
    emission_file_code,
    natureza_from_parent,
    vehicle_from_parent,
)

logger = get_logger(__name__)


@dataclass
class RepairOpeaDocsSummary:
    action: str = "repair_documentos"
    emissoes: int = 0
    cedoc_children: int = 0
    id_cedoc: str | None = None
    documentos_removidos: int = 0
    documentos_gravados: int = 0
    emissoes_com_documentos: int = 0
    emissoes_sem_documentos: int = 0
    sem_documentos: list[dict] = field(default_factory=list)
    erros: int = 0


def _documento_from_child(child: dict, emissao: Emissao) -> DocumentoData | None:
    url = child.get("url")
    if not url:
        return None
    file_id = opea_file_id(child)
    return DocumentoData(
        link_documento=normalize_opea_document_url(url),
        titulo=child.get("name") or "",
        tipo_documento=child.get("categoryName"),
        data_documento=parse_br_date(str(child.get("createdOn") or "")[:10]),
        numero_emissao=emissao.numero_emissao,
        codigo_cetip=(emissao.codigos_cetip or "").split()[0]
        if emissao.codigos_cetip
        else None,
        id_origem_arquivo=file_id,
        extras=child,
    )


def _pick_id_cedoc(session, scraper: OpeaScraper) -> str | None:
    """Prefer a stored extras.idCedoc; otherwise resolve one live detail."""
    rows = session.scalars(
        select(Emissao)
        .where(Emissao.fonte == "opea")
        .order_by(Emissao.emissao_id.asc())
        .limit(50)
    ).all()
    for emissao in rows:
        extras = emissao.extras or {}
        if isinstance(extras, dict) and extras.get("idCedoc"):
            return str(extras["idCedoc"])
    for emissao in rows:
        extras = emissao.extras or {}
        codes: list[str] = []
        if isinstance(extras, dict):
            codes = list(extras.get("series_codigos") or [])
        if not codes and emissao.link:
            codes = [emissao.link.rstrip("/").split("/")[-1]]
        for codigo in codes[:1]:
            try:
                detail = scraper.client.get_json(
                    scraper._DETAIL_URL,
                    params={"codigoOpea": codigo},
                )
            except Exception:
                continue
            content = (detail or {}).get("content") or {}
            if content.get("idCedoc"):
                return str(content["idCedoc"])
    return None


def run_repair_opea_documentos(
    config: ScraperConfig | None = None,
    *,
    context=None,
    sem_docs_limit: int = 2000,
) -> RepairOpeaDocsSummary:
    """Delete all Opea documents and reattach from a single cedoc download."""
    scraper = (
        OpeaScraper.from_env(context=context)
        if config is None
        else OpeaScraper(config, context=context)
    )
    summary = RepairOpeaDocsSummary()
    try:
        with session_scope(scraper.config) as session:
            emissoes = list(
                session.scalars(
                    select(Emissao)
                    .where(Emissao.fonte == "opea")
                    .order_by(Emissao.id_origem.asc())
                ).all()
            )
            summary.emissoes = len(emissoes)
            id_cedoc = _pick_id_cedoc(session, scraper)
            if not id_cedoc:
                summary.erros += 1
                logger.error("repair_opea_docs_no_id_cedoc")
                return summary
            summary.id_cedoc = id_cedoc

            resp = scraper.client.get_json(
                scraper._FILES_URL, params={"idCedoc": id_cedoc}
            )
            children: list[dict] = (resp or {}).get("children") or []
            summary.cedoc_children = len(children)
            logger.info(
                "repair_opea_docs_cedoc_loaded",
                extra={"id_cedoc": id_cedoc, "children": len(children)},
            )

            deleted = session.execute(delete(Documento).where(Documento.fonte == "opea"))
            summary.documentos_removidos = int(deleted.rowcount or 0)
            session.flush()

            claimed_file_ids: set[str] = set()
            empty: list[dict] = []

            for emissao in emissoes:
                natureza = None
                if isinstance(emissao.extras, dict):
                    natureza = emissao.extras.get("natureza")
                natureza = natureza or natureza_from_parent(emissao.id_origem)
                vehicle = vehicle_from_parent(emissao.id_origem)
                ecode = emission_file_code(emissao.numero_emissao)
                attached = 0
                seen_local: set[str] = set()
                for child in children:
                    name = child.get("name") or ""
                    if not document_matches_emission(name, natureza, ecode, vehicle):
                        continue
                    file_id = opea_file_id(child)
                    if file_id and (
                        file_id in claimed_file_ids or file_id in seen_local
                    ):
                        continue
                    doc = _documento_from_child(child, emissao)
                    if doc is None:
                        continue
                    try:
                        upsert_documento(session, emissao.emissao_id, "opea", doc)
                        attached += 1
                        summary.documentos_gravados += 1
                        if file_id:
                            seen_local.add(file_id)
                            claimed_file_ids.add(file_id)
                    except Exception as exc:
                        summary.erros += 1
                        logger.warning(
                            "repair_opea_docs_upsert_error",
                            extra={
                                "id_origem": emissao.id_origem,
                                "error": str(exc),
                            },
                        )
                if attached:
                    summary.emissoes_com_documentos += 1
                else:
                    summary.emissoes_sem_documentos += 1
                    if len(empty) < sem_docs_limit:
                        empty.append(
                            {
                                "emissao_id": emissao.emissao_id,
                                "id_origem": emissao.id_origem,
                                "numero_emissao": emissao.numero_emissao,
                                "company": emissao.devedor or emissao.operacao,
                                "vehicle": vehicle,
                            }
                        )

            summary.sem_documentos = empty
            logger.info(
                "repair_opea_docs_done",
                extra={
                    "emissoes": summary.emissoes,
                    "documentos_removidos": summary.documentos_removidos,
                    "documentos_gravados": summary.documentos_gravados,
                    "emissoes_sem_documentos": summary.emissoes_sem_documentos,
                    "erros": summary.erros,
                },
            )
    finally:
        scraper.close()
    return summary


def list_opea_emissoes_sem_documentos(config: ScraperConfig | None = None) -> dict:
    """Return Opea emissions that currently have zero document rows."""
    cfg = config or ScraperConfig.from_env("opea")
    with session_scope(cfg) as session:
        rows = session.execute(
            select(
                Emissao.emissao_id,
                Emissao.id_origem,
                Emissao.numero_emissao,
                Emissao.devedor,
                Emissao.operacao,
                Emissao.codigos_cetip,
            )
            .outerjoin(Documento, Documento.emissao_id == Emissao.emissao_id)
            .where(Emissao.fonte == "opea")
            .group_by(
                Emissao.emissao_id,
                Emissao.id_origem,
                Emissao.numero_emissao,
                Emissao.devedor,
                Emissao.operacao,
                Emissao.codigos_cetip,
            )
            .having(func.count(Documento.documento_id) == 0)
            .order_by(Emissao.id_origem.asc())
        ).all()
        items = [
            {
                "emissao_id": row.emissao_id,
                "id_origem": row.id_origem,
                "numero_emissao": row.numero_emissao,
                "company": row.devedor or row.operacao,
                "codigos_cetip": row.codigos_cetip,
                "vehicle": vehicle_from_parent(row.id_origem or ""),
            }
            for row in rows
        ]
        return {"fonte": "opea", "total": len(items), "items": items}
