from decimal import Decimal
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scrapers.ecoagro.parsers import (
    build_series_from_emissao,
    emission_isin_from_series,
    merge_series_from_detail,
    parse_detail,
    parse_series_from_detail,
)

DETAIL_URL = "https://ecoagro.agr.br/emissoes-integra/326"
FIXTURE = Path(__file__).with_name("fixtures") / "ecoagro_emissao_326.html"


def _fixture_html() -> str:
    if not FIXTURE.exists():
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(httpx.get(DETAIL_URL, timeout=30).text, encoding="utf-8")
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_series_from_detail_emissao_175():
    html = httpx.get(DETAIL_URL, timeout=30).text
    series = parse_series_from_detail(html)
    assert len(series) == 2
    by_numero = {s.numero_serie: s for s in series}
    assert by_numero["1"].isin == "BRECOACRAA72"
    assert by_numero["1"].codigo_cetip == "CRA02200795"
    assert by_numero["1"].remuneracao == "IPCA + 8,1191%"
    assert by_numero["2"].isin == "BRECOACRAAU8"
    assert by_numero["2"].codigo_cetip == "CRA02200796"


def test_merge_series_from_detail_enriches_list_baseline():
    baseline = build_series_from_emissao("175", "1-2", "CRA02200795 CRA02200796")
    detail = parse_series_from_detail(httpx.get(DETAIL_URL, timeout=30).text)
    merged = merge_series_from_detail(baseline, detail)
    assert emission_isin_from_series(merged) is None
    by_numero = {s.numero_serie: s for s in merged}
    assert by_numero["1"].isin == "BRECOACRAA72"
    assert by_numero["2"].isin == "BRECOACRAAU8"


def test_parse_series_from_detail_offline_fixture():
    html = _fixture_html()
    series = parse_series_from_detail(html)
    assert {s.isin for s in series} == {"BRECOACRAA72", "BRECOACRAAU8"}


def test_parse_detail_emission_fields_offline_fixture():
    html = _fixture_html()
    updates, _docs = parse_detail(html, "https://ecoagro.agr.br")
    assert updates.get("devedor") == "Adubos Araguaia"
    assert updates.get("valor_total") == Decimal("500000000.00")
    assert updates.get("valor") == Decimal("500000000.00")
    assert updates.get("numero_emissao") == "175"

    baseline = build_series_from_emissao("175", "1-2", "CRA02200795 CRA02200796")
    detail = parse_series_from_detail(html)
    emission = {
        key: updates[key]
        for key in ("devedor", "valor_total", "valor", "numero_emissao")
        if key in updates
    }
    merged = merge_series_from_detail(baseline, detail, emission=emission)
    assert all(s.devedor == "Adubos Araguaia" for s in merged)
    assert all(s.valor_total == Decimal("500000000.00") for s in merged)
    assert all(s.numero_emissao == "175" for s in merged)
