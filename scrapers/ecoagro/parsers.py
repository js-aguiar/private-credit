"""HTML parsing for the Ecoagro website.

The listing at /emissoes is server-rendered as a table of ``<tr class="emissao"
data-id="...">`` rows with columns: ano, operação, emissão, série, cód. CETIP, valor.
Pagination is via ``?page=N`` links.
"""

from __future__ import annotations

import re

from shared.html_utils import extract_document_links, soupify
from shared.parsing import (
    clean_text,
    extract_cetip_codes,
    parse_br_date,
    parse_brl_amount,
    parse_int,
)
from shared.records import DocumentoData, EmissaoData

FONTE = "ecoagro"
_PAGE_RE = re.compile(r"[?&]page=(\d+)")
_SERIE_SPLIT_RE = re.compile(r"[-,/;\s]+")
_SERIE_JS_BLOCK_RE = re.compile(
    r"serie\.cd_serie\s*=\s*\"(?P<numero>[^\"]*)\";\s*"
    r"serie\.isin\s*=\s*\"(?P<isin>[^\"]*)\";?\s*"
    r"serie\.cetip\s*=\s*\"(?P<cetip>[^\"]*)\";?\s*"
    r"serie\.remuneracao\s*=\s*\"(?P<remuneracao>[^\"]*)\"",
    re.DOTALL,
)


def _cell_text(cells: list, index: int) -> str | None:
    if index < len(cells):
        return clean_text(cells[index].get_text(" "))
    return None


def find_max_page(html: str) -> int:
    """Return the highest page number referenced by the pagination controls."""
    soup = soupify(html)
    max_page = 1
    for anchor in soup.select("a[href]"):
        match = _PAGE_RE.search(anchor.get("href", ""))
        if match:
            max_page = max(max_page, int(match.group(1)))
    return max_page


def parse_listing_rows(html: str, base_url: str, detail_template: str) -> list[EmissaoData]:
    soup = soupify(html)
    emissoes: list[EmissaoData] = []
    for row in soup.select("tr.emissao[data-id]"):
        data_id = clean_text(row.get("data-id"))
        if not data_id:
            continue
        cells = row.find_all("td")
        numero_raw = _cell_text(cells, 2)  # e.g. "457ª"
        numero = re.sub(r"[^0-9]", "", numero_raw or "") or None
        cetip_codes = (
            extract_cetip_codes(cells[4].get_text(" ")) if len(cells) > 4 else []
        )
        emissoes.append(
            EmissaoData(
                fonte=FONTE,
                id_origem=data_id,
                link=detail_template.format(id=data_id),
                numero_emissao=numero,
                operacao=_cell_text(cells, 1),
                ano_emissao=parse_int(_cell_text(cells, 0)),
                tipo_ativo="CRA",
                series_raw=_cell_text(cells, 3),
                codigos_cetip=" ".join(cetip_codes) or None,
                valor_total=parse_brl_amount(_cell_text(cells, 5)),
                extras={"numero_emissao_raw": numero_raw} if numero_raw else {},
            )
        )
    return emissoes


def parse_detail(html: str, base_url: str) -> tuple[dict, list[DocumentoData]]:
    """Best-effort detail extraction: documents + any obvious extra fields.

    The exact detail-page structure can vary; we defensively collect every document-like
    link and stash a few labelled fields into ``extras`` so nothing is lost.
    """
    soup = soupify(html)
    documentos: list[DocumentoData] = []
    for titulo, url in extract_document_links(soup, base_url):
        documentos.append(
            DocumentoData(
                link_documento=url,
                titulo=titulo,
                tipo_documento=_guess_doc_type(titulo, url),
                data_documento=parse_br_date(titulo) if titulo else None,
            )
        )

    updates: dict = {}
    extras = _extract_labelled_fields(soup)
    if extras:
        updates["extras"] = extras
    return updates, documentos


