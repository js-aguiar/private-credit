"""One-off and maintenance helpers to remove duplicate Opea documents."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from shared.config import ScraperConfig
from shared.db import get_engine, session_scope
from shared.models import Documento, Emissao
from shared.opea_documents import normalize_opea_document_url, opea_file_id
from shared.repository import _resolve_canonical_emissao_id


@dataclass
class DedupeSummary:
    column_added: bool
    ids_backfilled: int
    links_normalized: int
    duplicates_removed: int
    emissions_reassigned: int
    unique_index_created: bool
    opea_documents_remaining: int


def ensure_id_origem_arquivo_column(session: Session) -> bool:
    """Add ``id_origem_arquivo`` when missing (existing deployments). Returns True if added."""
    exists = session.scalar(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'documentos'
                  AND column_name = 'id_origem_arquivo'
            )
            """
        )
    )
    if exists:
        return False
    session.execute(text("ALTER TABLE documentos ADD COLUMN id_origem_arquivo VARCHAR(255)"))
    return True


def ensure_unique_index(session: Session) -> bool:
    """Create the partial unique index when missing. Returns True if created."""
    exists = session.scalar(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE indexname = 'uq_documentos_fonte_id_origem_arquivo'
            )
            """
        )
    )
    if exists:
        return False
    session.execute(
        text(
            """
            CREATE UNIQUE INDEX uq_documentos_fonte_id_origem_arquivo
                ON documentos (fonte, id_origem_arquivo)
                WHERE id_origem_arquivo IS NOT NULL
            """
        )
    )
    return True


def backfill_opea_file_ids(session: Session) -> int:
    rows = session.execute(
        text(
            """
            UPDATE documentos
               SET id_origem_arquivo = extras->>'id'
             WHERE fonte = 'opea'
               AND extras->>'id' IS NOT NULL
               AND extras->>'id' <> ''
               AND (id_origem_arquivo IS NULL OR id_origem_arquivo = '')
            """
        )
    )
    return int(rows.rowcount or 0)


def normalize_opea_links(session: Session) -> int:
    updated = 0
    docs = session.scalars(select(Documento).where(Documento.fonte == "opea")).all()
    for doc in docs:
        normalized = normalize_opea_document_url(doc.link_documento)
        file_id = doc.id_origem_arquivo or opea_file_id(doc.extras)
        changed = False
        if doc.link_documento != normalized:
            doc.link_documento = normalized
            changed = True
        if file_id and doc.id_origem_arquivo != file_id:
            doc.id_origem_arquivo = file_id
            changed = True
        if changed:
            updated += 1
    return updated


def remove_duplicate_opea_documents(session: Session) -> int:
    """Delete duplicate rows, keeping the oldest ``documento_id`` per Opea file id."""
    duplicate_ids = session.scalars(
        text(
            """
            SELECT d.documento_id
              FROM documentos d
              JOIN (
                    SELECT fonte,
                           id_origem_arquivo,
                           MIN(documento_id) AS keep_id
                      FROM documentos
                     WHERE fonte = 'opea'
                       AND id_origem_arquivo IS NOT NULL
                  GROUP BY fonte, id_origem_arquivo
                    HAVING COUNT(*) > 1
                   ) keepers
                ON d.fonte = keepers.fonte
               AND d.id_origem_arquivo = keepers.id_origem_arquivo
             WHERE d.documento_id <> keepers.keep_id
            """
        )
    ).all()
    if not duplicate_ids:
        return 0
    session.execute(
        text("DELETE FROM documentos WHERE documento_id = ANY(:ids)"),
        {"ids": list(duplicate_ids)},
    )
    return len(duplicate_ids)


def reassign_opea_documents_to_canonical_emissions(session: Session) -> int:
    reassigned = 0
    docs = session.scalars(select(Documento).where(Documento.fonte == "opea")).all()
    for doc in docs:
        canonical = _resolve_canonical_emissao_id(
            session, doc.fonte, doc.numero_emissao, doc.emissao_id
        )
        if doc.emissao_id != canonical:
            doc.emissao_id = canonical
            reassigned += 1
    return reassigned


def count_opea_documents(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(Documento).where(Documento.fonte == "opea")
        )
        or 0
    )


def dedupe_opea_documents(session: Session) -> DedupeSummary:
    column_added = ensure_id_origem_arquivo_column(session)
    session.flush()

    ids_backfilled = backfill_opea_file_ids(session)
    session.flush()

    links_normalized = normalize_opea_links(session)
    session.flush()

    duplicates_removed = remove_duplicate_opea_documents(session)
    session.flush()

    emissions_reassigned = reassign_opea_documents_to_canonical_emissions(session)
    session.flush()

    unique_index_created = ensure_unique_index(session)
    remaining = count_opea_documents(session)

    return DedupeSummary(
        column_added=column_added,
        ids_backfilled=ids_backfilled,
        links_normalized=links_normalized,
        duplicates_removed=duplicates_removed,
        emissions_reassigned=emissions_reassigned,
        unique_index_created=unique_index_created,
        opea_documents_remaining=remaining,
    )


def run_dedupe(config: ScraperConfig | None = None) -> DedupeSummary:
    config = config or ScraperConfig.from_env("dedupe_opea")
    with session_scope(config) as session:
        return dedupe_opea_documents(session)


def main() -> int:
    summary = run_dedupe()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
