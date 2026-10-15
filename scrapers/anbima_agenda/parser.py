"""Map ANBIMA BFF agenda JSON rows into ``agenda`` table dicts."""

from __future__ import annotations

from typing import Any

from shared.parsing import clean_text, parse_br_date


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        text = str(value)
        return text or None
    return clean_text(str(value))


def parse_agenda_payload(payload: Any, codigo_cetip: str) -> list[dict]:
    """Extract agenda rows from one BFF JSON body (possibly paginated)."""
    if not isinstance(payload, dict):
        return []

    # SPA stores useful fields under ``data``; tolerate a bare Spring page too.
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, list):
        # Some errors return {msg: ...}
        return []

    rows: list[dict] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        evento = item.get("evento")
        tipo_evento = _as_text(evento)
        if tipo_evento and tipo_evento != "-":
            # Match SPA presentation: capitalize first letter.
            tipo_evento = tipo_evento[:1].upper() + tipo_evento[1:].lower()

        taxa = item.get("taxa")
        if taxa is None:
            taxa = item.get("percentual_taxa") or item.get("percentualTaxa")

        valor = item.get("valor_pago")
        if valor is None:
            valor = item.get("valorPago") or item.get("valor")

        status = item.get("status")
        if isinstance(status, dict):
            status = status.get("descricao") or status.get("value") or status.get("raw")

        data_evento = parse_br_date(str(item.get("data_evento") or item.get("dataEvento") or ""))
        data_liquidacao = parse_br_date(
            str(item.get("data_liquidacao") or item.get("dataLiquidacao") or "")
        )

        rows.append(
            {
                "codigo_cetip": codigo_cetip,
                "data_evento": data_evento,
                "data_liquidacao": data_liquidacao,
                "tipo_evento": tipo_evento if tipo_evento != "-" else None,
                "taxa": _as_text(taxa) if _as_text(taxa) != "-" else None,
                "valor_pago": _as_text(valor) if _as_text(valor) != "-" else None,
                "status": _as_text(status) if _as_text(status) != "-" else None,
            }
        )
    return rows


def page_meta(payload: Any) -> tuple[int, int, int]:
    """Return ``(page_number, total_pages, total_elements)`` from a BFF body."""
    if not isinstance(payload, dict):
        return 0, 1, 0
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return 0, 1, 0
    number = int(data.get("number") or 0)
    total_pages = int(data.get("totalPages") or data.get("total_pages") or 1)
    total_elements = int(
        data.get("totalElements") or data.get("total_elements") or data.get("total") or 0
    )
    if total_pages < 1:
        total_pages = 1
    return number, total_pages, total_elements
