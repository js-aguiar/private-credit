"""Parsing helpers for Brazilian-formatted data (currency, dates, ISIN, etc.)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_WS_RE = re.compile(r"\s+")
_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
_CETIP_RE = re.compile(r"\b((?:CRA|CRI)[A-Z0-9]{6,9})\b", re.IGNORECASE)
_MONEY_RE = re.compile(r"-?\d[\d.]*(?:,\d+)?")


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace and strip; return None for empty results."""
    if value is None:
        return None
    cleaned = _WS_RE.sub(" ", value).strip()
    return cleaned or None


def parse_brl_amount(value: str | None) -> Decimal | None:
    """Parse Brazilian currency strings like ``R$ 200.000.000,00`` into a Decimal."""
    if value is None:
        return None
    match = _MONEY_RE.search(value)
    if not match:
        return None
    raw = match.group(0).replace(".", "").replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def parse_br_date(value: str | None) -> date | None:
    """Parse dates in common BR/ISO formats into a ``date``."""
    if value is None:
        return None
    text = clean_text(value)
    if not text:
        return None
    # Keep only the leading date token if a datetime string is passed.
    token = text.split(" ")[0].split("T")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"-?\d+", value.replace(".", ""))
    return int(match.group(0)) if match else None


def extract_isins(value: str | None) -> list[str]:
    if not value:
        return []
    return _ISIN_RE.findall(value.upper())


def extract_cetip_codes(value: str | None) -> list[str]:
    if not value:
        return []
    return [code.upper() for code in _CETIP_RE.findall(value)]


def first_or_none(values: list[str]) -> str | None:
    return values[0] if values else None
