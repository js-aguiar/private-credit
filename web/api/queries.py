"""Read-only queries for the document / series catalog API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.models import Documento, DocumentoSerie, Serie

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _company_expr():
    return func.coalesce(Serie.devedor, Serie.operacao)


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


def _apply_document_filters(
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
            .select_from(Serie)
            .join(DocumentoSerie, DocumentoSerie.serie_id == Serie.serie_id)
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

    company = _company_expr()
    # One row per document; pick an arbitrary linked série for company display.
    base = (
        select(Documento, company.label("company"))
        .outerjoin(DocumentoSerie, DocumentoSerie.documento_id == Documento.documento_id)
        .outerjoin(Serie, Serie.serie_id == DocumentoSerie.serie_id)
        .distinct(Documento.documento_id)
    )
    base = _apply_document_filters(
        base,
        fonte=fonte,
        devedor=devedor,
        tipo_documento=tipo_documento,
        date_from=date_from,
        date_to=date_to,
    )

    total = session.scalar(
        select(func.count()).select_from(
            select(Documento.documento_id)
            .outerjoin(
                DocumentoSerie, DocumentoSerie.documento_id == Documento.documento_id
            )
            .outerjoin(Serie, Serie.serie_id == DocumentoSerie.serie_id)
            .where(
                *([Documento.fonte == fonte] if fonte else []),
                *([company == devedor] if devedor else []),
                *([Documento.tipo_documento == tipo_documento] if tipo_documento else []),
                *([Documento.data_documento >= date_from] if date_from else []),
                *([Documento.data_documento <= date_to] if date_to else []),
            )
            .distinct()
            .subquery()
        )
    ) or 0

    rows = session.execute(
        base.order_by(
            Documento.documento_id,
            Documento.data_documento.desc().nulls_last(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    # Re-sort in Python for date order (DISTINCT ON requires leading order key).
    items = sorted(
        [
            {
                "id": documento.documento_id,
                "company": company_value,
                "date": _as_iso(documento.data_documento),
                "document_type": documento.tipo_documento,
                "_sort_date": documento.data_documento,
            }
            for documento, company_value in rows
        ],
        key=lambda item: (
            item["_sort_date"] is not None,
            item["_sort_date"] or date.min,
            item["id"],
        ),
        reverse=True,
    )
    for item in items:
        item.pop("_sort_date", None)

    return {
        "items": items,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < int(total),
    }


def _public_document_url(documento: Documento) -> str:
    """Return a browser-openable URL."""
    if documento.fonte == "opea":
        return f"/api/documents/{documento.documento_id}/open"
    return documento.link_documento


def _id_cedoc_for_document(session: Session, documento: Documento) -> str | None:
    extras = documento.extras or {}
    id_cedoc = extras.get("idCedoc") or extras.get("id_cedoc")
    if id_cedoc:
        return str(id_cedoc)
    # Fall back to any linked série's extras.
    series = session.scalars(
        select(Serie)
        .join(DocumentoSerie, DocumentoSerie.serie_id == Serie.serie_id)
        .where(DocumentoSerie.documento_id == documento.documento_id)
        .limit(5)
    ).all()
    for serie in series:
        serie_extras = serie.extras or {}
        if isinstance(serie_extras, dict):
            value = serie_extras.get("idCedoc") or serie_extras.get("id_cedoc")
            if value:
                return str(value)
    return None


def resolve_document_open_url(session: Session, documento_id: int) -> str | None:
    """Resolve a working URL for Open document (refreshing Opea presigned links)."""
    documento = session.get(Documento, documento_id)
    if documento is None:
        return None
    if documento.fonte != "opea":
        return documento.link_documento

    id_cedoc = _id_cedoc_for_document(session, documento)
    from shared.opea_documents import refresh_opea_presigned_url

    return refresh_opea_presigned_url(
        id_cedoc=str(id_cedoc or ""),
        file_id=documento.id_origem_arquivo,
        stored_url=documento.link_documento,
    )


def get_document(session: Session, documento_id: int) -> dict | None:
    documento = session.get(Documento, documento_id)
    if documento is None:
        return None

    series = session.scalars(
        select(Serie)
        .join(DocumentoSerie, DocumentoSerie.serie_id == Serie.serie_id)
        .where(DocumentoSerie.documento_id == documento_id)
        .order_by(Serie.numero_serie.asc(), Serie.serie_id.asc())
    ).all()
    primary = series[0] if series else None
    company = None
    if primary:
        company = primary.devedor or primary.operacao
    extras = documento.extras or {}
    return {
        "id": documento.documento_id,
        "title": documento.titulo,
        "document_type": documento.tipo_documento,
        "date": _as_iso(documento.data_documento),
        "inserted_at": _as_iso(documento.data_insercao),
        "url": _public_document_url(documento),
        "fonte": documento.fonte,
        "company": company,
        "isin": documento.isin or (primary.isin if primary else None),
        "numero_emissao": documento.numero_emissao
        or (primary.numero_emissao if primary else None),
        "codigo_cetip": documento.codigo_cetip
        or (primary.codigo_cetip if primary else None),
        "operacao": primary.operacao if primary else None,
        "emission_url": None
        if (primary and primary.fonte == "opea")
        else (primary.link if primary else None),
        "series": [
            {
                "id": serie.serie_id,
                "numero_serie": serie.numero_serie,
                "isin": serie.isin,
                "codigo_cetip": serie.codigo_cetip,
                "company": serie.devedor or serie.operacao,
            }
            for serie in series
        ],
        "extras": _json_safe(extras) if extras else {},
    }


def list_series_filters(session: Session) -> dict:
    company = _company_expr()
    fontes = list(
        session.scalars(select(Serie.fonte).distinct().order_by(Serie.fonte)).all()
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


def _apply_serie_filters(
    stmt,
    *,
    fonte: str | None,
    company: str | None,
    cetip: str | None,
    isin: str | None,
):
    if fonte:
        stmt = stmt.where(Serie.fonte == fonte)
    if company:
        stmt = stmt.where(_company_expr() == company)
    if cetip:
        pattern = f"%{cetip}%"
        stmt = stmt.where(Serie.codigo_cetip.ilike(pattern))
    if isin:
        needle = isin.strip().upper()
        stmt = stmt.where(func.upper(Serie.isin) == needle)
    return stmt


def list_series(
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

    base = select(Serie)
    base = _apply_serie_filters(
        base, fonte=fonte, company=company, cetip=cetip, isin=isin
    )

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.order_by(
            Serie.data_emissao.desc().nulls_last(),
            Serie.serie_id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    items = [
        {
            "id": serie.serie_id,
            "company": serie.devedor or serie.operacao,
            "fonte": serie.fonte,
            "numero_emissao": serie.numero_emissao,
            "numero_serie": serie.numero_serie,
            "isin": serie.isin,
            "codigo_cetip": serie.codigo_cetip,
            "data_vencimento": _as_iso(serie.data_vencimento),
            "data_emissao": _as_iso(serie.data_emissao),
        }
        for serie in rows
    ]
    return {
        "items": items,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < int(total),
    }


def get_serie(session: Session, serie_id: int) -> dict | None:
    serie = session.get(Serie, serie_id)
    if serie is None:
        return None

    doc_rows = session.scalars(
        select(Documento)
        .join(DocumentoSerie, DocumentoSerie.documento_id == Documento.documento_id)
        .where(DocumentoSerie.serie_id == serie_id)
        .order_by(
            Documento.data_documento.desc().nulls_last(),
            Documento.documento_id.desc(),
        )
    ).all()

    # Sibling séries in the same source emission grouping (optional context).
    siblings: list[Serie] = []
    if serie.emissao_id:
        siblings = list(
            session.scalars(
                select(Serie)
                .where(
                    Serie.fonte == serie.fonte,
                    Serie.emissao_id == serie.emissao_id,
                    Serie.serie_id != serie.serie_id,
                )
                .order_by(Serie.numero_serie.asc(), Serie.serie_id.asc())
            ).all()
        )

    return {
        "id": serie.serie_id,
        "company": serie.devedor or serie.operacao,
        "operacao": serie.operacao,
        "devedor": serie.devedor,
        "fonte": serie.fonte,
        "numero_emissao": serie.numero_emissao,
        "numero_serie": serie.numero_serie,
        "link": None if serie.fonte == "opea" else serie.link,
        "isin": serie.isin,
        "codigo_cetip": serie.codigo_cetip,
        "emissao_id": serie.emissao_id,
        "data_emissao": _as_iso(serie.data_emissao),
        "data_vencimento": _as_iso(serie.data_vencimento),
        "remuneracao": serie.remuneracao,
        "indexador": serie.indexador,
        "quantidade": serie.quantidade,
        "valor": float(serie.valor) if serie.valor is not None else None,
        "extras": _json_safe(serie.extras) if serie.extras else {},
        "siblings": [
            {
                "id": sibling.serie_id,
                "numero_serie": sibling.numero_serie,
                "codigo_cetip": sibling.codigo_cetip,
                "isin": sibling.isin,
            }
            for sibling in siblings
        ],
        "documentos": [
            {
                "id": documento.documento_id,
                "titulo": documento.titulo,
                "tipo_documento": documento.tipo_documento,
                "data_documento": _as_iso(documento.data_documento),
                "url": _public_document_url(documento),
            }
            for documento in doc_rows
        ],
    }


# Back-compat aliases for older callers during transition.
list_emissoes_filters = list_series_filters
list_emissoes = list_series
get_emissao = get_serie
