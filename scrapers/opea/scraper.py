"""Opea scraper (https://app.opea.com.br/pt/emissoes).

Opea is a Vue/Vite SPA backed by a BFF REST API that is publicly accessible without
authentication. All data is fetched with plain httpx calls — no headless browser needed.

API base: https://app.opea.com.br/bff/v1/api/
  List:   GET emissao/passivosoperacoes?pagina={page}&tamanhoPagina={size}
          → {"content": {"emissoes": {"lastPage": N, "totalCount": N, "items": [...]}}}
          Each list item is one **série**. Items that share a parent ``codigoOpea``
          (everything except the trailing série segment) belong to the same emission
          grouping (stored as string ``emissao_id`` on each série).
  Detail: GET emissao/passivosoperacoes/detalhe?codigoOpea={serieCodigo}
          → {"content": {..., "idCedoc": "<guid>", ...}}
  Files:  GET cedoc/files?idCedoc={idCedoc}
          → {"children": [{name, url, categoryName, createdOn, ...}]}
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from shared.mapping import pick
from shared.opea_documents import normalize_opea_document_url, opea_file_id
from shared.parsing import parse_br_date
from shared.records import DetailResult, DocumentoData, SerieData
from shared.scraper_base import BaseScraper

_BFF_BASE = "https://app.opea.com.br/bff/v1/api/"
FONTE = "opea"


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


def vehicle_from_parent(parent: str) -> str | None:
    """Third segment of the parent code (``CIA``, ``TRU``, ``PLS``, …)."""
    parts = (parent or "").split(".")
    if len(parts) >= 3:
        part = parts[2].strip().upper()
        return part or None
    return None


def emission_file_code(numero_emissao: str | int | None) -> str | None:
    """Zero-padded emission code used in cedoc filenames, e.g. 228 → ``E0228``."""
    if numero_emissao is None:
        return None
    try:
        return f"E{int(str(numero_emissao).strip()):04d}"
    except (ValueError, TypeError):
        return None


def _filename_has_natureza(name: str, nature: str) -> bool:
    """True when the uppercased filename mentions natureza in a structured way."""
    if f"OP_{nature}_" in name:
        return True
    if f"_{nature}_" in name:
        return True
    if name.startswith(f"{nature}_") or name.startswith(f"{nature} "):
        return True
    return False


def _leading_book_token(filename: str, nature: str) -> str | None:
    """Leading book/vehicle token when present and not OP / natureza."""
    name = (filename or "").upper()
    if "_" not in name:
        return None
    token = name.split("_", 1)[0].strip()
    if not token or token == "OP" or token == nature:
        return None
    return token


def document_matches_emission(
    filename: str,
    natureza: str | None,
    emission_code: str | None,
    vehicle: str | None = None,
) -> bool:
    """Keep files for this parent emission's natureza, E0NNN code, and vehicle book."""
    if not emission_code:
        return False
    name = (filename or "").upper()
    ecode = emission_code.upper()
    if ecode not in name:
        return False
    if not natureza:
        return True
    nature = natureza.upper()
    if not _filename_has_natureza(name, nature):
        return False

    veh = (vehicle or "").strip().upper() or None
    book = _leading_book_token(name, nature)
    if book:
        return bool(veh) and book == veh
    return veh == "CIA"


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


def serie_from_list_item(record: dict) -> SerieData | None:
    """Map one list-level série row. Requires ISIN and/or CETIP (codigoIf)."""
    codigo = (record.get("codigoOpea") or "").strip()
    if not codigo:
        return None
    isin = (record.get("isin") or "").strip() or None
    cetip = (record.get("codigoIf") or "").strip() or None
    if not (isin or cetip):
        return None

    parent = parent_codigo_opea(codigo)
    numero = record.get("emissao")
    return SerieData(
        fonte=FONTE,
        id_origem=codigo,
        emissao_id=parent,
        numero_serie=normalize_serie_number(record.get("serie")),
        isin=isin,
        codigo_cetip=cetip,
        numero_emissao=str(numero) if numero is not None else None,
        operacao=pick(record, "nomeDevedor", "apelidoOperacao"),
        devedor=pick(record, "nomeDevedor"),
        tipo_ativo=pick(record, "naturezaOperacao", "classe"),
        indexador=pick(record, "indexador"),
        data_vencimento=parse_br_date(str(record.get("dataVencimento") or "")[:10]),
        rating=pick(record, "rating"),
        extras={
            "codigo_opea": codigo,
            "natureza": natureza_from_parent(parent),
            "vehicle": vehicle_from_parent(parent),
        },
    )


