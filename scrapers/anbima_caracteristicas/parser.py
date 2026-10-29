"""Map ANBIMA BFF características JSON into ``series_anbima`` row dicts."""

from __future__ import annotations

from typing import Any

from shared.parsing import clean_text, parse_br_date

# Known column keys we persist explicitly (everything else goes to extras).
_KNOWN_COLUMNS = {
    "codigo_cetip",
    "tipo",
    "serie",
    "remuneracao",
    "data_inicio_rentabilidade",
    "quantidade_serie_emissao",
    "volume_serie_emissao",
    "vne",
    "quantidade_mercado_b3",
    "data_emissao",
    "data_vencimento",
    "isin",
    "lei_12431",
    "emissao",
    "empresa",
    "setor",
    "cnpj",
    "garantia",
    "extras",
}

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "serie": (
        "serie",
        "numero_serie",
        "numeroSerie",
        "serie_emissao",
        "serieEmissao",
    ),
    "remuneracao": ("remuneracao", "Remuneracao", "indexador_taxa", "indexadorTaxa"),
    "data_inicio_rentabilidade": (
        "data_inicio_rentabilidade",
        "dataInicioRentabilidade",
        "data_inicio",
        "dataInicio",
    ),
    "quantidade_serie_emissao": (
        "quantidade_serie_emissao",
        "quantidadeSerieEmissao",
        "quantidade_serie_na_data_de_emissao",
        "quantidade_serie",
        "quantidadeSerie",
        "quantidade_emissao",
        "quantidadeEmissao",
    ),
    "volume_serie_emissao": (
        "volume_serie_emissao",
        "volumeSerieEmissao",
        "volume_serie_na_data_de_emissao",
        "volume_serie",
        "volumeSerie",
        "volume_emissao",
        "volumeEmissao",
    ),
    "vne": (
        "vne",
        "valor_nominal_emissao",
        "valorNominalEmissao",
        "valor_nominal_na_emissao",
        "valor_nominal",
        "valorNominal",
    ),
    "quantidade_mercado_b3": (
        "quantidade_mercado_b3",
        "quantidadeMercadoB3",
        "quantidade_em_mercado_na_b3",
        "quantidade_mercado",
        "quantidadeMercado",
        "quantidade_em_mercado",
        "quantidadeEmMercado",
    ),
    "data_emissao": ("data_emissao", "dataEmissao"),
    "data_vencimento": ("data_vencimento", "dataVencimento", "vencimento"),
    "isin": ("isin", "codigo_isin", "codigoIsin"),
    "lei_12431": (
        "lei_12431",
        "lei12431",
        "lei_12_431",
        "incentivo_lei_12431",
        "incentivoLei12431",
        "incentivo_fiscal",
        "incentivoFiscal",
        "enquadra_lei_12431",
        "enquadraLei12431",
    ),
    "emissao": (
        "emissao",
        "numero_emissao",
        "numeroEmissao",
        "serie_emissao",
        "serieEmissao",
    ),
    "empresa": (
        "empresa",
        "emissor",
        "devedor",
        "nome_empresa",
        "nomeEmpresa",
        "razao_social",
        "razaoSocial",
    ),
    "setor": ("setor", "setor_economico", "setorEconomico"),
    "cnpj": ("cnpj", "cnpj_emissor", "cnpjEmissor", "cnpj_devedor", "cnpjDevedor"),
    "garantia": (
        "garantia",
        "garantias",
        "tipo_garantia",
        "tipoGarantia",
        "especie_garantia",
        "especieGarantia",
    ),
}


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Sim" if value else "Não"
    if isinstance(value, (int, float)):
        text = str(value)
        return text or None
    if isinstance(value, dict):
        for key in ("descricao", "nome", "value", "label", "raw", "texto"):
            if key in value:
                return _as_text(value.get(key))
        return None
    if isinstance(value, list):
        parts = [_as_text(item) for item in value]
        joined = "; ".join(part for part in parts if part)
        return joined or None
    text = clean_text(str(value))
    if text in {None, "-", "—", "null", "None"}:
        return None
    return text


