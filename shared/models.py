"""SQLAlchemy ORM models.

Table and column names are in Portuguese to match the terminology used by the source
websites. Business linking keys (``isin``, ``numero_emissao``, ``codigo_cetip``) are
carried on every table; each table also has a stable surrogate primary key and a
``extras`` JSONB column for site-specific fields.

Keying rationale: an emission (``emissoes``) can have several séries, each with its own
ISIN/CETIP code, so ISIN cannot be the single primary key across all tables. ISIN is
therefore enforced as a UNIQUE key on ``series`` (the level at which it is defined) and
duplicated onto the other tables for cross-linking, while joins use the ``emissao_id``
surrogate key.
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


class Emissao(Base):
    """One row per operation/emission (``emissao``)."""

    __tablename__ = "emissoes"

    emissao_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Source + identity
    fonte: Mapped[str] = mapped_column(String(30), nullable=False)
    id_origem: Mapped[str] = mapped_column(String(255), nullable=False)
    link: Mapped[str | None] = mapped_column(Text)

    # Business linking keys
    isin: Mapped[str | None] = mapped_column(String(20))
    numero_emissao: Mapped[str | None] = mapped_column(String(50))
    codigos_cetip: Mapped[str | None] = mapped_column(Text)

    # Emission attributes (superset across sources; null where unavailable)
    operacao: Mapped[str | None] = mapped_column(Text)
    devedor: Mapped[str | None] = mapped_column(Text)
    ano_emissao: Mapped[int | None] = mapped_column(Integer)
    tipo_ativo: Mapped[str | None] = mapped_column(String(50))
    series_raw: Mapped[str | None] = mapped_column("series", Text)
    valor_total: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    indexador: Mapped[str | None] = mapped_column(String(120))
    data_emissao: Mapped[date | None] = mapped_column(Date)
    data_vencimento: Mapped[date | None] = mapped_column(Date)
    rating: Mapped[str | None] = mapped_column(String(80))

    # Scrape metadata / incremental control
    data_scraping: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detalhes_coletados: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ultima_verificacao_detalhe: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extras: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    series: Mapped[list["Serie"]] = relationship(
        back_populates="emissao", cascade="all, delete-orphan"
    )
    documentos: Mapped[list["Documento"]] = relationship(
        back_populates="emissao", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("fonte", "id_origem", name="uq_emissoes_fonte_id_origem"),
        Index("ix_emissoes_isin", "isin"),
        Index("ix_emissoes_numero_emissao", "numero_emissao"),
        # Drives the re-check ordering: not-yet-detailed first, then oldest re-checks.
        Index(
            "ix_emissoes_recheck",
            "fonte",
            "detalhes_coletados",
            "ultima_verificacao_detalhe",
        ),
    )


class Serie(Base):
    """One row per série of an emission. ``isin`` is the unique business key."""

    __tablename__ = "series"

    serie_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    emissao_id: Mapped[int] = mapped_column(
        ForeignKey("emissoes.emissao_id", ondelete="CASCADE"), nullable=False
    )

    fonte: Mapped[str] = mapped_column(String(30), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(20))
    numero_emissao: Mapped[str | None] = mapped_column(String(50))
    numero_serie: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    codigo_cetip: Mapped[str | None] = mapped_column(String(30))

    valor: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    remuneracao: Mapped[str | None] = mapped_column(Text)
    indexador: Mapped[str | None] = mapped_column(String(120))
    data_emissao: Mapped[date | None] = mapped_column(Date)
    data_vencimento: Mapped[date | None] = mapped_column(Date)
    quantidade: Mapped[int | None] = mapped_column(BigInteger)
    rating: Mapped[str | None] = mapped_column(String(80))

    extras: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    emissao: Mapped["Emissao"] = relationship(back_populates="series")

    __table_args__ = (
        UniqueConstraint("isin", name="uq_series_isin"),
        UniqueConstraint("emissao_id", "numero_serie", name="uq_series_emissao_numero"),
        Index("ix_series_emissao_id", "emissao_id"),
    )


class Documento(Base):
    """One row per document attached to an emission (and optionally a série)."""

    __tablename__ = "documentos"

    documento_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    emissao_id: Mapped[int] = mapped_column(
        ForeignKey("emissoes.emissao_id", ondelete="CASCADE"), nullable=False
    )

    fonte: Mapped[str] = mapped_column(String(30), nullable=False)
    # Business linking keys (denormalized for convenient joins by ISIN/CETIP/emission).
    isin: Mapped[str | None] = mapped_column(String(20))
    numero_emissao: Mapped[str | None] = mapped_column(String(50))
    codigo_cetip: Mapped[str | None] = mapped_column(String(30))

    titulo: Mapped[str | None] = mapped_column(Text)
    tipo_documento: Mapped[str | None] = mapped_column(String(120))
    link_documento: Mapped[str] = mapped_column(Text, nullable=False)
    data_documento: Mapped[date | None] = mapped_column(Date)
    # When this row was first added to the table (set once, on insert).
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

    emissao: Mapped["Emissao"] = relationship(back_populates="documentos")

    __table_args__ = (
        UniqueConstraint("emissao_id", "link_documento", name="uq_documentos_emissao_link"),
        Index("ix_documentos_emissao_id", "emissao_id"),
        Index("ix_documentos_isin", "isin"),
    )
