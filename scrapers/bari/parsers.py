"""Parsing helpers for the Bari website (Strapi API + Next.js SSG detail pages)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from shared.parsing import parse_br_date, parse_int
from shared.records import DocumentoData, EmissaoData, SerieData

STRAPI_EMISSIONS_URL = "https://strapicms.bancobari.com.br/api/emissions"
PORTAL_BASE = "https://barisec.com.br"
DETAIL_URL_TEMPLATE = f"{PORTAL_BASE}/emissoes/{{code}}"
STRAPI_PAGE_SIZE = 100

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def parse_bari_date(value: str | None) -> date | None:
    """Parse ISO, BR, or US (MM/DD/YYYY) date strings from Bari payloads."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    parsed = parse_br_date(text)
    if parsed is not None:
        return parsed
    token = text.split(" ")[0].split("T")[0]
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


def fetch_strapi_emissions(client) -> list[dict]:
    """Paginate the public Strapi emissions collection."""
    rows: list[dict] = []
    page = 1
    while True:
        payload = client.get_json(
            STRAPI_EMISSIONS_URL,
            params={
                "pagination[page]": page,
                "pagination[pageSize]": STRAPI_PAGE_SIZE,
            },
        )
        if not isinstance(payload, dict):
            break
        batch = payload.get("data") or []
        if not isinstance(batch, list):
            break
        rows.extend(item for item in batch if isinstance(item, dict))

        pagination = (payload.get("meta") or {}).get("pagination") or {}
        page_count = int(pagination.get("pageCount") or page)
        if page >= page_count or not batch:
            break
        page += 1
    return rows


