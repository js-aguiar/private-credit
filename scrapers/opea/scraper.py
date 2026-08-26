"""Opea scraper (https://app.opea.com.br/pt/emissoes).

Opea is a Vue/Vite SPA backed by a BFF REST API that is publicly accessible without
authentication. All data is fetched with plain httpx calls — no headless browser needed.

API base: https://app.opea.com.br/bff/v1/api/
  List:   GET emissao/passivosoperacoes?pagina={page}&tamanhoPagina={size}
          → {"content": {"emissoes": {"lastPage": N, "totalCount": N, "items": [...]}}}
          Each list item is one **série**. Items that share a parent ``codigoOpea``
          (everything except the trailing série segment) belong to the same emission.
  Detail: GET emissao/passivosoperacoes/detalhe?codigoOpea={serieCodigo}
          → {"content": {..., "idCedoc": "<guid>", ...}}
  Files:  GET cedoc/files?idCedoc={idCedoc}
          → {"children": [{name, url, categoryName, createdOn, ...}]}
          Institution-wide list; filter by natureza + emission code in the filename
          (e.g. CRA + E0228 for parent ``CRA.228.CIA``).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from shared.mapping import pick
from shared.opea_documents import normalize_opea_document_url, opea_file_id
from shared.parsing import parse_br_date
from shared.records import DetailResult, DocumentoData, EmissaoData, SerieData
from shared.scraper_base import BaseScraper

_BFF_BASE = "https://app.opea.com.br/bff/v1/api/"


def parent_codigo_opea(codigo_opea: str) -> str:
    """Strip the trailing série segment: ``CRI.624.CIA.1`` → ``CRI.624.CIA``."""
    parts = (codigo_opea or "").strip().split(".")
    if len(parts) >= 2:
        return ".".join(parts[:-1])
    return (codigo_opea or "").strip()


def natureza_from_parent(parent: str) -> str | None:
    """First segment of the parent code (``CRA``, ``CRI``, ``DEB``, …)."""
    part = (parent or "").split(".")[0].strip().upper()
    return part or None


def emission_file_code(numero_emissao: str | int | None) -> str | None:
    """Zero-padded emission code used in cedoc filenames, e.g. 228 → ``E0228``."""
    if numero_emissao is None:
        return None
    try:
        return f"E{int(str(numero_emissao).strip()):04d}"
    except (ValueError, TypeError):
        return None


def document_matches_emission(filename: str, natureza: str | None, emission_code: str | None) -> bool:
    """Keep files that mention both the asset nature and the emission code."""
    if not emission_code:
        return False
    name = (filename or "").upper()
    if emission_code.upper() not in name:
        return False
    if not natureza:
        return True
    nature = natureza.upper()
    # Prefer explicit OP_{NATURE}_E0NNN markers; also accept _{NATURE}_ near the code.
    if f"OP_{nature}_" in name:
        return True
    if f"_{nature}_" in name and emission_code.upper() in name:
        return True
    # Some older names start with the nature token.
    if name.startswith(f"{nature}_") or name.startswith(f"{nature} "):
        return True
    return False


def normalize_serie_number(value: Any) -> str:
    if value is None or value == "":
        return "1"
    try:
        as_float = float(str(value).strip())
        if as_float.is_integer():
            return str(int(as_float))
    except (ValueError, TypeError):
        pass
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text or "1"


def parse_remuneracao(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, dict):
        return pick(value, "descricao", "value", "raw")
    return str(value).strip() or None


def parse_volume(detail: dict) -> Decimal | None:
    raw = detail.get("volumeEmitido")
    if raw is None:
        raw = detail.get("valorGlobalSerie")
    if raw is not None:
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError):
            pass
    qty = detail.get("quantidadeEmitida")
    preco = detail.get("precoUnitario")
    if qty is None or preco is None:
        return None
    try:
        return Decimal(str(qty)) * Decimal(str(preco))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return None


def _enum_value(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj.strip() or None
    if isinstance(obj, dict):
        return pick(obj, "value", "raw", "descricao", "nomeSimplificado")
    return str(obj).strip() or None


def serie_from_detail(codigo_opea: str, detail: dict, numero_emissao: str | None) -> SerieData | None:
    """Map one detail payload into a SerieData row."""
    if not detail:
        return None
    isin = (detail.get("codigoIsin") or "").strip() or None
    cetip = (detail.get("codigoCetipBbb") or "").strip() or None
    if not (isin or cetip):
        return None

    pagamento = detail.get("pagamentoPassivo") or {}
    extras = {
        "codigo_opea": codigo_opea,
        "classe": _enum_value(detail.get("classeOperacao")),
        "concentracao": _enum_value(detail.get("concentracao")),
        "periodicidade_juros": _enum_value(pagamento.get("periodicidadeFrequenciaJuros")),
        "periodicidade_amortizacao": _enum_value(
            pagamento.get("periodicidadeFrequenciaAmortizacao")
        ),
        "quantidade_integralizada": _safe_int(detail.get("quantidadeIntegralizada")),
        "agente_fiduciario": _enum_value(detail.get("agenteFiduciario")),
        "segmento": pick(detail, "descricaoSegmentoOperacao")
        or _enum_value(detail.get("descricaoSegmentoOperacao")),
        "precoUnitario": detail.get("precoUnitario"),
    }
    # Drop empty extras keys.
    extras = {key: value for key, value in extras.items() if value not in (None, "")}

    return SerieData(
        numero_serie=normalize_serie_number(detail.get("serie")),
        isin=isin,
        numero_emissao=numero_emissao
        if numero_emissao is not None
        else (str(detail.get("emissao")) if detail.get("emissao") is not None else None),
        codigo_cetip=cetip,
        valor=parse_volume(detail),
        remuneracao=parse_remuneracao(detail.get("remuneracao")),
        indexador=None,
        data_emissao=parse_br_date(str(detail.get("dataEmissaoSerie") or "")[:10]),
        data_vencimento=parse_br_date(str(detail.get("dataVencimentoSerie") or "")[:10]),
        quantidade=_safe_int(detail.get("quantidadeEmitida")),
        extras=extras,
    )


def group_list_items(items: list[dict]) -> list[EmissaoData]:
    """Collapse série-level list rows into one EmissaoData per parent codigoOpea."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in items:
        codigo = (record.get("codigoOpea") or "").strip()
        if not codigo:
            continue
        groups[parent_codigo_opea(codigo)].append(record)

    emissoes: list[EmissaoData] = []
    for parent, members in sorted(groups.items()):
        members_sorted = sorted(
            members,
            key=lambda row: (
                float(row.get("serie") or 0),
                str(row.get("codigoOpea") or ""),
            ),
        )
        first = members_sorted[0]
        numero = first.get("emissao")
        cetips: list[str] = []
        for row in members_sorted:
            cetip = (row.get("codigoIf") or "").strip()
            if cetip and cetip not in cetips:
                cetips.append(cetip)
        series_codigos = [str(row.get("codigoOpea")).strip() for row in members_sorted]
        series_nums = [
            normalize_serie_number(row.get("serie")) for row in members_sorted if row.get("serie") is not None
        ]
        isins = [
            (row.get("isin") or "").strip()
            for row in members_sorted
            if (row.get("isin") or "").strip()
        ]
        unique_isins = list(dict.fromkeys(isins))
        first_codigo = series_codigos[0]
        emissoes.append(
            EmissaoData(
                fonte="opea",
                id_origem=parent,
                link=f"https://app.opea.com.br/pt/emissoes/{first_codigo}",
                isin=unique_isins[0] if len(unique_isins) == 1 else None,
                numero_emissao=str(numero) if numero is not None else None,
                codigos_cetip=" ".join(cetips) or None,
                operacao=pick(first, "nomeDevedor", "apelidoOperacao"),
                devedor=pick(first, "nomeDevedor"),
                tipo_ativo=pick(first, "naturezaOperacao", "classe"),
                series_raw="-".join(series_nums) if series_nums else None,
                indexador=pick(first, "indexador"),
                data_vencimento=parse_br_date(str(first.get("dataVencimento") or "")[:10]),
                rating=pick(first, "rating"),
                extras={
                    "series_codigos": series_codigos,
                    "natureza": natureza_from_parent(parent),
                    "list_count": len(members_sorted),
                },
            )
        )
    return emissoes


