"""Tests for Opea parent-emission grouping and series/document mapping."""

from __future__ import annotations

from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scrapers.opea.scraper import (
    document_matches_emission,
    emission_file_code,
    natureza_from_parent,
    normalize_serie_number,
    parent_codigo_opea,
    parse_remuneracao,
    serie_from_detail,
    serie_from_list_item,
    vehicle_from_parent,
)


def test_parent_codigo_opea():
    assert parent_codigo_opea("CRI.624.CIA.1") == "CRI.624.CIA"
    assert parent_codigo_opea("CRA.228.CIA.1") == "CRA.228.CIA"
    assert natureza_from_parent("CRA.228.CIA") == "CRA"
    assert vehicle_from_parent("CRI.228.CIA") == "CIA"
    assert vehicle_from_parent("CRI.228.TRU") == "TRU"
    assert vehicle_from_parent("DEB.2.GCII") == "GCII"


def test_normalize_serie_number_and_remuneracao():
    assert normalize_serie_number(1.0) == "1"
    assert normalize_serie_number("2") == "2"
    assert parse_remuneracao("CDI + 1,3000% a.a.") == "CDI + 1,3000% a.a."
    assert parse_remuneracao({"descricao": "IPCA + 9%"}) == "IPCA + 9%"


def test_document_filter_separates_cra_and_cri():
    assert document_matches_emission("OP_CRA_E0228_S001_TS.pdf", "CRA", "E0228", "CIA")
    assert not document_matches_emission("OP_CRI_E0228_S001_TS.pdf", "CRA", "E0228", "CIA")
    assert document_matches_emission("OP_CRI_E0228_S001_TS.pdf", "CRI", "E0228", "CIA")
    assert emission_file_code(228) == "E0228"


def test_document_filter_separates_cia_and_tru_books():
    """Patriani III (CIA) vs Dutra (TRU) share CRI + E0228 — vehicle decides."""
    op = "OP_CRI_E0228_S001_S002_S003_S004_S005_S006_S007_S008_TS_20231121.pdf"
    tru = "TRU_CRI_E0228_S001_TS.pdf"
    assert document_matches_emission(op, "CRI", "E0228", "CIA")
    assert not document_matches_emission(op, "CRI", "E0228", "TRU")
    assert document_matches_emission(tru, "CRI", "E0228", "TRU")
    assert not document_matches_emission(tru, "CRI", "E0228", "CIA")


def test_document_filter_rejects_foreign_prefixes_and_pls_op():
    gs = "GS_CRA_E0032_S001_DFPS_20251231.pdf"
    gcii = "GCII_DEB_E0002_S001_ANE_20180302.pdf"
    op_cra = "OP_CRA_E0032_S001_DFPS_20250930.pdf"
    assert not document_matches_emission(gs, "CRA", "E0032", "CIA")
    assert not document_matches_emission(gs, "CRA", "E0032", "TRU")
    assert not document_matches_emission(gs, "CRA", "E0032", "PLS")
    assert document_matches_emission(gcii, "DEB", "E0002", "GCII")
    assert not document_matches_emission(gcii, "DEB", "E0002", "CIA")
    assert not document_matches_emission(op_cra, "CRA", "E0032", "PLS")
    assert document_matches_emission(op_cra, "CRA", "E0032", "CIA")
    assert document_matches_emission("TRU_CRA_E0032_S001_TS.pdf", "CRA", "E0032", "TRU")
    assert not document_matches_emission("TRU_CRA_E0032_S001_TS.pdf", "CRA", "E0032", "CIA")


def test_serie_from_list_item_maps_keys_and_parent():
    item = {
        "codigoOpea": "CRI.624.CIA.1",
        "emissao": 624,
        "serie": 1,
        "isin": "BRRBRACIR4R9",
        "codigoIf": "26H1942318",
        "nomeDevedor": "LOG PRIME II",
        "naturezaOperacao": "Corporativo",
    }
    serie = serie_from_list_item(item)
    assert serie is not None
    assert serie.id_origem == "CRI.624.CIA.1"
    assert serie.emissao_id == "CRI.624.CIA"
    assert serie.isin == "BRRBRACIR4R9"
    assert serie.codigo_cetip == "26H1942318"
    assert serie.numero_emissao == "624"
    assert serie.devedor == "LOG PRIME II"


def test_serie_from_list_item_skips_without_keys():
    assert (
        serie_from_list_item(
            {"codigoOpea": "CRI.1.CIA.1", "emissao": 1, "serie": 1}
        )
        is None
    )


def test_serie_from_detail_maps_string_remuneracao():
    detail = {
        "serie": 1.0,
        "emissao": 624,
        "codigoIsin": "BRRBRACIR4R9",
        "codigoCetipBbb": "26H1942318",
        "dataEmissaoSerie": "2026-08-03",
        "dataVencimentoSerie": "2027-04-25",
        "quantidadeEmitida": 119000.0,
        "quantidadeIntegralizada": 119000.0,
        "remuneracao": "CDI + 1,3000% a.a.",
        "precoUnitario": 1000.0,
        "classeOperacao": {"descricao": "Única"},
        "concentracao": {"value": "Concentrado"},
        "descricaoSegmentoOperacao": "Logístico",
        "agenteFiduciario": {"nomeSimplificado": "Vortx DTVM"},
        "pagamentoPassivo": {
            "periodicidadeFrequenciaJuros": {"descricao": "Mensal"},
            "periodicidadeFrequenciaAmortizacao": {"descricao": "Variável"},
        },
    }
    serie = serie_from_detail("CRI.624.CIA.1", detail, "624")
    assert serie is not None
    assert serie.fonte == "opea"
    assert serie.id_origem == "CRI.624.CIA.1"
    assert serie.emissao_id == "CRI.624.CIA"
    assert serie.numero_serie == "1"
    assert serie.remuneracao == "CDI + 1,3000% a.a."
    assert serie.quantidade == 119000
    assert serie.extras["classe"] == "Única"
    assert serie.extras["segmento"] == "Logístico"
    assert serie.extras["agente_fiduciario"] == "Vortx DTVM"
    assert serie.extras["periodicidade_juros"] == "Mensal"


@pytest.mark.integration
def test_live_log_prime_docs_identical_across_series():
    base = "https://app.opea.com.br/bff/v1/api"
    codes = ["CRI.624.CIA.1", "CRI.624.CIA.2", "CRI.624.CIA.3"]
    doc_id_sets = []
    for code in codes:
        detail = httpx.get(
            f"{base}/emissao/passivosoperacoes/detalhe",
            params={"codigoOpea": code},
            timeout=30,
        ).json()["content"]
        files = httpx.get(
            f"{base}/cedoc/files",
            params={"idCedoc": detail["idCedoc"]},
            timeout=60,
        ).json()["children"]
        matched = {
            child["id"]
            for child in files
            if document_matches_emission(child.get("name") or "", "CRI", "E0624", "CIA")
        }
        doc_id_sets.append(matched)
        serie = serie_from_detail(code, detail, "624")
        assert serie is not None
        assert serie.isin
        assert serie.remuneracao
    assert len(doc_id_sets[0]) >= 1
    assert doc_id_sets[0] == doc_id_sets[1] == doc_id_sets[2]