def group_by_emission_number(rows: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        emission_number = row.get("emissionNumber")
        if emission_number is None:
            continue
        grouped[int(emission_number)].append(row)
    for emission_number in grouped:
        grouped[emission_number].sort(key=lambda item: int(item.get("serieNumber") or 0))
    return dict(grouped)


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _normalize_isin(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    return text


def map_list_item(row: dict, *, fonte: str) -> EmissaoData | None:
    """Map one Strapi series row to an emission record (id_origem = IF B3 code)."""
    code = str(row.get("code") or "").strip()
    if not code:
        return None

    emission_number = row.get("emissionNumber")
    isin = _normalize_isin(row.get("ISIN"))

    return EmissaoData(
        fonte=fonte,
        id_origem=code,
        link=DETAIL_URL_TEMPLATE.format(code=code),
        isin=isin,
        numero_emissao=str(emission_number) if emission_number is not None else None,
        codigos_cetip=code,
        operacao=f"CRI {emission_number}ª" if emission_number is not None else None,
        devedor=(row.get("ballastNature") or "").strip() or None,
        tipo_ativo="CRI",
        series_raw=str(row.get("serieNumber")) if row.get("serieNumber") is not None else None,
        valor_total=_parse_decimal(row.get("emissionValue")),
        indexador=(row.get("remuneration") or "").strip() or None,
        data_emissao=parse_bari_date(row.get("emissionDate")),
        data_vencimento=parse_bari_date(row.get("dueDate")),
        extras={
            "series_row": row,
            "emissionStatus": row.get("emissionStatus"),
            "ballastNature": row.get("ballastNature"),
            "trustee": row.get("trustee"),
            "custodian": row.get("custodian"),
            "tradingEnvironment": row.get("tradingEnvironment"),
            "strapi_emission_number": emission_number,
            "serie_number": row.get("serieNumber"),
        },
    )


def map_grouped_emission(
    emission_number: int,
    series_rows: list[dict],
    *,
    fonte: str,
) -> EmissaoData | None:
    if not series_rows:
        return None

    primary = series_rows[0]
    primary_code = str(primary.get("code") or "").strip()
    if not primary_code:
        return None

    codes = [str(row.get("code")).strip() for row in series_rows if row.get("code")]
    series_numbers = [
        str(row.get("serieNumber"))
        for row in series_rows
        if row.get("serieNumber") is not None
    ]
    isins = [_normalize_isin(row.get("ISIN")) for row in series_rows]
    isins = [isin for isin in isins if isin]

    return EmissaoData(
        fonte=fonte,
        id_origem=str(emission_number),
        link=DETAIL_URL_TEMPLATE.format(code=primary_code),
        isin=isins[0] if isins else None,
        numero_emissao=str(emission_number),
        codigos_cetip=" ".join(dict.fromkeys(codes)) or None,
        operacao=f"CRI {emission_number}ª",
        devedor=(primary.get("ballastNature") or "").strip() or None,
        tipo_ativo="CRI",
        series_raw="-".join(series_numbers) if series_numbers else None,
        valor_total=_parse_decimal(primary.get("emissionValue")),
        indexador=(primary.get("remuneration") or "").strip() or None,
        data_emissao=parse_bari_date(primary.get("emissionDate")),
        data_vencimento=parse_bari_date(primary.get("dueDate")),
        extras={
            "primary_code": primary_code,
            "series_rows": series_rows,
            "emissionStatus": primary.get("emissionStatus"),
            "ballastNature": primary.get("ballastNature"),
            "trustee": primary.get("trustee"),
            "custodian": primary.get("custodian"),
            "tradingEnvironment": primary.get("tradingEnvironment"),
        },
    )


def extract_next_data(html: str) -> dict:
    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise ValueError("__NEXT_DATA__ not found in HTML")
    return json.loads(match.group(1))


def extract_page_props(html: str) -> dict:
    data = extract_next_data(html)
    page_props = (data.get("props") or {}).get("pageProps") or {}
    if not isinstance(page_props, dict):
        return {}
    return page_props


def map_series_from_rows(series_rows: list[dict], emissao) -> list[SerieData]:
    series: list[SerieData] = []
    seen: set[str] = set()
    for row in series_rows:
        if not isinstance(row, dict):
            continue
        numero = row.get("serieNumber")
        if numero is None:
            continue
        numero_serie = str(numero)
        if numero_serie in seen:
            continue
        seen.add(numero_serie)
        code = (row.get("code") or "").strip() or None
        series.append(
            SerieData(
                numero_serie=numero_serie,
                isin=_normalize_isin(row.get("ISIN")),
                numero_emissao=emissao.numero_emissao,
                codigo_cetip=code,
                valor=_parse_decimal(row.get("emissionValue")),
                remuneracao=(row.get("remuneration") or "").strip() or None,
                indexador=(row.get("remuneration") or "").strip() or None,
                data_emissao=parse_bari_date(row.get("emissionDate")),
                data_vencimento=parse_bari_date(row.get("dueDate")),
                quantidade=parse_int(str(row.get("amount") or "").replace(".", "")),
                extras={
                    "code": code,
                    "emissionStatus": row.get("emissionStatus"),
                    "amount": row.get("amount"),
                    "strapi_id": row.get("id"),
                },
            )
        )
    return series


def _document_url(item: dict) -> str | None:
    file_obj = item.get("file")
    if isinstance(file_obj, dict):
        url = (file_obj.get("url") or "").strip()
        if url:
            return url
    return None


def map_documents(page_props: dict, emissao) -> list[DocumentoData]:
    docs: list[DocumentoData] = []
    seen: set[str] = set()

    for key in ("emissionCrisDocuments", "emissionFinancialStatements"):
        items = page_props.get(key) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            url = _document_url(item)
            if not url or url in seen:
                continue
            seen.add(url)
            doc_type = item.get("documentType") or item.get("reportType")
            docs.append(
                DocumentoData(
                    link_documento=url,
                    titulo=item.get("documentName"),
                    tipo_documento=doc_type,
                    data_documento=parse_bari_date(item.get("date")),
                    numero_emissao=emissao.numero_emissao,
                    codigo_cetip=(emissao.codigos_cetip or "").split()[0]
                    if emissao.codigos_cetip
                    else None,
                    extras={**item, "source_array": key},
                )
            )
    return docs
