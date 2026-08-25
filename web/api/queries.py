"""Read-only queries for the document / emissions catalog API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from shared.models import Documento, Emissao, Serie

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _company_expr():
    return func.coalesce(Emissao.devedor, Emissao.operacao)


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
        stmt = stmt.where(_company_expr() == devedor)
    if tipo_documento:
        stmt = stmt.where(Documento.tipo_documento == tipo_documento)
    if date_from:
        stmt = stmt.where(Documento.data_documento >= date_from)
    if date_to:
        stmt = stmt.where(Documento.data_documento <= date_to)
    return stmt


def list_filters(session: Session) -> dict:
    company = _company_expr()
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
            select(company)
            .where(company.is_not(None))
            .where(company != "")
            .distinct()
            .order_by(company)
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

    base = select(Documento, _company_expr()).join(
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


def list_emissoes_filters(session: Session) -> dict:
    company = _company_expr()
    fontes = list(
        session.scalars(select(Emissao.fonte).distinct().order_by(Emissao.fonte)).all()
    )
    companies = [
        value
        for value in session.scalars(
            select(company)
            .where(company.is_not(None))
            .where(company != "")
            .distinct()
            .order_by(company)
        ).all()
    ]
    return {"fontes": fontes, "companies": companies}


def _apply_emissao_filters(
    stmt,
    *,
    fonte: str | None,
    company: str | None,
    cetip: str | None,
    isin: str | None,
):
    if fonte:
        stmt = stmt.where(Emissao.fonte == fonte)
    if company:
        stmt = stmt.where(_company_expr() == company)
    if cetip:
        pattern = f"%{cetip}%"
        stmt = stmt.where(
            or_(
                Emissao.codigos_cetip.ilike(pattern),
                exists(
                    select(Serie.serie_id).where(
                        Serie.emissao_id == Emissao.emissao_id,
                        Serie.codigo_cetip.ilike(pattern),
                    )
                ),
            )
        )
    if isin:
        needle = isin.strip().upper()
        stmt = stmt.where(
            or_(
                func.upper(Emissao.isin) == needle,
                exists(
                    select(Serie.serie_id).where(
                        Serie.emissao_id == Emissao.emissao_id,
                        func.upper(Serie.isin) == needle,
                    )
                ),
            )
        )
    return stmt


def list_emissoes(
    session: Session,
    *,
    fonte: str | None = None,
    company: str | None = None,
    cetip: str | None = None,
    isin: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    limit = min(max(limit, 1), MAX_LIMIT)
    offset = max(offset, 0)

    base = select(Emissao)
    base = _apply_emissao_filters(
        base, fonte=fonte, company=company, cetip=cetip, isin=isin
    )

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.order_by(
            Emissao.data_emissao.desc().nulls_last(),
            Emissao.emissao_id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    items = [
        {
            "id": emissao.emissao_id,
            "company": emissao.devedor or emissao.operacao,
            "fonte": emissao.fonte,
            "numero_emissao": emissao.numero_emissao,
            "isin": emissao.isin,
            "codigos_cetip": emissao.codigos_cetip,
            "data_vencimento": _as_iso(emissao.data_vencimento),
            "data_emissao": _as_iso(emissao.data_emissao),
        }
        for emissao in rows
    ]
    return {
        "items": items,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < int(total),
    }


def get_emissao(session: Session, emissao_id: int) -> dict | None:
    emissao = session.get(Emissao, emissao_id)
    if emissao is None:
        return None

    series_rows = session.scalars(
        select(Serie)
        .where(Serie.emissao_id == emissao_id)
        .order_by(Serie.numero_serie.asc(), Serie.serie_id.asc())
    ).all()
    doc_rows = session.scalars(
        select(Documento)
        .where(Documento.emissao_id == emissao_id)
        .order_by(
            Documento.data_documento.desc().nulls_last(),
            Documento.documento_id.desc(),
        )
    ).all()

    return {
        "id": emissao.emissao_id,
        "company": emissao.devedor or emissao.operacao,
        "operacao": emissao.operacao,
        "devedor": emissao.devedor,
        "fonte": emissao.fonte,
        "numero_emissao": emissao.numero_emissao,
        "link": emissao.link,
        "isin": emissao.isin,
        "codigos_cetip": emissao.codigos_cetip,
        "data_emissao": _as_iso(emissao.data_emissao),
        "data_vencimento": _as_iso(emissao.data_vencimento),
        "series": [
            {
                "id": serie.serie_id,
                "numero_serie": serie.numero_serie,
                "codigo_cetip": serie.codigo_cetip,
                "isin": serie.isin,
                "data_vencimento": _as_iso(serie.data_vencimento),
                "remuneracao": serie.remuneracao,
                "indexador": serie.indexador,
            }
            for serie in series_rows
        ],
        "documentos": [
            {
                "id": documento.documento_id,
                "titulo": documento.titulo,
                "tipo_documento": documento.tipo_documento,
                "data_documento": _as_iso(documento.data_documento),
                "url": documento.link_documento,
            }
            for documento in doc_rows
        ],
    }
