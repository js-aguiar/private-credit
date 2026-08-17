"""Read-only queries for the document catalog API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.models import Documento, Emissao

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _as_iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _apply_filters(
    stmt,
    *,
    fonte: str | None,
    devedor: str | None,
    tipo_documento: str | None,
    date_from: date | None,
    date_to: date | None,
):
    if fonte:
        stmt = stmt.where(Documento.fonte == fonte)
    if devedor:
        stmt = stmt.where(func.coalesce(Emissao.devedor, Emissao.operacao) == devedor)
    if tipo_documento:
        stmt = stmt.where(Documento.tipo_documento == tipo_documento)
    if date_from:
        stmt = stmt.where(Documento.data_documento >= date_from)
    if date_to:
        stmt = stmt.where(Documento.data_documento <= date_to)
    return stmt


def list_filters(session: Session) -> dict:
    fontes = list(
        session.scalars(select(Documento.fonte).distinct().order_by(Documento.fonte)).all()
    )
    tipos = [
        value
        for value in session.scalars(
            select(Documento.tipo_documento)
            .where(Documento.tipo_documento.is_not(None))
            .where(Documento.tipo_documento != "")
            .distinct()
            .order_by(Documento.tipo_documento)
        ).all()
    ]
    companies = [
        value
        for value in session.scalars(
            select(func.coalesce(Emissao.devedor, Emissao.operacao))
            .where(func.coalesce(Emissao.devedor, Emissao.operacao).is_not(None))
            .where(func.coalesce(Emissao.devedor, Emissao.operacao) != "")
            .distinct()
            .order_by(func.coalesce(Emissao.devedor, Emissao.operacao))
        ).all()
    ]
    return {"fontes": fontes, "tipos": tipos, "companies": companies}


def list_documents(
    session: Session,
    *,
    fonte: str | None = None,
    devedor: str | None = None,
    tipo_documento: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    limit = min(max(limit, 1), MAX_LIMIT)
    offset = max(offset, 0)

    base = select(Documento, func.coalesce(Emissao.devedor, Emissao.operacao)).join(
        Emissao, Documento.emissao_id == Emissao.emissao_id
    )
    base = _apply_filters(
        base,
        fonte=fonte,
        devedor=devedor,
        tipo_documento=tipo_documento,
        date_from=date_from,
        date_to=date_to,
    )

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.execute(
        base.order_by(
            Documento.data_documento.desc().nulls_last(),
            Documento.documento_id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    items = [
        {
            "id": documento.documento_id,
            "company": company,
            "date": _as_iso(documento.data_documento),
            "document_type": documento.tipo_documento,
        }
        for documento, company in rows
    ]
    return {
        "items": items,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < int(total),
    }


def get_document(session: Session, documento_id: int) -> dict | None:
    row = session.execute(
        select(Documento, Emissao)
        .join(Emissao, Documento.emissao_id == Emissao.emissao_id)
        .where(Documento.documento_id == documento_id)
    ).first()
    if row is None:
        return None
    documento, emissao = row
    extras = documento.extras or {}
    return {
        "id": documento.documento_id,
        "title": documento.titulo,
        "document_type": documento.tipo_documento,
        "date": _as_iso(documento.data_documento),
        "inserted_at": _as_iso(documento.data_insercao),
        "url": documento.link_documento,
        "fonte": documento.fonte,
        "company": emissao.devedor or emissao.operacao,
        "isin": documento.isin,
        "numero_emissao": documento.numero_emissao,
        "codigo_cetip": documento.codigo_cetip,
        "operacao": emissao.operacao,
        "emission_url": emissao.link,
        "extras": _json_safe(extras) if extras else {},
    }