def parse_series_from_detail(html: str) -> list:
    """Extract per-série ISIN/CETIP/remuneration from inline JavaScript on the detail page."""
    from shared.records import SerieData

    series: list[SerieData] = []
    seen: set[tuple[str, str | None]] = set()
    for match in _SERIE_JS_BLOCK_RE.finditer(html):
        numero = clean_text(match.group("numero"))
        if not numero:
            continue
        isin = clean_text(match.group("isin")) or None
        cetip = clean_text(match.group("cetip")) or None
        remuneracao = clean_text(match.group("remuneracao")) or None
        key = (numero, cetip)
        if key in seen:
            continue
        seen.add(key)
        series.append(
            SerieData(
                numero_serie=numero,
                isin=isin,
                codigo_cetip=cetip,
                remuneracao=remuneracao,
            )
        )
    return series


def merge_series_from_detail(baseline: list, detail: list) -> list:
    """Overlay detail-page fields onto list-derived séries."""
    from shared.records import SerieData

    if not detail:
        return baseline

    by_numero = {serie.numero_serie: serie for serie in detail}
    by_cetip = {serie.codigo_cetip: serie for serie in detail if serie.codigo_cetip}

    merged: list[SerieData] = []
    for base in baseline:
        extra = by_numero.get(base.numero_serie)
        if extra is None and base.codigo_cetip:
            extra = by_cetip.get(base.codigo_cetip)
        merged.append(
            SerieData(
                numero_serie=base.numero_serie,
                numero_emissao=base.numero_emissao,
                codigo_cetip=(extra.codigo_cetip if extra else None) or base.codigo_cetip,
                isin=(extra.isin if extra else None) or base.isin,
                remuneracao=(extra.remuneracao if extra else None) or base.remuneracao,
                indexador=(extra.indexador if extra else None) or base.indexador,
                valor=base.valor,
                data_emissao=base.data_emissao,
                data_vencimento=base.data_vencimento,
                quantidade=base.quantidade,
                rating=base.rating,
                extras=base.extras,
            )
        )

    seen = {(serie.numero_serie, serie.codigo_cetip) for serie in merged}
    for extra in detail:
        key = (extra.numero_serie, extra.codigo_cetip)
        if key not in seen:
            merged.append(extra)
            seen.add(key)
    return merged


def emission_isin_from_series(series: list) -> str | None:
    """Return a single emission-level ISIN when exactly one série has one."""
    isins: list[str] = []
    for serie in series:
        if serie.isin and serie.isin not in isins:
            isins.append(serie.isin)
    return isins[0] if len(isins) == 1 else None


def _guess_doc_type(titulo: str | None, url: str) -> str | None:
    text = f"{titulo or ''} {url}".lower()
    for label, keywords in {
        "termo_securitizacao": ["termo de securit", "termo_de_securit"],
        "prospecto": ["prospecto"],
        "relatorio": ["relatorio", "relatório"],
        "boletim": ["boletim"],
        "assembleia": ["assembleia", "assembléia", "ata"],
        "demonstracao_financeira": ["demonstra", "balanco", "balanço"],
    }.items():
        if any(k in text for k in keywords):
            return label
    return None


def _extract_labelled_fields(soup) -> dict:
    """Capture simple ``label: value`` pairs from definition-list-like structures."""
    extras: dict = {}
    for dl in soup.find_all("dl"):
        terms = dl.find_all("dt")
        defs = dl.find_all("dd")
        for term, definition in zip(terms, defs):
            key = clean_text(term.get_text())
            value = clean_text(definition.get_text())
            if key and value:
                extras[key] = value
    return extras


def build_series_from_emissao(
    numero_emissao: str | None, series_raw: str | None, codigos_cetip: str | None
):
    """Derive per-série rows from the list-level série numbers + CETIP codes.

    Ecoagro shows séries as "1-2-3" and their CETIP codes side by side, so we can
    populate the ``series`` table even if the detail page is unavailable.
    """
    from shared.records import SerieData

    numeros = [n for n in _SERIE_SPLIT_RE.split(series_raw or "") if n]
    cetips = (codigos_cetip or "").split()
    series: list[SerieData] = []
    count = max(len(numeros), len(cetips))
    for index in range(count):
        numero_serie = numeros[index] if index < len(numeros) else str(index + 1)
        codigo_cetip = cetips[index] if index < len(cetips) else None
        series.append(
            SerieData(
                numero_serie=numero_serie,
                numero_emissao=numero_emissao,
                codigo_cetip=codigo_cetip,
            )
        )
    return series
