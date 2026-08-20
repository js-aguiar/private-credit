"""Plain data containers passed from scrapers to the repository layer.

These decouple the site-specific scraping logic from the SQLAlchemy models: a scraper
only needs to produce ``EmissaoData`` / ``SerieData`` / ``DocumentoData`` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class EmissaoData:
    """List-level (and optionally detail-level) fields for one emission."""

    fonte: str
    id_origem: str  # stable per-source identifier; used as the upsert key with `fonte`
    link: str | None = None
    isin: str | None = None
    numero_emissao: str | None = None
    codigos_cetip: str | None = None
    operacao: str | None = None
    devedor: str | None = None
    ano_emissao: int | None = None
    tipo_ativo: str | None = None
    series_raw: str | None = None
    valor_total: Decimal | None = None
    indexador: str | None = None
    data_emissao: date | None = None
    data_vencimento: date | None = None
    rating: str | None = None
    extras: dict = field(default_factory=dict)


@dataclass
class SerieData:
    numero_serie: str
    isin: str | None = None
    numero_emissao: str | None = None
    codigo_cetip: str | None = None
    valor: Decimal | None = None
    remuneracao: str | None = None
    indexador: str | None = None
    data_emissao: date | None = None
    data_vencimento: date | None = None
    quantidade: int | None = None
    rating: str | None = None
    extras: dict = field(default_factory=dict)


@dataclass
class DocumentoData:
    link_documento: str
    titulo: str | None = None
    tipo_documento: str | None = None
    data_documento: date | None = None
    isin: str | None = None
    numero_emissao: str | None = None
    codigo_cetip: str | None = None
    id_origem_arquivo: str | None = None
    extras: dict = field(default_factory=dict)


@dataclass
class DetailResult:
    """What a scraper returns for a single emission's detail page."""

    # Field-name -> value updates applied to the `emissoes` row (subset of EmissaoData).
    emissao_updates: dict = field(default_factory=dict)
    series: list[SerieData] = field(default_factory=list)
    documentos: list[DocumentoData] = field(default_factory=list)
