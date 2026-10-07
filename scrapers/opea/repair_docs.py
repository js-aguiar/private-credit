"""One-shot Opea document repair: clear stolen attachments and rebind from cedoc once.

Cedoc is institution-wide. Re-fetching it per série during a normal detail backfill
is too slow for conflict repair. This module downloads the file list once, deletes all
``fonte=opea`` document rows, then re-attaches each file to séries sharing the same
parent ``emissao_id`` using the vehicle-aware filter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, func, select

from shared.config import ScraperConfig
from shared.db import session_scope
from shared.logging_config import get_logger
from shared.models import Documento, DocumentoSerie, Serie
from shared.opea_documents import normalize_opea_document_url, opea_file_id
from shared.parsing import parse_br_date
from shared.records import DocumentoData
from shared.repository import link_documento_series, upsert_documento

from .scraper import (
    OpeaScraper,
    document_matches_emission,
    emission_file_code,
    natureza_from_parent,
    parent_codigo_opea,
    vehicle_from_parent,
)

logger = get_logger(__name__)


@dataclass
class RepairOpeaDocsSummary:
    action: str = "repair_documentos"
    emissoes: int = 0  # distinct parent emissao_id groupings
    series: int = 0
    cedoc_children: int = 0
    id_cedoc: str | None = None
    documentos_removidos: int = 0
    documentos_gravados: int = 0
    emissoes_com_documentos: int = 0
    emissoes_sem_documentos: int = 0
    sem_documentos: list[dict] = field(default_factory=list)
    erros: int = 0


def _documento_from_child(child: dict, parent: str, sample: Serie) -> DocumentoData | None:
    url = child.get("url")
    if not url:
        return None
    file_id = opea_file_id(child)
    return DocumentoData(
        link_documento=normalize_opea_document_url(url),
        titulo=child.get("name") or "",
        tipo_documento=child.get("categoryName"),
        data_documento=parse_br_date(str(child.get("createdOn") or "")[:10]),
        numero_emissao=sample.numero_emissao,
        codigo_cetip=sample.codigo_cetip,
        emissao_id=parent,
        id_origem_arquivo=file_id,
        extras=child,
    )


def _pick_id_cedoc(session, scraper: OpeaScraper) -> str | None:
    """Prefer a stored extras.idCedoc; otherwise resolve one live detail."""
    rows = session.scalars(
        select(Serie)
        .where(Serie.fonte == "opea")
        .order_by(Serie.serie_id.asc())
        .limit(50)
    ).all()
    for serie in rows:
        extras = serie.extras or {}
        if isinstance(extras, dict) and extras.get("idCedoc"):
            return str(extras["idCedoc"])
    for serie in rows:
        try:
            detail = scraper.client.get_json(
                scraper._DETAIL_URL,
                params={"codigoOpea": serie.id_origem},
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
            series = list(
                session.scalars(
                    select(Serie)
                    .where(Serie.fonte == "opea")
                    .order_by(Serie.id_origem.asc())
                ).all()
            )
            summary.series = len(series)

            by_parent: dict[str, list[Serie]] = {}
            for serie in series:
                parent = serie.emissao_id or parent_codigo_opea(serie.id_origem)
                by_parent.setdefault(parent, []).append(serie)
            summary.emissoes = len(by_parent)

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

            for parent, members in by_parent.items():
                sample = members[0]
                natureza = None
                if isinstance(sample.extras, dict):
                    natureza = sample.extras.get("natureza")
                natureza = natureza or natureza_from_parent(parent)
                vehicle = vehicle_from_parent(parent)
                ecode = emission_file_code(sample.numero_emissao)
                serie_ids = [s.serie_id for s in members]
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
                    doc = _documento_from_child(child, parent, sample)
                    if doc is None:
                        continue
                    try:
                        doc_id = upsert_documento(session, "opea", doc)
                        if doc_id is not None:
                            link_documento_series(session, doc_id, serie_ids)
                        attached += 1
                        summary.documentos_gravados += 1
                        if file_id:
                            seen_local.add(file_id)
                            claimed_file_ids.add(file_id)
                    except Exception as exc:
                        summary.erros += 1
                        logger.warning(
                            "repair_opea_docs_upsert_error",
                            extra={"emissao_id": parent, "error": str(exc)},
                        )
                if attached:
                    summary.emissoes_com_documentos += 1
                else:
                    summary.emissoes_sem_documentos += 1
                    if len(empty) < sem_docs_limit:
                        empty.append(
                            {
                                "emissao_id": parent,
                                "id_origem": parent,
                                "numero_emissao": sample.numero_emissao,
                                "company": sample.devedor or sample.operacao,
                                "vehicle": vehicle,
                                "series_count": len(members),
                            }
                        )

            summary.sem_documentos = empty
            logger.info(
                "repair_opea_docs_done",
                extra={
                    "emissoes": summary.emissoes,
                    "series": summary.series,
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
    """Return Opea parent groupings that currently have zero linked documents."""
    cfg = config or ScraperConfig.from_env("opea")
    with session_scope(cfg) as session:
        linked = (
            select(Serie.emissao_id)
            .join(DocumentoSerie, DocumentoSerie.serie_id == Serie.serie_id)
            .where(Serie.fonte == "opea", Serie.emissao_id.is_not(None))
            .distinct()
        )
        rows = session.execute(
            select(
                Serie.emissao_id,
                func.min(Serie.numero_emissao).label("numero_emissao"),
                func.min(Serie.devedor).label("devedor"),
                func.min(Serie.operacao).label("operacao"),
                func.count(Serie.serie_id).label("series_count"),
            )
            .where(
                Serie.fonte == "opea",
                Serie.emissao_id.is_not(None),
                Serie.emissao_id.not_in(linked),
            )
            .group_by(Serie.emissao_id)
            .order_by(Serie.emissao_id.asc())
        ).all()
        items = [
            {
                "emissao_id": row.emissao_id,
                "id_origem": row.emissao_id,
                "numero_emissao": row.numero_emissao,
                "company": row.devedor or row.operacao,
                "series_count": row.series_count,
                "vehicle": vehicle_from_parent(row.emissao_id or ""),
            }
            for row in rows
        ]
        return {"fonte": "opea", "total": len(items), "items": items}