def serie_from_detail(
    codigo_opea: str, detail: dict, numero_emissao: str | None
) -> SerieData | None:
    """Map one detail payload into a SerieData row."""
    if not detail:
        return None
    isin = (detail.get("codigoIsin") or "").strip() or None
    cetip = (detail.get("codigoCetipBbb") or "").strip() or None
    if not (isin or cetip):
        return None

    parent = parent_codigo_opea(codigo_opea)
    pagamento = detail.get("pagamentoPassivo") or {}
    extras = {
        "codigo_opea": codigo_opea,
        "natureza": natureza_from_parent(parent),
        "vehicle": vehicle_from_parent(parent),
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
        "idCedoc": detail.get("idCedoc"),
    }
    extras = {key: value for key, value in extras.items() if value not in (None, "")}

    return SerieData(
        fonte=FONTE,
        id_origem=codigo_opea,
        emissao_id=parent,
        numero_serie=normalize_serie_number(detail.get("serie")),
        isin=isin,
        codigo_cetip=cetip,
        numero_emissao=numero_emissao
        if numero_emissao is not None
        else (str(detail.get("emissao")) if detail.get("emissao") is not None else None),
        operacao=pick(detail, "apelidoOperacao")
        or pick(detail.get("emissor") or {}, "descricao"),
        valor=parse_volume(detail),
        remuneracao=parse_remuneracao(detail.get("remuneracao")),
        data_emissao=parse_br_date(str(detail.get("dataEmissaoSerie") or "")[:10]),
        data_vencimento=parse_br_date(str(detail.get("dataVencimentoSerie") or "")[:10]),
        quantidade=_safe_int(detail.get("quantidadeEmitida")),
        extras=extras,
    )


