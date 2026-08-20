"""HTML parsing for the Ecoagro website.

The listing at /emissoes is server-rendered as a table of ``<tr class="emissao"
data-id="...">`` rows with columns: ano, operação, emissão, série, cód. CETIP, valor.
Pagination is via ``?page=N`` links.
"""

from __future__ import annotations

import re

from shared.html_utils import absolute_url, extract_document_links, soupify
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
_IGNORE_URL_FRAGMENTS = (
    "politica-de-privacidade",
    "politica-de-cookies",
    "/wp/wp-content/",
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
    """Extract documents and labelled extras from an emissoes-integra page."""
    soup = soupify(html)
    documentos: list[DocumentoData] = []
    seen: set[str] = set()

    for item in soup.select("div.documents__item"):
        anchor = item.select_one("a[href]")
        if anchor is None:
            continue
        url = absolute_url(base_url, anchor.get("href"))
        if not url or url in seen or _should_ignore_doc_url(url):
            continue
        title_el = item.select_one("p")
        titulo = clean_text(title_el.get_text(" ")) if title_el else None
        if not titulo or titulo.lower() == "baixar":
            titulo = clean_text(anchor.get("title")) or clean_text(anchor.get_text())
            if titulo and titulo.lower() == "baixar":
                titulo = None
        seen.add(url)
        documentos.append(
            DocumentoData(
                link_documento=url,
                titulo=titulo,
                tipo_documento=_guess_doc_type(titulo, url),
                data_documento=parse_br_date(titulo) if titulo else None,
            )
        )

    # Fallback: leftover file links inside the documents section only (not site footer).
    section = soup.select_one("section.documents") or soup.select_one(".documents")
    if section is not None:
        for titulo, url in extract_document_links(section, base_url):
            if not url or url in seen or _should_ignore_doc_url(url):
                continue
            if "/public/storage/" not in url.lower():
                continue
            seen.add(url)
            if titulo and titulo.lower() == "baixar":
                titulo = None
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


def _should_ignore_doc_url(url: str) -> bool:
    lowered = url.lower()
    return any(fragment in lowered for fragment in _IGNORE_URL_FRAGMENTS)


def _guess_doc_type(titulo: str | None, url: str) -> str | None:
    text = f"{titulo or ''} {url}".lower()
    for label, keywords in {
        "termo_securitizacao": ["termo de securit", "termo_de_securit"],
        "anuncio_inicio": ["anúncio de início", "anuncio de inicio", "anúncio de inicio"],
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