def _pick(data: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if key in data and data[key] is not None and data[key] != "":
            return data[key]
    # Case-insensitive fallback.
    lower_map = {str(k).lower(): v for k, v in data.items()}
    for key in aliases:
        hit = lower_map.get(key.lower())
        if hit is not None and hit != "":
            return hit
    return None


def _unwrap_payload(payload: Any) -> dict[str, Any]:
    """Normalize BFF envelope to a flat dict of características fields."""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return {}
    # Some endpoints nest under caracteristicas / detalhes / serie.
    for nest_key in ("caracteristicas", "detalhes", "serie", "ativo", "content"):
        nested = data.get(nest_key)
        if isinstance(nested, dict):
            merged = {**data, **nested}
            return merged
        if isinstance(nested, list) and nested and isinstance(nested[0], dict):
            merged = {**data, **nested[0]}
            return merged
    return data


def _nested_dict(data: dict[str, Any], *path: str) -> dict[str, Any]:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(key)
    return cur if isinstance(cur, dict) else {}


def parse_caracteristicas_payload(
    payload: Any,
    *,
    codigo_cetip: str,
    tipo: str,
) -> dict[str, Any]:
    """Extract one ``series_anbima`` row from a BFF características JSON body."""
    data = _unwrap_payload(payload)
    if not data:
        return {
            "codigo_cetip": codigo_cetip,
            "tipo": tipo,
            "extras": {"raw": payload} if payload is not None else {},
        }

    emissao_obj = _nested_dict(data, "emissao")
    emissor_obj = _nested_dict(emissao_obj, "emissor") or _nested_dict(data, "emissor")
    # CRI/CRA pages may nest company under securitizadora / devedor objects.
    securitizadora_obj = _nested_dict(data, "securitizadora")
    if not isinstance(data.get("devedor"), dict):
        # Flatten scalar/object company candidates into lookup dict.
        pass

    serie = _as_text(_pick(data, _FIELD_ALIASES["serie"]))
    emissao = _as_text(emissao_obj.get("numero_emissao")) or _as_text(
        _pick(data, ("numero_emissao", "numeroEmissao"))
    )

    empresa = (
        _as_text(emissor_obj.get("nome"))
        or _as_text(emissor_obj.get("razao_social"))
        or _as_text(data.get("devedor") if not isinstance(data.get("devedor"), dict) else None)
        or _as_text(_nested_dict(data, "devedor").get("nome"))
        or _as_text(securitizadora_obj.get("nome"))
        or _as_text(_pick(data, _FIELD_ALIASES["empresa"]))
    )
    cnpj = (
        _as_text(emissor_obj.get("cnpj"))
        or _as_text(_nested_dict(data, "devedor").get("cnpj"))
        or _as_text(securitizadora_obj.get("cnpj"))
        or _as_text(_pick(data, _FIELD_ALIASES["cnpj"]))
    )
    garantia = _as_text(emissao_obj.get("garantia")) or _as_text(
        _pick(data, _FIELD_ALIASES["garantia"])
    )
    data_emissao = parse_br_date(
        str(
            emissao_obj.get("data_emissao")
            or _pick(data, _FIELD_ALIASES["data_emissao"])
            or ""
        )
    )

    row: dict[str, Any] = {
        "codigo_cetip": codigo_cetip,
        "tipo": tipo,
        "serie": serie,
        "remuneracao": _as_text(_pick(data, _FIELD_ALIASES["remuneracao"])),
        "data_inicio_rentabilidade": parse_br_date(
            str(_pick(data, _FIELD_ALIASES["data_inicio_rentabilidade"]) or "")
        ),
        "quantidade_serie_emissao": _as_text(
            data.get("quantidade_emitida")
            or _pick(data, _FIELD_ALIASES["quantidade_serie_emissao"])
        ),
        "volume_serie_emissao": _as_text(
            data.get("volume") or _pick(data, _FIELD_ALIASES["volume_serie_emissao"])
        ),
        "vne": _as_text(_pick(data, _FIELD_ALIASES["vne"])),
        "quantidade_mercado_b3": _as_text(
            data.get("quantidade_mercado")
            or _pick(data, _FIELD_ALIASES["quantidade_mercado_b3"])
        ),
        "data_emissao": data_emissao,
        "data_vencimento": parse_br_date(
            str(_pick(data, _FIELD_ALIASES["data_vencimento"]) or "")
        ),
        "isin": _as_text(_pick(data, _FIELD_ALIASES["isin"])),
        "lei_12431": _as_text(
            data.get("lei") if "lei" in data else _pick(data, _FIELD_ALIASES["lei_12431"])
        ),
        "emissao": emissao,
        "empresa": empresa,
        "setor": _as_text(_pick(data, _FIELD_ALIASES["setor"]))
        or _as_text(emissor_obj.get("setor")),
        "cnpj": cnpj,
        "garantia": garantia,
    }

    consumed_keys: set[str] = set()
    for aliases in _FIELD_ALIASES.values():
        consumed_keys.update(aliases)
    consumed_keys.update(
        {
            "emissor",
            "devedor",
            "securitizadora",
            "emissao",
            "quantidade_emitida",
            "quantidade_mercado",
            "volume",
            "lei",
        }
    )

    extras: dict[str, Any] = {}
    for key, value in data.items():
        if key in consumed_keys or key in _KNOWN_COLUMNS:
            continue
        if value is None or value == "" or value == {}:
            continue
        if isinstance(value, (str, int, float, bool, list, dict)):
            extras[str(key)] = value
        else:
            extras[str(key)] = str(value)
    if emissao_obj:
        extras.setdefault("emissao_raw", emissao_obj)
    row["extras"] = extras
    return row