class OpeaScraper(BaseScraper):
    source_name = "opea"

    _LIST_URL = f"{_BFF_BASE}emissao/passivosoperacoes"
    _DETAIL_URL = f"{_BFF_BASE}emissao/passivosoperacoes/detalhe"
    _FILES_URL = f"{_BFF_BASE}cedoc/files"
    _PAGE_SIZE = 50

    def __init__(self, config, context=None):
        super().__init__(config, context=context)
        # Cedoc/files is institution-wide (~18MB). Cache once per process so a
        # full backfill does not re-download it for every série.
        self._cedoc_children_cache: list[dict] | None = None
        self._cedoc_cache_id: str | None = None

    def list_series(self):
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

        for parent, members in grouped.items():
            codes = [
                str(row.get("codigoOpea")).strip()
                for row in members
                if (row.get("codigoOpea") or "").strip()
            ]
            for item in members:
                mapped = serie_from_list_item(item)
                if mapped is None:
                    continue
                mapped.extras = {
                    **(mapped.extras or {}),
                    "series_codigos": codes,
                    "natureza": natureza_from_parent(parent),
                }
                yield mapped

    def fetch_detail(self, serie) -> DetailResult:
        codigo = serie.id_origem
        detail = self._fetch_serie_detail(codigo)
        if not detail:
            return DetailResult(
                serie_updates={"extras": {"detalhe_acessivel": False}},
                series=[],
                documentos=[],
            )

        mapped = serie_from_detail(codigo, detail, serie.numero_emissao)
        series: list[SerieData] = [mapped] if mapped is not None else []
        parent = serie.emissao_id or parent_codigo_opea(codigo)
        updates = self._serie_updates(serie, detail, mapped)
        # Docs attach to all séries sharing emissao_id (resolved in scraper_base).
        documentos = self._fetch_documents(serie, detail, parent)

        return DetailResult(
            serie_updates=updates,
            series=series,
            documentos=documentos,
        )

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

    def _serie_updates(self, serie, detail: dict, mapped: SerieData | None) -> dict:
        updates: dict = {
            "extras": {
                "detalhe_acessivel": bool(detail),
                "natureza": (serie.extras or {}).get("natureza")
                if isinstance(serie.extras, dict)
                else natureza_from_parent(serie.emissao_id or serie.id_origem),
                "idCedoc": detail.get("idCedoc"),
                "permissao_divulgacao": pick(
                    detail.get("permissaoDivulgacaoPortal") or {}, "raw", "value"
                ),
                "oferta": pick(detail.get("tipoOferta") or {}, "raw", "value"),
                "emissor": pick(detail.get("emissor") or {}, "descricao"),
                "apelido_operacao": detail.get("apelidoOperacao"),
            }
        }
        if mapped:
            updates.update(
                {
                    "isin": mapped.isin,
                    "codigo_cetip": mapped.codigo_cetip,
                    "numero_serie": mapped.numero_serie,
                    "numero_emissao": mapped.numero_emissao or serie.numero_emissao,
                    "valor": mapped.valor,
                    "remuneracao": mapped.remuneracao,
                    "data_emissao": mapped.data_emissao,
                    "data_vencimento": mapped.data_vencimento,
                    "quantidade": mapped.quantidade,
                    "operacao": mapped.operacao or serie.operacao,
                }
            )
        updates["extras"] = {
            key: value
            for key, value in updates["extras"].items()
            if value not in (None, "", [])
        }
        return updates

    def _get_cedoc_children(self, id_cedoc: str) -> list[dict]:
        """Return institution-wide cedoc children, fetching at most once per run."""
        if self._cedoc_children_cache is not None:
            self.logger.info(
                "opea_cedoc_cache_hit",
                extra={
                    "id_cedoc": id_cedoc,
                    "cached_id_cedoc": self._cedoc_cache_id,
                    "children": len(self._cedoc_children_cache),
                },
            )
            return self._cedoc_children_cache

        try:
            resp = self.client.get_json(self._FILES_URL, params={"idCedoc": id_cedoc})
        except Exception as exc:
            self.logger.warning(
                "opea_cedoc_error",
                extra={"id_cedoc": id_cedoc, "error": str(exc)},
            )
            return []

        children: list[dict] = (resp or {}).get("children") or []
        self._cedoc_children_cache = children
        self._cedoc_cache_id = str(id_cedoc)
        self.logger.info(
            "opea_cedoc_cache_miss",
            extra={"id_cedoc": id_cedoc, "children": len(children)},
        )
        return children

    def _fetch_documents(
        self,
        serie,
        detail: dict,
        parent: str,
    ) -> list[DocumentoData]:
        """Filter cached cedoc children; attach to all séries sharing ``emissao_id``."""
        id_cedoc = detail.get("idCedoc")
        if not id_cedoc:
            return []
        emission_code = emission_file_code(serie.numero_emissao)
        natureza = None
        if isinstance(serie.extras, dict):
            natureza = serie.extras.get("natureza")
        natureza = natureza or natureza_from_parent(parent)
        vehicle = vehicle_from_parent(parent)
        if not emission_code:
            return []

        children = self._get_cedoc_children(str(id_cedoc))
        docs: list[DocumentoData] = []
        seen_ids: set[str] = set()
        for child in children:
            name = child.get("name") or ""
            if not document_matches_emission(name, natureza, emission_code, vehicle):
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
                    numero_emissao=serie.numero_emissao,
                    codigo_cetip=serie.codigo_cetip,
                    emissao_id=parent,
                    id_origem_arquivo=file_id,
                    extras={**child, "idCedoc": id_cedoc},
                )
            )
        return docs
