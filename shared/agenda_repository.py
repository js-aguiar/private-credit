"""Persistence helpers for the ANBIMA ``agenda`` table."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import AgendaEvento

_AGENDA = AgendaEvento.__table__


def _now() -> datetime:
    return datetime.now(timezone.utc)


def replace_agenda_for_cetip(
    session: Session,
    codigo_cetip: str,
    rows: list[dict[str, Any]],
) -> int:
    """Replace all agenda rows for one CETIP with ``rows`` (delete + insert).

    Returns the number of inserted rows. Using replace avoids PostgreSQL UNIQUE
    quirks around NULL columns in the natural key.
    """
    cetip = (codigo_cetip or "").strip().upper()
    if not cetip:
        return 0

    session.execute(delete(_AGENDA).where(_AGENDA.c.codigo_cetip == cetip))
    if not rows:
        return 0

    now = _now()
    payloads: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for row in rows:
        data_evento = row.get("data_evento")
        data_liquidacao = row.get("data_liquidacao")
        if isinstance(data_evento, datetime):
            data_evento = data_evento.date()
        if isinstance(data_liquidacao, datetime):
            data_liquidacao = data_liquidacao.date()
        if data_evento is not None and not isinstance(data_evento, date):
            data_evento = None
        if data_liquidacao is not None and not isinstance(data_liquidacao, date):
            data_liquidacao = None

        payload = {
            "codigo_cetip": cetip,
            "data_evento": data_evento,
            "data_liquidacao": data_liquidacao,
            "tipo_evento": row.get("tipo_evento"),
            "taxa": row.get("taxa"),
            "valor_pago": row.get("valor_pago"),
            "status": row.get("status"),
            "criado_em": now,
            "atualizado_em": now,
        }
        key = (
            payload["codigo_cetip"],
            payload["data_evento"],
            payload["data_liquidacao"],
            payload["tipo_evento"],
            payload["taxa"],
            payload["valor_pago"],
            payload["status"],
        )
        if key in seen:
            continue
        seen.add(key)
        payloads.append(payload)

    if not payloads:
        return 0

    # ON CONFLICT keeps re-runs idempotent if unique key matches.
    stmt = insert(_AGENDA).values(payloads)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_agenda_natural",
        set_={"atualizado_em": _now()},
    )
    session.execute(stmt)
    return len(payloads)
