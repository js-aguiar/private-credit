"""Idempotent persistence helpers (upserts) and re-check queries.

Uses PostgreSQL ``INSERT ... ON CONFLICT DO UPDATE`` against the core Tables (column-name
keyed) so the ``excluded`` pseudo-row lines up with the physical column names. Notably the
``emissoes.series`` column is exposed as the ``series_raw`` ORM attribute (the ``series``
attribute is the relationship), so we translate that here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .logging_config import get_logger
from .models import Documento, Emissao, Serie
from .opea_documents import normalize_opea_document_url, opea_file_id
from .records import DocumentoData, EmissaoData, SerieData

logger = get_logger(__name__)

_EMISSAO = Emissao.__table__
_SERIE = Serie.__table__
_DOCUMENTO = Documento.__table__

# Physical column names (in `emissoes`) that discovery may refresh on conflict. Excludes
# detalhes_coletados / ultima_verificacao_detalhe / criado_em so re-discovery never resets
# the detail-collection state.
_EMISSAO_LIST_COLUMNS = (
    "link",
    "isin",
    "numero_emissao",
    "codigos_cetip",
    "operacao",
    "devedor",
    "ano_emissao",
    "tipo_ativo",
    "series",
    "valor_total",
    "indexador",
    "data_emissao",
    "data_vencimento",
    "rating",
)

_SERIE_COLUMNS = (
    "isin",
    "numero_emissao",
    "codigo_cetip",
    "valor",
    "remuneracao",
    "indexador",
    "data_emissao",
    "data_vencimento",
    "quantidade",
    "rating",
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _normalize_isin(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def is_isin_contested(session: Session, isin: str | None) -> bool:
    """Return True if this ISIN was previously marked as duplicated across séries."""
    isin = _normalize_isin(isin)
    if not isin:
        return False
    row = session.execute(
        text("SELECT 1 FROM isin_contestados WHERE isin = :isin LIMIT 1"),
        {"isin": isin},
    ).first()
    return row is not None


def mark_isin_contested(session: Session, isin: str, fonte: str | None = None) -> None:
    """Record a contested ISIN so future upserts keep it NULL."""
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


def _resolve_serie_isin(
    session: Session, emissao_id: int, numero_serie: str, fonte: str, isin: str | None
) -> str | None:
    """Return ISIN to store, nulling both sides when the same ISIN is claimed twice."""
    isin = _normalize_isin(isin)
    if not isin:
        return None
    if is_isin_contested(session, isin):
        return None

    others = session.execute(
        select(Serie.emissao_id, Serie.numero_serie).where(Serie.isin == isin)
    ).all()
    conflict = any(
        row.emissao_id != emissao_id or (row.numero_serie or "") != numero_serie
        for row in others
    )
    if not conflict:
        return isin

    mark_isin_contested(session, isin, fonte=fonte)
    session.execute(
        _SERIE.update()
        .where(_SERIE.c.isin == isin)
        .values(isin=None, atualizado_em=_now())
    )
    logger.info(
        "serie_isin_contested",
        extra={
            "isin": isin,
            "fonte": fonte,
            "emissao_id": emissao_id,
            "numero_serie": numero_serie,
        },
    )
    return None


def _emissao_values(data: EmissaoData) -> dict:
    return {
        "fonte": data.fonte,
        "id_origem": data.id_origem,
        "link": data.link,
        "isin": _normalize_isin(data.isin),
        "numero_emissao": data.numero_emissao,
        "codigos_cetip": data.codigos_cetip,
        "operacao": data.operacao,
        "devedor": data.devedor,
        "ano_emissao": data.ano_emissao,
        "tipo_ativo": data.tipo_ativo,
        "series": data.series_raw,  # ORM attribute series_raw -> column "series"
        "valor_total": data.valor_total,
        "indexador": data.indexador,
        "data_emissao": data.data_emissao,
        "data_vencimento": data.data_vencimento,
        "rating": data.rating,
        "extras": data.extras or {},
    }


def upsert_emissao(session: Session, data: EmissaoData) -> int:
    """Insert or update an emission by (fonte, id_origem); return its emissao_id."""
    values = _emissao_values(data)
    values["isin"] = sanitize_isin(session, values.get("isin"))
    values["data_scraping"] = _now()

    stmt = insert(_EMISSAO).values(**values)
    update_set = {col: stmt.excluded[col] for col in _EMISSAO_LIST_COLUMNS}
    update_set["extras"] = _EMISSAO.c.extras.op("||")(stmt.excluded.extras)
    update_set["data_scraping"] = stmt.excluded.data_scraping
    update_set["atualizado_em"] = _now()

    stmt = stmt.on_conflict_do_update(
        constraint="uq_emissoes_fonte_id_origem", set_=update_set
    ).returning(_EMISSAO.c.emissao_id)
    return session.execute(stmt).scalar_one()


def apply_emissao_detail(session: Session, emissao_id: int, updates: dict) -> None:
    """Apply detail-page fields and mark the emission as detailed/re-checked now."""
    extras = updates.get("extras")
    payload = {k: v for k, v in updates.items() if k != "extras"}
    if "isin" in payload:
        payload["isin"] = sanitize_isin(session, payload.get("isin"))
    payload["detalhes_coletados"] = True
    payload["ultima_verificacao_detalhe"] = _now()
    payload["atualizado_em"] = _now()

    if extras:
        payload["extras"] = _EMISSAO.c.extras.op("||")(extras)

    session.execute(
        _EMISSAO.update().where(_EMISSAO.c.emissao_id == emissao_id).values(**payload)
    )


def upsert_serie(session: Session, emissao_id: int, fonte: str, data: SerieData) -> None:
    numero_serie = data.numero_serie or ""
    isin = _resolve_serie_isin(session, emissao_id, numero_serie, fonte, data.isin)

    values = {
        "emissao_id": emissao_id,
        "fonte": fonte,
        "numero_serie": numero_serie,
        "extras": data.extras or {},
    }
    for col in _SERIE_COLUMNS:
        values[col] = getattr(data, col)
    values["isin"] = isin

    stmt = insert(_SERIE).values(**values)
    update_set = {col: stmt.excluded[col] for col in _SERIE_COLUMNS}
    update_set["extras"] = _SERIE.c.extras.op("||")(stmt.excluded.extras)
    update_set["atualizado_em"] = _now()
    stmt = stmt.on_conflict_do_update(
        constraint="uq_series_emissao_numero", set_=update_set
    )
    session.execute(stmt)


def _resolve_canonical_emissao_id(
    session: Session,
    fonte: str,
    numero_emissao: str | None,
    fallback_emissao_id: int,
) -> int:
    """Legacy helper kept for maintenance scripts.

    Prefer attaching documents to the emission currently being scraped. Grouping by
    ``numero_emissao`` alone is unsafe for Opea (numbers collide across natures).
    """
    return fallback_emissao_id


def _prepare_documento_values(
    session: Session,
    emissao_id: int,
    fonte: str,
    data: DocumentoData,
) -> tuple[dict, str | None]:
    """Normalize Opea links/ids for storage on the given emission."""
    link = data.link_documento
    id_origem_arquivo = data.id_origem_arquivo

    if fonte == "opea":
        link = normalize_opea_document_url(link)
        id_origem_arquivo = id_origem_arquivo or opea_file_id(data.extras)

    values = {
        "emissao_id": emissao_id,
        "fonte": fonte,
        "isin": sanitize_isin(session, data.isin),
        "numero_emissao": data.numero_emissao,
        "codigo_cetip": data.codigo_cetip,
        "titulo": data.titulo,
        "tipo_documento": data.tipo_documento,
        "link_documento": link,
        "id_origem_arquivo": id_origem_arquivo,
        "data_documento": data.data_documento,
        "extras": data.extras or {},
    }
    return values, id_origem_arquivo


def upsert_documento(session: Session, emissao_id: int, fonte: str, data: DocumentoData) -> None:
    """Insert a document if new; refresh metadata if it already exists.

    Opea documents dedupe globally by ``(fonte, id_origem_arquivo)`` using the stable
    cedoc file UUID. Presigned S3 URLs are normalized to the object path before storage.
    Documents attach to the emission row currently being scraped (parent ``codigoOpea``);
    do **not** reassign by ``numero_emissao`` alone — that number collides across
    unrelated deals (e.g. CRA.228 vs CRI.228).

    Other sources keep deduping by ``(emissao_id, link_documento)``.

    ``data_insercao`` is deliberately never updated so it always reflects when the
    document was first added to the table.
    """
    values, id_origem_arquivo = _prepare_documento_values(session, emissao_id, fonte, data)
    stmt = insert(_DOCUMENTO).values(**values)
    update_set = {
        "emissao_id": stmt.excluded.emissao_id,
        "isin": stmt.excluded.isin,
        "numero_emissao": stmt.excluded.numero_emissao,
        "codigo_cetip": stmt.excluded.codigo_cetip,
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
            set_=update_set,
        )
    else:
        stmt = stmt.on_conflict_do_update(
            constraint="uq_documentos_emissao_link", set_=update_set
        )
    session.execute(stmt)


def select_emissoes_para_detalhe(session: Session, fonte: str, limit: int) -> list[Emissao]:
    """Return emissions to visit, prioritizing never-detailed ones, then oldest re-checks.

    Ordering: detalhes_coletados ASC (False first), then ultima_verificacao_detalhe ASC
    with NULLs first. This both backfills new operations and rotates re-checks fairly
    across the whole catalog so new documents on old emissions get picked up.
    """
    stmt = (
        select(Emissao)
        .where(Emissao.fonte == fonte)
        .order_by(
            Emissao.detalhes_coletados.asc(),
            Emissao.ultima_verificacao_detalhe.asc().nullsfirst(),
            Emissao.emissao_id.asc(),
        )
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def count_emissoes(session: Session, fonte: str) -> int:
    return session.execute(
        select(func.count()).select_from(Emissao).where(Emissao.fonte == fonte)
    ).scalar_one()


def count_emissoes_sem_detalhe(session: Session, fonte: str) -> int:
    """Count emissions that have never had detail pages collected."""
    return session.execute(
        select(func.count())
        .select_from(Emissao)
        .where(Emissao.fonte == fonte, Emissao.detalhes_coletados.is_(False))
    ).scalar_one()


def count_table_rows(session: Session, fonte: str | None = None) -> dict[str, int]:
    """Return row counts for the three core tables, optionally filtered by fonte."""
    if fonte:
        return {
            "emissoes": int(
                session.execute(
                    select(func.count()).select_from(Emissao).where(Emissao.fonte == fonte)
                ).scalar_one()
            ),
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
        }
    return {
        "emissoes": int(
            session.execute(select(func.count()).select_from(Emissao)).scalar_one()
        ),
        "series": int(
            session.execute(select(func.count()).select_from(Serie)).scalar_one()
        ),
        "documentos": int(
            session.execute(select(func.count()).select_from(Documento)).scalar_one()
        ),
    }
