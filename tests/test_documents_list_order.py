"""Documents catalog list must page newest-first by data_documento."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import os
import sys
import uuid

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web" / "api"))

from shared.config import ScraperConfig
from shared.db import ensure_schema, session_scope
from shared.records import DocumentoData, SerieData
from shared.repository import link_documento_series, upsert_documento, upsert_serie
from queries import list_documents


def _pg_available() -> bool:
    try:
        with session_scope(_config()) as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _config() -> ScraperConfig:
    os.environ.setdefault("DB_HOST", "localhost")
    os.environ.setdefault("DB_PORT", "5432")
    os.environ.setdefault("DB_NAME", "securitizacao")
    os.environ.setdefault("DB_USER", "postgres")
    os.environ.setdefault("DB_PASSWORD", "postgres")
    os.environ.setdefault("DB_SSLMODE", "disable")
    return ScraperConfig.from_env("test")


pytestmark = pytest.mark.skipif(not _pg_available(), reason="PostgreSQL not available")


@pytest.fixture(scope="module", autouse=True)
def _schema():
    ensure_schema(_config())


@pytest.fixture()
def session():
    with session_scope(_config()) as sess:
        yield sess
        sess.rollback()


@pytest.fixture()
def fonte(session):
    name = f"t_{uuid.uuid4().hex[:10]}"
    yield name
    session.execute(text("DELETE FROM documentos WHERE fonte = :f"), {"f": name})
    session.execute(text("DELETE FROM series WHERE fonte = :f"), {"f": name})
    session.commit()


def test_list_documents_newest_date_first(session, fonte):
    serie = upsert_serie(
        session,
        SerieData(
            fonte=fonte,
            id_origem="s1",
            numero_serie="1",
            isin=f"BR{uuid.uuid4().hex[:9].upper()}",
            codigo_cetip=f"C{uuid.uuid4().hex[:8].upper()}",
            devedor="Acme Debtor",
        ),
    )
    session.flush()

    older_id = upsert_documento(
        session,
        fonte,
        DocumentoData(
            link_documento=f"https://example.com/{uuid.uuid4().hex}-old.pdf",
            titulo="Older",
            tipo_documento="Termo",
            data_documento=date(2020, 1, 1),
        ),
    )
    newer_id = upsert_documento(
        session,
        fonte,
        DocumentoData(
            link_documento=f"https://example.com/{uuid.uuid4().hex}-new.pdf",
            titulo="Newer",
            tipo_documento="Aviso",
            data_documento=date(2026, 6, 15),
        ),
    )
    assert older_id is not None and newer_id is not None
    # Insert older-looking id after newer date would still sort by date if we
    # wrongly paginated by documento_id ascending.
    link_documento_series(session, older_id, [serie.serie_id])
    link_documento_series(session, newer_id, [serie.serie_id])
    session.flush()

    page = list_documents(session, fonte=fonte, limit=1, offset=0)
    assert page["total"] == 2
    assert page["items"][0]["id"] == newer_id
    assert page["items"][0]["date"] == "2026-06-15"
    assert page["items"][0]["company"] == "Acme Debtor"
    assert page["items"][0]["document_type"] == "Aviso"

    page2 = list_documents(session, fonte=fonte, limit=1, offset=1)
    assert page2["items"][0]["id"] == older_id
    assert page2["has_more"] is False
