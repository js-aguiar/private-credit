"""SQLAlchemy ORM models (series-first).

``series`` is the primary scraped entity. ``isin`` and ``codigo_cetip`` are UNIQUE when
present; rows missing both are skipped at upsert time. Documents attach to many séries
via ``documentos_series``. ``emissao_id`` on series/documents is a source grouping
string, not a foreign key.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Serie(Base):
    """One row per série (primary catalog entity)."""

    __tablename__ = "series"

    serie_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    fonte: Mapped[str] = mapped_column(String(30), nullable=False)
    id_origem: Mapped[str] = mapped_column(String(255), nullable=False)
    link: Mapped[str | None] = mapped_column(Text)

    isin: Mapped[str | None] = mapped_column(String(20))
    codigo_cetip: Mapped[str | None] = mapped_column(String(30))

    # Source emission grouping (string, not an FK)
    emissao_id: Mapped[str | None] = mapped_column(String(255))
    numero_emissao: Mapped[str | None] = mapped_column(String(50))
    numero_serie: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    operacao: Mapped[str | None] = mapped_column(Text)
    devedor: Mapped[str | None] = mapped_column(Text)
    ano_emissao: Mapped[int | None] = mapped_column(Integer)
    tipo_ativo: Mapped[str | None] = mapped_column(String(50))
    series_raw: Mapped[str | None] = mapped_column(Text)
    valor_total: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))

    valor: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    remuneracao: Mapped[str | None] = mapped_column(Text)
    indexador: Mapped[str | None] = mapped_column(String(120))
    data_emissao: Mapped[date | None] = mapped_column(Date)
    data_vencimento: Mapped[date | None] = mapped_column(Date)
    quantidade: Mapped[int | None] = mapped_column(BigInteger)
    rating: Mapped[str | None] = mapped_column(String(80))

    data_scraping: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detalhes_coletados: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ultima_verificacao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extras: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    documento_links: Mapped[list["DocumentoSerie"]] = relationship(
        back_populates="serie", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("fonte", "id_origem", name="uq_series_fonte_id_origem"),
        UniqueConstraint("isin", name="uq_series_isin"),
        UniqueConstraint("codigo_cetip", name="uq_series_codigo_cetip"),
        Index("ix_series_numero_emissao", "numero_emissao"),
        Index("ix_series_emissao_id", "emissao_id"),
        Index("ix_series_devedor", "devedor"),
        Index(
            "ix_series_recheck",
            "fonte",
            "detalhes_coletados",
            "ultima_verificacao",
        ),
    )


class Documento(Base):
    """One row per document; linked to one or more séries via documentos_series."""

    __tablename__ = "documentos"

    documento_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    fonte: Mapped[str] = mapped_column(String(30), nullable=False)
    emissao_id: Mapped[str | None] = mapped_column(String(255))
    isin: Mapped[str | None] = mapped_column(String(20))
    numero_emissao: Mapped[str | None] = mapped_column(String(50))
    codigo_cetip: Mapped[str | None] = mapped_column(String(30))

    titulo: Mapped[str | None] = mapped_column(Text)
    tipo_documento: Mapped[str | None] = mapped_column(String(120))
    link_documento: Mapped[str] = mapped_column(Text, nullable=False)
    id_origem_arquivo: Mapped[str | None] = mapped_column(String(255))
    data_documento: Mapped[date | None] = mapped_column(Date)
    data_insercao: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    extras: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    serie_links: Mapped[list["DocumentoSerie"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("fonte", "link_documento", name="uq_documentos_fonte_link"),
        Index("ix_documentos_emissao_id", "emissao_id"),
        Index("ix_documentos_isin", "isin"),
    )


class DocumentoSerie(Base):
    """Junction: document ↔ série (many-to-many)."""

    __tablename__ = "documentos_series"

    documento_id: Mapped[int] = mapped_column(
        ForeignKey("documentos.documento_id", ondelete="CASCADE"), primary_key=True
    )
    serie_id: Mapped[int] = mapped_column(
        ForeignKey("series.serie_id", ondelete="CASCADE"), primary_key=True
    )

    documento: Mapped["Documento"] = relationship(back_populates="serie_links")
    serie: Mapped["Serie"] = relationship(back_populates="documento_links")

    __table_args__ = (Index("ix_documentos_series_serie_id", "serie_id"),)
