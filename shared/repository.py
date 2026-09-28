"""Idempotent persistence helpers (upserts) and re-check queries — series-first.

``series`` is the primary entity. Upserts skip when both ``isin`` and ``codigo_cetip``
are missing, or when either key is already owned by a different série (warn + skip).
Documents attach to one or more séries via ``documentos_series``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .logging_config import get_logger
from .models import Documento, DocumentoSerie, Serie
from .opea_documents import normalize_opea_document_url, opea_file_id
from .records import DocumentoData, SerieData

logger = get_logger(__name__)

_SERIE = Serie.__table__
_DOCUMENTO = Documento.__table__
_DOCUMENTO_SERIE = DocumentoSerie.__table__

UpsertStatus = Literal[
    "inserted",
    "updated",
    "skipped_no_keys",
    "skipped_isin_conflict",
    "skipped_cetip_conflict",
]

# Columns refreshed on discovery/detail conflict. Excludes scrape-state flags and PK.
_SERIE_UPSERT_COLUMNS = (
    "link",
    "isin",
    "codigo_cetip",
    "emissao_id",
    "numero_emissao",
    "numero_serie",
    "operacao",
    "devedor",
    "ano_emissao",
    "tipo_ativo",
    "series_raw",
    "valor_total",
    "valor",
    "remuneracao",
    "indexador",
    "data_emissao",
    "data_vencimento",
    "quantidade",
    "rating",
)


@dataclass(frozen=True)
class UpsertSerieResult:
    serie_id: int | None
    status: UpsertStatus


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# Site placeholders that must not occupy UNIQUE(isin) / UNIQUE(codigo_cetip).
_PLACEHOLDER_KEYS = frozenset(
    {
        "-",
        ".",
        "..",
        "n/a",
        "n.a",
        "n.a.",
        "na",
        "none",
        "null",
        "nil",
        "undefined",
        "sem isin",
        "sem cetip",
        "nao informado",
        "não informado",
        "s/n",
        "sn",
        "mudar",  # Vert placeholder seen in list API
        "tbd",
        "todo",
    }
)


def _is_placeholder_key(value: str) -> bool:
    """True for empty-ish, literal placeholders, or all-zero business keys."""
    lowered = value.casefold().strip()
    if not lowered or lowered in _PLACEHOLDER_KEYS:
        return True
    # Digits-only zeros (e.g. 0000000000) or ticker+zeros (CRA0000000, BR0000000000).
    alnum = "".join(ch for ch in lowered if ch.isalnum())
    if not alnum:
        return True
    digits = "".join(ch for ch in alnum if ch.isdigit())
    letters = "".join(ch for ch in alnum if ch.isalpha())
    if digits and set(digits) == {"0"} and (not letters or letters in {"br", "cra", "cri", "cdca", "deb"}):
        return True
    return False


def _normalize_key(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned or _is_placeholder_key(cleaned):
        return None
    return cleaned


def _normalize_isin(value: str | None) -> str | None:
    return _normalize_key(value)


def is_isin_contested(session: Session, isin: str | None) -> bool:
    """Return True if this ISIN was previously marked as contested."""
    isin = _normalize_isin(isin)
    if not isin:
        return False
    row = session.execute(
        text("SELECT 1 FROM isin_contestados WHERE isin = :isin LIMIT 1"),
        {"isin": isin},
    ).first()
    return row is not None


def mark_isin_contested(session: Session, isin: str, fonte: str | None = None) -> None:
    """Record a contested ISIN for audit/diagnostics."""
    isin = _normalize_isin(isin)
    if not isin:
        return
    session.execute(
        text(
            """
            INSERT INTO isin_contestados (isin, fonte, detectado_em)
            VALUES (:isin, :fonte, :detectado_em)
            ON CONFLICT (isin) DO NOTHING
            """
        ),
        {"isin": isin, "fonte": fonte, "detectado_em": _now()},
    )


def sanitize_isin(session: Session, isin: str | None) -> str | None:
    """Blank → NULL; contested → NULL."""
    isin = _normalize_isin(isin)
    if isin and is_isin_contested(session, isin):
        return None
    return isin


def _find_serie_by_origem(session: Session, fonte: str, id_origem: str) -> Serie | None:
    return session.execute(
        select(Serie).where(Serie.fonte == fonte, Serie.id_origem == id_origem)
    ).scalar_one_or_none()


def _find_serie_by_isin(session: Session, isin: str) -> Serie | None:
    return session.execute(select(Serie).where(Serie.isin == isin)).scalar_one_or_none()


def _find_serie_by_cetip(session: Session, codigo_cetip: str) -> Serie | None:
    return session.execute(
        select(Serie).where(Serie.codigo_cetip == codigo_cetip)
    ).scalar_one_or_none()


def _serie_values(data: SerieData) -> dict:
    return {
        "fonte": data.fonte,
        "id_origem": data.id_origem,
        "link": data.link,
        "isin": _normalize_isin(data.isin),
        "codigo_cetip": _normalize_key(data.codigo_cetip),
        "emissao_id": _normalize_key(data.emissao_id),
        "numero_emissao": data.numero_emissao,
        "numero_serie": data.numero_serie or "",
        "operacao": data.operacao,
        "devedor": data.devedor,
        "ano_emissao": data.ano_emissao,
        "tipo_ativo": data.tipo_ativo,
        "series_raw": data.series_raw,
        "valor_total": data.valor_total,
        "valor": data.valor,
        "remuneracao": data.remuneracao,
        "indexador": data.indexador,
        "data_emissao": data.data_emissao,
        "data_vencimento": data.data_vencimento,
        "quantidade": data.quantidade,
        "rating": data.rating,
        "extras": data.extras or {},
    }


def upsert_serie(session: Session, data: SerieData) -> UpsertSerieResult:
    """Insert or update a série; skip on missing keys or uniqueness conflicts.

    Identity preference: ``(fonte, id_origem)``. Business keys ``isin`` /
    ``codigo_cetip`` must not be stolen from another série.
    """
    isin = _normalize_isin(data.isin)
    cetip = _normalize_key(data.codigo_cetip)

    if not isin and not cetip:
        logger.warning(
            "serie_skipped_no_keys",
            extra={
                "fonte": data.fonte,
                "id_origem": data.id_origem,
                "numero_serie": data.numero_serie,
            },
        )
        return UpsertSerieResult(serie_id=None, status="skipped_no_keys")

    existing = _find_serie_by_origem(session, data.fonte, data.id_origem)
    existing_id = existing.serie_id if existing else None

    if isin:
        owner = _find_serie_by_isin(session, isin)
        if owner is not None and owner.serie_id != existing_id:
            mark_isin_contested(session, isin, fonte=data.fonte)
            logger.warning(
                "serie_skipped_isin_conflict",
                extra={
                    "fonte": data.fonte,
                    "id_origem": data.id_origem,
                    "isin": isin,
                    "owner_serie_id": owner.serie_id,
                    "owner_id_origem": owner.id_origem,
                },
            )
            return UpsertSerieResult(serie_id=None, status="skipped_isin_conflict")

    if cetip:
        owner = _find_serie_by_cetip(session, cetip)
        if owner is not None and owner.serie_id != existing_id:
            logger.warning(
                "serie_skipped_cetip_conflict",
                extra={
                    "fonte": data.fonte,
                    "id_origem": data.id_origem,
                    "codigo_cetip": cetip,
                    "owner_serie_id": owner.serie_id,
                    "owner_id_origem": owner.id_origem,
                },
            )
            return UpsertSerieResult(serie_id=None, status="skipped_cetip_conflict")

    values = _serie_values(data)
    values["isin"] = sanitize_isin(session, values.get("isin"))
    # Contested ISIN with no CETIP left → skip (cannot store without a key).
    if not values["isin"] and not values["codigo_cetip"]:
        logger.warning(
            "serie_skipped_no_keys",
            extra={
                "fonte": data.fonte,
                "id_origem": data.id_origem,
                "reason": "contested_isin_no_cetip",
            },
        )
        return UpsertSerieResult(serie_id=None, status="skipped_no_keys")

    values["data_scraping"] = _now()

    stmt = insert(_SERIE).values(**values)
    update_set = {col: stmt.excluded[col] for col in _SERIE_UPSERT_COLUMNS}
    update_set["extras"] = _SERIE.c.extras.op("||")(stmt.excluded.extras)
    update_set["data_scraping"] = stmt.excluded.data_scraping
    update_set["atualizado_em"] = _now()

    stmt = stmt.on_conflict_do_update(
        constraint="uq_series_fonte_id_origem", set_=update_set
    ).returning(_SERIE.c.serie_id)
    serie_id = session.execute(stmt).scalar_one()
    status: UpsertStatus = "updated" if existing_id is not None else "inserted"
    return UpsertSerieResult(serie_id=serie_id, status=status)


def apply_serie_detail(session: Session, serie_id: int, updates: dict) -> None:
    """Apply detail-page fields and mark the série as detailed/re-checked now."""
    extras = updates.get("extras")
    payload = {k: v for k, v in updates.items() if k != "extras"}
    if "isin" in payload:
        payload["isin"] = sanitize_isin(session, payload.get("isin"))
    if "codigo_cetip" in payload:
        payload["codigo_cetip"] = _normalize_key(payload.get("codigo_cetip"))
    payload["detalhes_coletados"] = True
    payload["ultima_verificacao"] = _now()
    payload["atualizado_em"] = _now()

    if extras:
        payload["extras"] = _SERIE.c.extras.op("||")(extras)

    session.execute(
        _SERIE.update().where(_SERIE.c.serie_id == serie_id).values(**payload)
    )


def mark_serie_detailed(session: Session, serie_id: int) -> None:
    """Mark a série as detailed without applying field updates."""
    session.execute(
        _SERIE.update()
        .where(_SERIE.c.serie_id == serie_id)
        .values(
            detalhes_coletados=True,
            ultima_verificacao=_now(),
            atualizado_em=_now(),
        )
    )


def _prepare_documento_values(
    session: Session,
    fonte: str,
    data: DocumentoData,
) -> tuple[dict, str | None]:
    """Normalize Opea links/ids for storage."""
    link = data.link_documento
    id_origem_arquivo = data.id_origem_arquivo

    if fonte == "opea":
        link = normalize_opea_document_url(link)
        id_origem_arquivo = id_origem_arquivo or opea_file_id(data.extras)

    values = {
        "fonte": fonte,
        "emissao_id": _normalize_key(data.emissao_id),
        "isin": sanitize_isin(session, data.isin),
        "numero_emissao": data.numero_emissao,
        "codigo_cetip": _normalize_key(data.codigo_cetip),
        "titulo": data.titulo,
        "tipo_documento": data.tipo_documento,
        "link_documento": link,
        "id_origem_arquivo": id_origem_arquivo,
        "data_documento": data.data_documento,
        "extras": data.extras or {},
    }
    return values, id_origem_arquivo


def upsert_documento(session: Session, fonte: str, data: DocumentoData) -> int | None:
    """Insert/update a document by ``(fonte, id_origem_arquivo)`` or ``(fonte, link)``.

    Returns ``documento_id``, or ``None`` when ``link_documento`` is empty.
    Does not attach séries — call ``link_documento_series`` separately.
    """
    if not data.link_documento:
        return None

    values, id_origem_arquivo = _prepare_documento_values(session, fonte, data)
    stmt = insert(_DOCUMENTO).values(**values)
    metadata_update = {
        "isin": stmt.excluded.isin,
        "numero_emissao": stmt.excluded.numero_emissao,
        "codigo_cetip": stmt.excluded.codigo_cetip,
        "emissao_id": func.coalesce(_DOCUMENTO.c.emissao_id, stmt.excluded.emissao_id),
        "titulo": stmt.excluded.titulo,
        "tipo_documento": stmt.excluded.tipo_documento,
        "link_documento": stmt.excluded.link_documento,
        "data_documento": stmt.excluded.data_documento,
        "extras": _DOCUMENTO.c.extras.op("||")(stmt.excluded.extras),
        "atualizado_em": _now(),
    }

    if id_origem_arquivo:
        stmt = stmt.on_conflict_do_update(
            index_elements=["fonte", "id_origem_arquivo"],
            index_where=text("id_origem_arquivo IS NOT NULL"),
            set_=metadata_update,
        )
    else:
        stmt = stmt.on_conflict_do_update(
            constraint="uq_documentos_fonte_link",
            set_=metadata_update,
        )
    return session.execute(stmt.returning(_DOCUMENTO.c.documento_id)).scalar_one()


def link_documento_series(
    session: Session,
    documento_id: int,
    serie_ids: list[int],
) -> None:
    """Attach a document to one or more séries (idempotent M2M)."""
    for serie_id in dict.fromkeys(serie_ids):
        if not serie_id:
            continue
        session.execute(
            insert(_DOCUMENTO_SERIE)
            .values(documento_id=documento_id, serie_id=serie_id)
            .on_conflict_do_nothing(constraint="documentos_series_pkey")
        )


def resolve_serie_ids_by_origem(
    session: Session, fonte: str, id_origens: list[str]
) -> list[int]:
    """Map ``(fonte, id_origem)`` values to ``serie_id``s (missing ones omitted)."""
    if not id_origens:
        return []
    rows = session.execute(
        select(Serie.serie_id, Serie.id_origem).where(
            Serie.fonte == fonte, Serie.id_origem.in_(list(dict.fromkeys(id_origens)))
        )
    ).all()
    by_origem = {row.id_origem: row.serie_id for row in rows}
    return [by_origem[o] for o in dict.fromkeys(id_origens) if o in by_origem]


def resolve_serie_ids_by_emissao_id(
    session: Session, fonte: str, emissao_id: str | None
) -> list[int]:
    """Return all ``serie_id``s sharing a source emission grouping string."""
    emissao_id = _normalize_key(emissao_id)
    if not emissao_id:
        return []
    return list(
        session.execute(
            select(Serie.serie_id)
            .where(Serie.fonte == fonte, Serie.emissao_id == emissao_id)
            .order_by(Serie.serie_id.asc())
        ).scalars().all()
    )


def select_series_para_detalhe(session: Session, fonte: str, limit: int) -> list[Serie]:
    """Return séries to visit, prioritizing never-detailed ones, then oldest re-checks."""
    stmt = (
        select(Serie)
        .where(Serie.fonte == fonte)
        .order_by(
            Serie.detalhes_coletados.asc(),
            Serie.ultima_verificacao.asc().nullsfirst(),
            Serie.serie_id.asc(),
        )
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def count_series(session: Session, fonte: str) -> int:
    return session.execute(
        select(func.count()).select_from(Serie).where(Serie.fonte == fonte)
    ).scalar_one()


def count_series_sem_detalhe(session: Session, fonte: str) -> int:
    """Count séries that have never had detail pages collected."""
    return session.execute(
        select(func.count())
        .select_from(Serie)
        .where(Serie.fonte == fonte, Serie.detalhes_coletados.is_(False))
    ).scalar_one()


# Back-compat aliases used by older scripts during the transition.
count_emissoes = count_series
count_emissoes_sem_detalhe = count_series_sem_detalhe


def count_table_rows(session: Session, fonte: str | None = None) -> dict[str, int]:
    """Return row counts for core tables, optionally filtered by fonte."""
    if fonte:
        return {
            "series": int(
                session.execute(
                    select(func.count()).select_from(Serie).where(Serie.fonte == fonte)
                ).scalar_one()
            ),
            "documentos": int(
                session.execute(
                    select(func.count())
                    .select_from(Documento)
                    .where(Documento.fonte == fonte)
                ).scalar_one()
            ),
            "documentos_series": int(
                session.execute(
                    select(func.count())
                    .select_from(DocumentoSerie)
                    .join(Serie, DocumentoSerie.serie_id == Serie.serie_id)
                    .where(Serie.fonte == fonte)
                ).scalar_one()
            ),
        }
    return {
        "series": int(
            session.execute(select(func.count()).select_from(Serie)).scalar_one()
        ),
        "documentos": int(
            session.execute(select(func.count()).select_from(Documento)).scalar_one()
        ),
        "documentos_series": int(
            session.execute(select(func.count()).select_from(DocumentoSerie)).scalar_one()
        ),
    }
