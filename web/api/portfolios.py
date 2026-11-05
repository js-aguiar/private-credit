"""Portfolio (carteiras) read/write helpers for the catalog API."""

from __future__ import annotations

from queries import _as_iso
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from shared.models import Carteira, Serie


def _serie_summary(serie: Serie) -> dict:
    return {
        "id": serie.serie_id,
        "company": serie.devedor or serie.operacao,
        "fonte": serie.fonte,
        "isin": serie.isin,
        "codigo_cetip": serie.codigo_cetip,
        "numero_emissao": serie.numero_emissao,
        "numero_serie": serie.numero_serie,
        "remuneracao": serie.remuneracao,
        "valor": float(serie.valor) if serie.valor is not None else None,
        "link": serie.link,
    }


def normalize_portfolio_name(nome: str | None) -> str:
    cleaned = (nome or "").strip()
    if not cleaned:
        raise ValueError("portfolio name is required")
    if len(cleaned) > 255:
        raise ValueError("portfolio name must be at most 255 characters")
    return cleaned


def list_portfolios(session: Session) -> dict:
    rows = session.execute(
        select(
            Carteira.nome,
            func.count(Carteira.serie_id).label("series_count"),
            func.min(Carteira.criado_em).label("criado_em"),
        )
        .group_by(Carteira.nome)
        .order_by(Carteira.nome.asc())
    ).all()
    return {
        "items": [
            {
                "nome": row.nome,
                "series_count": int(row.series_count),
                "criado_em": _as_iso(row.criado_em),
            }
            for row in rows
        ]
    }


def get_portfolio(session: Session, nome: str) -> dict | None:
    nome = normalize_portfolio_name(nome)
    memberships = session.scalars(
        select(Carteira)
        .where(Carteira.nome == nome)
        .order_by(Carteira.criado_em.asc(), Carteira.carteira_id.asc())
    ).all()
    if not memberships:
        return None
    series: list[dict] = []
    for row in memberships:
        serie = session.get(Serie, row.serie_id)
        if serie is None:
            continue
        item = _serie_summary(serie)
        item["added_at"] = _as_iso(row.criado_em)
        series.append(item)
    return {
        "nome": nome,
        "series_count": len(series),
        "criado_em": _as_iso(memberships[0].criado_em),
        "series": series,
    }


def portfolio_name_exists(session: Session, nome: str) -> bool:
    return (
        session.scalar(
            select(Carteira.carteira_id)
            .where(func.lower(Carteira.nome) == nome.lower())
            .limit(1)
        )
        is not None
    )


def find_series_by_code(
    session: Session,
    *,
    cetip: str | None = None,
    isin: str | None = None,
    q: str | None = None,
) -> list[dict]:
    """Exact-ish lookup against ``series`` for portfolio add validation."""
    stmt = select(Serie)
    cetip_v = (cetip or "").strip() or None
    isin_v = (isin or "").strip() or None
    q_v = (q or "").strip() or None

    if cetip_v:
        stmt = stmt.where(func.upper(Serie.codigo_cetip) == cetip_v.upper())
    elif isin_v:
        stmt = stmt.where(func.upper(Serie.isin) == isin_v.upper())
    elif q_v:
        needle = q_v.upper()
        stmt = stmt.where(
            (func.upper(Serie.codigo_cetip) == needle) | (func.upper(Serie.isin) == needle)
        )
    else:
        raise ValueError("provide cetip, isin, or q")

    series = session.scalars(stmt.order_by(Serie.serie_id.asc()).limit(20)).all()
    return [_serie_summary(serie) for serie in series]


def create_portfolio_with_series(
    session: Session,
    *,
    nome: str,
    serie_ids: list[int],
) -> dict:
    nome = normalize_portfolio_name(nome)
    ids = sorted({int(value) for value in serie_ids})
    if not ids:
        raise ValueError("portfolio must include at least one série")
    if portfolio_name_exists(session, nome):
        raise LookupError("portfolio_name_exists")

    for serie_id in ids:
        serie = session.get(Serie, serie_id)
        if serie is None:
            raise LookupError(f"serie_not_found:{serie_id}")
        session.add(Carteira(nome=nome, serie_id=serie_id))
    session.flush()
    detail = get_portfolio(session, nome)
    assert detail is not None
    return detail


def add_serie_to_portfolio(
    session: Session,
    *,
    nome: str,
    serie_id: int | None = None,
    cetip: str | None = None,
    isin: str | None = None,
) -> dict:
    nome = normalize_portfolio_name(nome)
    if serie_id is None:
        matches = find_series_by_code(session, cetip=cetip, isin=isin)
        if not matches:
            raise LookupError("serie_not_found")
        if len(matches) > 1:
            raise ValueError("multiple séries matched; refine cetip/isin")
        serie_id = int(matches[0]["id"])
    else:
        serie_id = int(serie_id)

    serie = session.get(Serie, serie_id)
    if serie is None:
        raise LookupError("serie_not_found")

    exists = session.scalar(
        select(Carteira.carteira_id)
        .where(Carteira.nome == nome, Carteira.serie_id == serie_id)
        .limit(1)
    )
    if exists is not None:
        detail = get_portfolio(session, nome)
        assert detail is not None
        return {**detail, "already_present": True}

    # Creating a brand-new portfolio name requires this first série (empty not stored).
    session.add(Carteira(nome=nome, serie_id=serie_id))
    session.flush()
    detail = get_portfolio(session, nome)
    assert detail is not None
    return {**detail, "already_present": False}


def remove_serie_from_portfolio(session: Session, *, nome: str, serie_id: int) -> dict:
    nome = normalize_portfolio_name(nome)
    result = session.execute(
        delete(Carteira).where(Carteira.nome == nome, Carteira.serie_id == int(serie_id))
    )
    if result.rowcount == 0:
        if not portfolio_name_exists(session, nome):
            raise LookupError("portfolio_not_found")
        raise LookupError("serie_not_in_portfolio")
    remaining = get_portfolio(session, nome)
    if remaining is None:
        return {"nome": nome, "deleted": True, "series_count": 0, "series": []}
    return {**remaining, "deleted": False}


def rename_portfolio(session: Session, *, nome: str, new_nome: str) -> dict:
    nome = normalize_portfolio_name(nome)
    new_nome = normalize_portfolio_name(new_nome)
    if nome == new_nome:
        detail = get_portfolio(session, nome)
        if detail is None:
            raise LookupError("portfolio_not_found")
        return detail
    if not portfolio_name_exists(session, nome):
        raise LookupError("portfolio_not_found")
    if portfolio_name_exists(session, new_nome):
        raise LookupError("portfolio_name_exists")
    session.execute(update(Carteira).where(Carteira.nome == nome).values(nome=new_nome))
    session.flush()
    detail = get_portfolio(session, new_nome)
    assert detail is not None
    return detail


def delete_portfolio(session: Session, *, nome: str) -> dict:
    nome = normalize_portfolio_name(nome)
    result = session.execute(delete(Carteira).where(Carteira.nome == nome))
    if result.rowcount == 0:
        raise LookupError("portfolio_not_found")
    return {"nome": nome, "deleted": True, "removed": int(result.rowcount)}
