"""Unit tests for Riza "Outras informações" series accordion mapping."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from scrapers.riza.scraper import RizaScraper, _format_remuneracao, _normalize_serie_numero


_DETAIL_SERIES = [
    {
        "id": 2521,
        "number": 1,
        "instrumentCode": "26H3422746",
        "isinCode": "BRIMWLCRIQW3",
        "type": "sênior",
        "icvm": "160",
        "dueDate": "2035-10-15T00:00:00Z",
        "firstPaymentDate": "2026-08-28T00:00:00Z",
        "params": {
            "indexer": "IPCA",
            "interestRate": 11.0,
            "amount": 61980,
            "unitValue": 1000,
            "totalValue": 61980000.0,
        },
    },
    {
        "id": 2522,
        "number": 2.0,
        "instrumentCode": "26H3422750",
        "isinCode": "BRIMWLCRIQX1",
        "type": "subordinada",
        "icvm": "160",
        "dueDate": "2043-10-15T00:00:00Z",
        "firstPaymentDate": "2026-08-28T00:00:00Z",
        "params": {
            "indexer": "IPCA",
            "interestRate": 8.0,
            "amount": 20660,
            "unitValue": 1000,
            "totalValue": 20660000.0,
        },
    },
]


def test_normalize_serie_numero():
    assert _normalize_serie_numero(1) == "1"
    assert _normalize_serie_numero(2.0) == "2"
    assert _normalize_serie_numero("3.0") == "3"
    assert _normalize_serie_numero("") == ""


def test_format_remuneracao():
    assert _format_remuneracao("IPCA", 11.0) == "IPCA + 11,00% a.a."
    assert _format_remuneracao(None, 8) == "8,00% a.a."
    assert _format_remuneracao("CDI", None) == "CDI"
    assert _format_remuneracao(None, None) is None


def test_extract_series_outras_informacoes():
    scraper = object.__new__(RizaScraper)
    serie_ctx = SimpleNamespace(numero_emissao="332", link=None, operacao=None)
    series = scraper._extract_series(_DETAIL_SERIES, serie_ctx, "op-332")

    assert len(series) == 2
    senior = series[0]
    assert senior.fonte == "riza"
    assert senior.emissao_id == "op-332"
    assert senior.id_origem == "op-332:26H3422746"
    assert senior.numero_serie == "1"
    assert senior.isin == "BRIMWLCRIQW3"
    assert senior.codigo_cetip == "26H3422746"
    assert senior.valor == Decimal("61980000.0")
    assert senior.quantidade == 61980
    assert senior.indexador == "IPCA"
    assert senior.remuneracao == "IPCA + 11,00% a.a."
    assert senior.data_emissao == date(2026, 8, 28)
    assert senior.data_vencimento == date(2035, 10, 15)
    assert senior.extras["tipo"] == "sênior"
    assert senior.extras["icvm"] == "160"
    assert senior.extras["valor_unitario"] == 1000
    outras = senior.extras["outras_informacoes"]
    assert outras["codigo_if"] == "26H3422746"
    assert outras["codigo_isin"] == "BRIMWLCRIQW3"
    assert outras["quantidade_papeis"] == 61980
    assert outras["taxa_juros_spread"] == 11.0
    assert outras["volume_total"] == 61980000
    assert outras["valor_unitario"] == 1000

    sub = series[1]
    assert sub.numero_serie == "2"
    assert sub.remuneracao == "IPCA + 8,00% a.a."
    assert sub.quantidade == 20660
    assert sub.extras["tipo"] == "subordinada"
