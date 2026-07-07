"""Idempotent persistence helpers (upserts) and re-check queries.

Uses PostgreSQL ``INSERT ... ON CONFLICT DO UPDATE`` against the core Tables (column-name
keyed) so the ``excluded`` pseudo-row lines up with the physical column names. Notably the
``emissoes.series`` column is exposed as the ``series_raw`` ORM attribute (the ``series``
attribute is the relationship), so we translate that here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Documento, Emissao, Serie
from .records import DocumentoData, EmissaoData, SerieData

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


def _emissao_values(data: EmissaoData) -> dict:
    return {
        "fonte": data.fonte,
        "id_origem": data.id_origem,
        "link": data.link,
        "isin": data.isin,
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
    payload["detalhes_coletados"] = True
    payload["ultima_verificacao_detalhe"] = _now()
    payload["atualizado_em"] = _now()

    if extras:
        payload["extras"] = _EMISSAO.c.extras.op("||")(extras)

    session.execute(
        _EMISSAO.update().where(_EMISSAO.c.emissao_id == emissao_id).values(**payload)
    )


def upsert_serie(session: Session, emissao_id: int, fonte: str, data: SerieData) -> None:
    values = {
        "emissao_id": emissao_id,
        "fonte": fonte,
        "numero_serie": data.numero_serie or "",
        "extras": data.extras or {},
    }
    for col in _SERIE_COLUMNS:
        values[col] = getattr(data, col)

    stmt = insert(_SERIE).values(**values)
    update_set = {col: stmt.excluded[col] for col in _SERIE_COLUMNS}
    update_set["extras"] = _SERIE.c.extras.op("||")(stmt.excluded.extras)
    update_set["atualizado_em"] = _now()
    stmt = stmt.on_conflict_do_update(
        constraint="uq_series_emissao_numero", set_=update_set
    )
    session.execute(stmt)


def upsert_documento(session: Session, emissao_id: int, fonte: str, data: DocumentoData) -> None:
    """Insert a document if new (dedup by link); refresh metadata if it already exists.

    ``data_insercao`` is deliberately never updated so it always reflects when the
    document was first added to the table.
    """
    values = {
        "emissao_id": emissao_id,
        "fonte": fonte,
        "isin": data.isin,
        "numero_emissao": data.numero_emissao,
        "codigo_cetip": data.codigo_cetip,
        "titulo": data.titulo,
        "tipo_documento": data.tipo_documento,
        "link_documento": data.link_documento,
        "data_documento": data.data_documento,
        "extras": data.extras or {},
    }
    stmt = insert(_DOCUMENTO).values(**values)
    update_set = {
        "isin": stmt.excluded.isin,
        "numero_emissao": stmt.excluded.numero_emissao,
        "codigo_cetip": stmt.excluded.codigo_cetip,
        "titulo": stmt.excluded.titulo,
        "tipo_documento": stmt.excluded.tipo_documento,
        "data_documento": stmt.excluded.data_documento,
        "extras": _DOCUMENTO.c.extras.op("||")(stmt.excluded.extras),
        "atualizado_em": _now(),
    }
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