class OpeaScraper(BaseScraper):
    source_name = "opea"

    _LIST_URL = f"{_BFF_BASE}emissao/passivosoperacoes"
    _DETAIL_URL = f"{_BFF_BASE}emissao/passivosoperacoes/detalhe"
    _FILES_URL = f"{_BFF_BASE}cedoc/files"
    _PAGE_SIZE = 50

    def list_emissoes(self):
        grouped: dict[str, list[dict]] = defaultdict(list)
        page = 1
        while True:
            try:
                payload = self.client.get_json(
                    self._LIST_URL,
                    params={"pagina": page, "tamanhoPagina": self._PAGE_SIZE},
                )
            except Exception as exc:
                self.logger.warning("opea_list_error", extra={"page": page, "error": str(exc)})
                break

            emissoes_block = (payload or {}).get("content", {}).get("emissoes") or {}
            items = emissoes_block.get("items") or []
            last_page = int(emissoes_block.get("lastPage") or 1)

            for item in items:
                codigo = (item.get("codigoOpea") or "").strip()
                if not codigo:
                    continue
                grouped[parent_codigo_opea(codigo)].append(item)

            self.logger.info(
                "opea_list_page",
                extra={"page": page, "last_page": last_page, "items": len(items)},
            )
            if page >= last_page or not items:
                break
            page += 1

        flat = [row for members in grouped.values() for row in members]
        yield from group_list_items(flat)

    def fetch_detail(self, emissao) -> DetailResult:
        series_codigos = self._series_codigos_for(emissao)
        if not series_codigos:
            return DetailResult(
                emissao_updates={"extras": {"detalhe_acessivel": False}},
                series=[],
                documentos=[],
            )

        series: list[SerieData] = []
        details: list[dict] = []
        for codigo in series_codigos:
            detail = self._fetch_serie_detail(codigo)
            if not detail:
                continue
            details.append(detail)
            mapped = serie_from_detail(codigo, detail, emissao.numero_emissao)
            if mapped is not None:
                series.append(mapped)

        first_detail = details[0] if details else {}
        updates = self._emission_updates(emissao, series, first_detail)
        documentos = self._fetch_documents(emissao, first_detail)

        return DetailResult(
            emissao_updates=updates,
            series=series,
            documentos=documentos,
        )

    def _series_codigos_for(self, emissao) -> list[str]:
        extras = emissao.extras or {}
        codes = extras.get("series_codigos") if isinstance(extras, dict) else None
        if isinstance(codes, list) and codes:
            return [str(code).strip() for code in codes if str(code).strip()]
        # Fallback for legacy one-série id_origem rows.
        if emissao.id_origem and emissao.id_origem.count(".") >= 3:
            return [emissao.id_origem]
        return []

    def _fetch_serie_detail(self, codigo_opea: str) -> dict:
        try:
            resp = self.client.get_json(self._DETAIL_URL, params={"codigoOpea": codigo_opea})
            return (resp or {}).get("content") or {}
        except Exception as exc:
            self.logger.warning(
                "opea_detail_error",
                extra={"codigo_opea": codigo_opea, "error": str(exc)},
            )
            return {}

    def _emission_updates(
        self, emissao, series: list[SerieData], first_detail: dict
    ) -> dict:
        updates: dict = {
            "extras": {
                "detalhe_acessivel": bool(series),
                "natureza": (emissao.extras or {}).get("natureza")
                if isinstance(emissao.extras, dict)
                else natureza_from_parent(emissao.id_origem),
                "series_codigos": [
                    (serie.extras or {}).get("codigo_opea")
                    for serie in series
                    if (serie.extras or {}).get("codigo_opea")
                ],
            }
        }
        if not series and not first_detail:
            return updates

        cetips = [serie.codigo_cetip for serie in series if serie.codigo_cetip]
        isins = [serie.isin for serie in series if serie.isin]
        unique_isins = list(dict.fromkeys(isins))
        issue_dates = [serie.data_emissao for serie in series if serie.data_emissao]
        maturities = [serie.data_vencimento for serie in series if serie.data_vencimento]

        updates.update(
            {
                "isin": unique_isins[0] if len(unique_isins) == 1 else None,
                "codigos_cetip": " ".join(dict.fromkeys(cetips)) or emissao.codigos_cetip,
                "data_emissao": min(issue_dates) if issue_dates else None,
                "data_vencimento": max(maturities) if maturities else None,
            }
        )
        if first_detail:
            updates["extras"].update(
                {
                    "permissao_divulgacao": pick(
                        first_detail.get("permissaoDivulgacaoPortal") or {}, "raw", "value"
                    ),
                    "oferta": pick(first_detail.get("tipoOferta") or {}, "raw", "value"),
                    "emissor": pick(first_detail.get("emissor") or {}, "descricao"),
                    "idCedoc": first_detail.get("idCedoc"),
                    "apelido_operacao": first_detail.get("apelidoOperacao"),
                }
            )
        # Drop empty extras.
        updates["extras"] = {
            key: value
            for key, value in updates["extras"].items()
            if value not in (None, "", [])
        }
        return updates

    def _fetch_documents(self, emissao, detail: dict) -> list[DocumentoData]:
        """Fetch documents once per emission; filter by natureza + E0NNN."""
        id_cedoc = detail.get("idCedoc")
        if not id_cedoc:
            return []
        emission_code = emission_file_code(emissao.numero_emissao)
        natureza = None
        if isinstance(emissao.extras, dict):
            natureza = emissao.extras.get("natureza")
        natureza = natureza or natureza_from_parent(emissao.id_origem)
        if not emission_code:
            return []

        try:
            resp = self.client.get_json(self._FILES_URL, params={"idCedoc": id_cedoc})
        except Exception as exc:
            self.logger.warning(
                "opea_cedoc_error",
                extra={"id_origem": emissao.id_origem, "error": str(exc)},
            )
            return []

        children: list[dict] = (resp or {}).get("children") or []
        docs: list[DocumentoData] = []
        seen_ids: set[str] = set()
        for child in children:
            name = child.get("name") or ""
            if not document_matches_emission(name, natureza, emission_code):
                continue
            url = child.get("url")
            if not url:
                continue
            file_id = opea_file_id(child)
            if file_id and file_id in seen_ids:
                continue
            if file_id:
                seen_ids.add(file_id)
            docs.append(
                DocumentoData(
                    link_documento=normalize_opea_document_url(url),
                    titulo=name,
                    tipo_documento=child.get("categoryName"),
                    data_documento=parse_br_date(str(child.get("createdOn") or "")[:10]),
                    numero_emissao=emissao.numero_emissao,
                    codigo_cetip=(emissao.codigos_cetip or "").split()[0]
                    if emissao.codigos_cetip
                    else None,
                    id_origem_arquivo=file_id,
                    extras=child,
                )
            )
        return docs
