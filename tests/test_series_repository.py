"""Series-first upsert skip rules and document M2M linkage (PostgreSQL)."""

from __future__ import annotations

from pathlib import Path
import os
import sys
import uuid

import pytest
from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.config import ScraperConfig
from shared.db import ensure_schema, session_scope
from shared.models import Documento, DocumentoSerie, Serie
from shared.records import DocumentoData, SerieData
from shared.repository import link_documento_series, upsert_documento, upsert_serie


def _pg_available() -> bool:
    try:
        cfg = _config()
        with session_scope(cfg) as session:
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
    cfg = _config()
    with session_scope(cfg) as sess:
        # Isolate each test under a unique fonte prefix.
        yield sess
        sess.rollback()


@pytest.fixture()
def fonte(session):
    name = f"t_{uuid.uuid4().hex[:10]}"
    yield name
    session.execute(text("DELETE FROM documentos WHERE fonte = :f"), {"f": name})
    session.execute(text("DELETE FROM series WHERE fonte = :f"), {"f": name})
    session.commit()


def _serie(fonte: str, **kwargs) -> SerieData:
    defaults = {
        "fonte": fonte,
        "id_origem": "s1",
        "numero_serie": "1",
        "isin": f"BR{uuid.uuid4().hex[:9].upper()}",
        "codigo_cetip": f"C{uuid.uuid4().hex[:8].upper()}",
    }
    defaults.update(kwargs)
    return SerieData(**defaults)


def test_skip_when_both_keys_missing(session, fonte):
    result = upsert_serie(
        session, _serie(fonte, isin=None, codigo_cetip=None, id_origem="nokeys")
    )
    assert result.status == "skipped_no_keys"
    assert result.serie_id is None
    assert (
        session.scalar(select(Serie).where(Serie.fonte == fonte).limit(1)) is None
    )


def test_happy_path_isin_only(session, fonte):
    isin = f"BR{uuid.uuid4().hex[:9].upper()}"
    result = upsert_serie(
        session, _serie(fonte, codigo_cetip=None, isin=isin, id_origem="isin-only")
    )
    assert result.status == "inserted"
    row = session.get(Serie, result.serie_id)
    assert row.isin == isin
    assert row.codigo_cetip is None


def test_happy_path_cetip_only(session, fonte):
    cetip = f"C{uuid.uuid4().hex[:8].upper()}"
    result = upsert_serie(
        session, _serie(fonte, isin=None, codigo_cetip=cetip, id_origem="cetip-only")
    )
    assert result.status == "inserted"
    row = session.get(Serie, result.serie_id)
    assert row.codigo_cetip == cetip
    assert row.isin is None


def test_happy_path_both_keys_update(session, fonte):
    data = _serie(fonte, id_origem="both")
    first = upsert_serie(session, data)
    assert first.status == "inserted"
    again = upsert_serie(
        session,
        _serie(
            fonte,
            id_origem="both",
            isin=data.isin,
            codigo_cetip=data.codigo_cetip,
            operacao="updated",
        ),
    )
    assert again.status == "updated"
    assert again.serie_id == first.serie_id
    row = session.get(Serie, first.serie_id)
    assert row.operacao == "updated"


def test_unique_isin_conflict_skips(session, fonte):
    isin = f"BR{uuid.uuid4().hex[:9].upper()}"
    first = upsert_serie(
        session, _serie(fonte, id_origem="a", isin=isin, codigo_cetip="C1A")
    )
    assert first.status == "inserted"
    session.flush()
    second = upsert_serie(
        session, _serie(fonte, id_origem="b", isin=isin, codigo_cetip="C2B")
    )
    assert second.status == "skipped_isin_conflict"
    assert second.serie_id is None
    assert session.scalar(select(Serie).where(Serie.fonte == fonte, Serie.id_origem == "b")) is None


def test_unique_cetip_conflict_skips(session, fonte):
    cetip = f"C{uuid.uuid4().hex[:8].upper()}"
    first = upsert_serie(
        session,
        _serie(fonte, id_origem="a", isin=f"BR{uuid.uuid4().hex[:9].upper()}", codigo_cetip=cetip),
    )
    assert first.status == "inserted"
    session.flush()
    second = upsert_serie(
        session,
        _serie(
            fonte,
            id_origem="b",
            isin=f"BR{uuid.uuid4().hex[:9].upper()}",
            codigo_cetip=cetip,
        ),
    )
    assert second.status == "skipped_cetip_conflict"
    assert second.serie_id is None


def test_document_m2m_links_multiple_series(session, fonte):
    s1 = upsert_serie(
        session,
        _serie(
            fonte,
            id_origem="s1",
            isin=f"BR{uuid.uuid4().hex[:9].upper()}",
            codigo_cetip="M2M1X",
        ),
    )
    s2 = upsert_serie(
        session,
        _serie(
            fonte,
            id_origem="s2",
            isin=f"BR{uuid.uuid4().hex[:9].upper()}",
            codigo_cetip="M2M2X",
        ),
    )
    session.flush()
    doc_id = upsert_documento(
        session,
        fonte,
        DocumentoData(
            link_documento=f"https://example.com/{uuid.uuid4().hex}.pdf",
            titulo="Termo",
            emissao_id="parent-1",
        ),
    )
    assert doc_id is not None
    link_documento_series(session, doc_id, [s1.serie_id, s2.serie_id])
    session.flush()

    links = session.scalars(
        select(DocumentoSerie).where(DocumentoSerie.documento_id == doc_id)
    ).all()
    assert {link.serie_id for link in links} == {s1.serie_id, s2.serie_id}
    doc = session.get(Documento, doc_id)
    assert doc.emissao_id == "parent-1"
