"""Persistence helpers for the ANBIMA ``series_anbima`` table."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import SerieAnbima

_TABLE = SerieAnbima.__table__

_COLUMNS = (
    "codigo_cetip",
    "tipo",
    "serie",
    "remuneracao",
    "data_inicio_rentabilidade",
    "quantidade_serie_emissao",
    "volume_serie_emissao",
    "vne",
    "quantidade_mercado_b3",
    "data_emissao",
    "data_vencimento",
    "isin",
    "lei_12431",
    "emissao",
    "empresa",
    "setor",
    "cnpj",
    "garantia",
    "extras",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def upsert_series_anbima(session: Session, row: dict[str, Any]) -> bool:
    """Insert or update one ``series_anbima`` row keyed by ``codigo_cetip``.

    Returns True when a row was written.
    """
    cetip = (row.get("codigo_cetip") or "").strip().upper()
    tipo = (row.get("tipo") or "").strip().lower()
    if not cetip or not tipo:
        return False

    now = _now()
    payload: dict[str, Any] = {
        "codigo_cetip": cetip,
        "tipo": tipo,
        "serie": row.get("serie"),
        "remuneracao": row.get("remuneracao"),
        "data_inicio_rentabilidade": _as_date(row.get("data_inicio_rentabilidade")),
        "quantidade_serie_emissao": row.get("quantidade_serie_emissao"),
        "volume_serie_emissao": row.get("volume_serie_emissao"),
        "vne": row.get("vne"),
        "quantidade_mercado_b3": row.get("quantidade_mercado_b3"),
        "data_emissao": _as_date(row.get("data_emissao")),
        "data_vencimento": _as_date(row.get("data_vencimento")),
        "isin": row.get("isin"),
        "lei_12431": row.get("lei_12431"),
        "emissao": row.get("emissao"),
        "empresa": row.get("empresa"),
        "setor": row.get("setor"),
        "cnpj": row.get("cnpj"),
        "garantia": row.get("garantia"),
        "extras": row.get("extras") or {},
        "criado_em": now,
        "atualizado_em": now,
    }

    update_cols = {col: payload[col] for col in _COLUMNS if col not in {"codigo_cetip"}}
    update_cols["atualizado_em"] = _now()

    stmt = insert(_TABLE).values(payload)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_series_anbima_cetip",
        set_=update_cols,
    )
    session.execute(stmt)
    return True
