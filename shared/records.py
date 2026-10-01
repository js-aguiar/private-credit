"""Plain data containers passed from scrapers to the repository layer.

Series-first: scrapers produce ``SerieData`` / ``DocumentoData``. ``EmissaoData`` remains
as an optional listing helper some scrapers use before expanding to séries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class SerieData:
    """One série — the primary upsert unit."""

    fonte: str
    id_origem: str  # stable per-source id; upsert key with `fonte`
    numero_serie: str = ""
    isin: str | None = None
    codigo_cetip: str | None = None
    # Source emission grouping (string, not an FK)
    emissao_id: str | None = None
    numero_emissao: str | None = None
    link: str | None = None
    operacao: str | None = None
    devedor: str | None = None
    ano_emissao: int | None = None
    tipo_ativo: str | None = None
    series_raw: str | None = None
    valor_total: Decimal | None = None
    valor: Decimal | None = None
    remuneracao: str | None = None
    indexador: str | None = None
    data_emissao: date | None = None
    data_vencimento: date | None = None
    quantidade: int | None = None
    rating: str | None = None
    extras: dict = field(default_factory=dict)


@dataclass
class EmissaoData:
    """Optional listing-level helper (expanded to SerieData by scrapers)."""

    fonte: str
    id_origem: str
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
class DocumentoData:
    link_documento: str
    titulo: str | None = None
    tipo_documento: str | None = None
    data_documento: date | None = None
    isin: str | None = None
    numero_emissao: str | None = None
    codigo_cetip: str | None = None
    emissao_id: str | None = None
    id_origem_arquivo: str | None = None
    # Link to séries by their (fonte, id_origem) after series upserts.
    serie_id_origens: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


@dataclass
class DetailResult:
    """What a scraper returns for one série's detail fetch."""

    # Field-name -> value updates applied to the série being detailed.
    serie_updates: dict = field(default_factory=dict)
    # Sibling / newly discovered séries from the same detail payload.
    series: list[SerieData] = field(default_factory=list)
    documentos: list[DocumentoData] = field(default_factory=list)
