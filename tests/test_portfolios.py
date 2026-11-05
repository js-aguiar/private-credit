"""Portfolio (carteiras) helpers against local Postgres."""

from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web" / "api"))

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "securitizacao")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", "postgres")
os.environ.setdefault("DB_SSLMODE", "disable")

from shared.config import ScraperConfig
from shared.db import ensure_schema, session_scope
from shared.models import Serie
from portfolios import (
    add_serie_to_portfolio,
    create_portfolio_with_series,
    delete_portfolio,
    find_series_by_code,
    get_portfolio,
    list_portfolios,
    remove_serie_from_portfolio,
)


def _config() -> ScraperConfig:
    return ScraperConfig.from_env("test_portfolios")


@pytest.fixture(scope="module")
def seeded_series():
    ensure_schema(_config())
    cetip = f"T{uuid.uuid4().hex[:8].upper()}"
    isin = f"BRTEST{uuid.uuid4().hex[:6].upper()}"
    with session_scope(_config()) as session:
        serie = Serie(
            fonte="ecoagro",
            id_origem=f"test-portfolio-{uuid.uuid4().hex}",
            codigo_cetip=cetip,
            isin=isin,
            numero_serie="1",
            devedor="Portfolio Test Co",
            valor=Decimal("1000.00"),
        )
        session.add(serie)
        session.flush()
        serie_id = serie.serie_id
    return {"serie_id": serie_id, "cetip": cetip, "isin": isin}


def test_create_requires_series():
    with session_scope(_config()) as session:
        with pytest.raises(ValueError, match="at least one"):
            create_portfolio_with_series(session, nome="Empty", serie_ids=[])


def test_portfolio_roundtrip(seeded_series):
    nome = f"Test PF {uuid.uuid4().hex[:8]}"
    with session_scope(_config()) as session:
        matches = find_series_by_code(session, cetip=seeded_series["cetip"])
        assert len(matches) == 1
        assert matches[0]["id"] == seeded_series["serie_id"]

        created = create_portfolio_with_series(
            session, nome=nome, serie_ids=[seeded_series["serie_id"]]
        )
        assert created["nome"] == nome
        assert created["series_count"] == 1
        assert created["series"][0]["codigo_cetip"] == seeded_series["cetip"]

        listed = list_portfolios(session)
        assert any(item["nome"] == nome for item in listed["items"])

        detail = get_portfolio(session, nome)
        assert detail is not None
        assert detail["series"][0]["isin"] == seeded_series["isin"]

        removed = remove_serie_from_portfolio(
            session, nome=nome, serie_id=seeded_series["serie_id"]
        )
        assert removed["deleted"] is True
        assert get_portfolio(session, nome) is None


def test_add_validates_series_and_unique_name(seeded_series):
    nome = f"Test PF {uuid.uuid4().hex[:8]}"
    with session_scope(_config()) as session:
        first = add_serie_to_portfolio(
            session, nome=nome, cetip=seeded_series["cetip"]
        )
        assert first["series_count"] == 1
        with pytest.raises(LookupError, match="portfolio_name_exists"):
            create_portfolio_with_series(
                session, nome=nome.upper(), serie_ids=[seeded_series["serie_id"]]
            )
        delete_portfolio(session, nome=nome)
