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

Orchestration (``run`` / ``_process_details``):
  Phase A — list discovery (portal ``link`` set per série).
  Phase B — filter/upsert cedoc documents **once per** ``emissao_id`` among the pending
            detail queue (cedoc download cached process-wide).
  Phase C — one detalhe GET per série for field updates only (``documentos=[]``).
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from shared.db import session_scope
from shared.mapping import pick
from shared.opea_documents import normalize_opea_document_url, opea_file_id
from shared.parsing import parse_br_date
from shared.records import DetailResult, DocumentoData, SerieData
from shared.repository import (
    link_documento_series,
    resolve_serie_ids_by_emissao_id,
    select_series_para_detalhe,
    upsert_documento,
)
from shared.scraper_base import BaseScraper

_BFF_BASE = "https://app.opea.com.br/bff/v1/api/"
_PORTAL_BASE = "https://app.opea.com.br/pt/emissoes"
FONTE = "opea"

# EC2 full-backfill throttle for Opea only (~2790 detalhe GETs in ~2h).
# Shared backfill SSM stays at 20 rpm for other fontes; Lambdas keep 8s / 6 rpm.
_EC2_BACKFILL_DELAY_SECONDS = 1.5
_EC2_BACKFILL_JITTER_SECONDS = 0.75
_EC2_BACKFILL_MAX_RPM = 40.0


def portal_link(codigo_opea: str) -> str:
    """Public SPA URL for one série (``codigoOpea`` / ``id_origem``)."""
    return f"{_PORTAL_BASE}/{(codigo_opea or '').strip()}"


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


def _slim_cedoc_extras(child: dict, id_cedoc: str) -> dict:
    """Stable cedoc identity fields only — drop bulky/presigned URL blobs."""
    extras = {
        "id": child.get("id"),
        "name": child.get("name"),
        "categoryName": child.get("categoryName"),
        "createdOn": child.get("createdOn"),
        "idCedoc": id_cedoc,
    }
    return {key: value for key, value in extras.items() if value not in (None, "")}


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
        link=portal_link(codigo),
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
        link=portal_link(codigo_opea),
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
        if os.getenv("EXECUTION_MODE", "").strip().lower() == "ec2_backfill":
            # Source-specific override after shared backfill SSM (2s / 20 rpm).
            config.request_delay_seconds = _EC2_BACKFILL_DELAY_SECONDS
            config.request_jitter_seconds = _EC2_BACKFILL_JITTER_SECONDS
            config.max_requests_per_minute = _EC2_BACKFILL_MAX_RPM
        super().__init__(config, context=context)
        # Cedoc/files is institution-wide (~18MB). Cache once per process so a
        # full backfill does not re-download it for every série / parent.
        self._cedoc_children_cache: list[dict] | None = None
        self._cedoc_cache_id: str | None = None
        self._docs_done_for_emissao: set[str] = set()
        if os.getenv("EXECUTION_MODE", "").strip().lower() == "ec2_backfill":
            self.logger.info(
                "opea_ec2_backfill_throttle",
                extra={
                    "request_delay_seconds": config.request_delay_seconds,
                    "request_jitter_seconds": config.request_jitter_seconds,
                    "max_requests_per_minute": config.max_requests_per_minute,
                },
            )

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
        """Phase C: série field updates only — documents are handled in Phase B."""
        codigo = serie.id_origem
        detail = self._fetch_serie_detail(codigo)
        if not detail:
            return DetailResult(
                serie_updates={
                    "link": portal_link(codigo),
                    "extras": {"detalhe_acessivel": False},
                },
                series=[],
                documentos=[],
            )

        mapped = serie_from_detail(codigo, detail, serie.numero_emissao)
        series: list[SerieData] = [mapped] if mapped is not None else []
        updates = self._serie_updates(serie, detail, mapped)
        return DetailResult(
            serie_updates=updates,
            series=series,
            documentos=[],
        )

    def _process_details(self, summary: dict) -> None:
        """Phase B (docs once per parent) then Phase C (detalhe fields per série)."""
        with session_scope(self.config) as session:
            include_recheck = (
                os.getenv("EXECUTION_MODE", "").strip().lower() != "ec2_backfill"
            )
            pending = select_series_para_detalhe(
                session,
                self.source_name,
                limit=self.config.detail_batch_limit,
                include_recheck=include_recheck,
            )
            self.logger.info(
                "detail_queue",
                extra={"pendentes": len(pending), "include_recheck": include_recheck},
            )

            self._process_documents_phase(session, pending, summary)

            detail_started = time.monotonic()
            for serie in pending:
                if not self.budget.has_time():
                    summary["interrompido_por_tempo"] = True
                    self.logger.info(
                        "detail_time_budget_reached",
                        extra={"restante_ms": self.budget.remaining_ms()},
                    )
                    break
                self._process_single_detail(session, serie, summary)

            self.logger.info(
                "opea_phase_detail_done",
                extra={
                    "detalhes_processados": summary.get("detalhes_processados", 0),
                    "erros": summary.get("erros", 0),
                    "elapsed_seconds": round(time.monotonic() - detail_started, 2),
                    "interrompido_por_tempo": summary.get("interrompido_por_tempo", False),
                },
            )

    def _process_documents_phase(self, session, pending: list, summary: dict) -> None:
        """Phase B: filter cedoc once per distinct ``emissao_id`` in the pending queue."""
        started = time.monotonic()
        by_parent: dict[str, list] = defaultdict(list)
        for serie in pending:
            parent = serie.emissao_id or parent_codigo_opea(serie.id_origem)
            if not parent:
                continue
            by_parent[parent].append(serie)

        if not by_parent:
            self.logger.info(
                "opea_phase_docs_done",
                extra={
                    "parents": 0,
                    "documentos_gravados": 0,
                    "elapsed_seconds": 0,
                },
            )
            return

        id_cedoc = self._resolve_id_cedoc(pending)
        if not id_cedoc:
            summary["erros"] += 1
            self.logger.warning("opea_phase_docs_no_id_cedoc")
            return

        children = self._get_cedoc_children(id_cedoc)
        docs_before = int(summary.get("documentos_gravados") or 0)
        parents_done = 0

        for parent, members in by_parent.items():
            if parent in self._docs_done_for_emissao:
                continue
            if not self.budget.has_time():
                summary["interrompido_por_tempo"] = True
                self.logger.info(
                    "opea_phase_docs_time_budget_reached",
                    extra={"restante_ms": self.budget.remaining_ms(), "parents_done": parents_done},
                )
                break

            sample = members[0]
            try:
                documentos = self._documents_for_parent(sample, parent, children, id_cedoc)
                link_ids = resolve_serie_ids_by_emissao_id(session, self.source_name, parent)
                if not link_ids:
                    link_ids = [s.serie_id for s in members if s.serie_id is not None]
                for documento in documentos:
                    if not documento.link_documento:
                        continue
                    doc_id = upsert_documento(session, self.source_name, documento)
                    if doc_id is None:
                        continue
                    link_documento_series(session, doc_id, link_ids)
                    summary["documentos_gravados"] += 1
                session.commit()
                self._docs_done_for_emissao.add(parent)
                parents_done += 1
            except Exception as exc:
                session.rollback()
                summary["erros"] += 1
                self.logger.warning(
                    "opea_phase_docs_parent_error",
                    extra={"emissao_id": parent, "error": str(exc)},
                )

        self.logger.info(
            "opea_phase_docs_done",
            extra={
                "parents": len(by_parent),
                "parents_done": parents_done,
                "documentos_gravados": int(summary.get("documentos_gravados") or 0) - docs_before,
                "cedoc_children": len(children),
                "id_cedoc": id_cedoc,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "interrompido_por_tempo": summary.get("interrompido_por_tempo", False),
            },
        )

    def _resolve_id_cedoc(self, pending: list) -> str | None:
        """Prefer stored extras.idCedoc; otherwise one live detalhe from the queue."""
        for serie in pending:
            extras = serie.extras if isinstance(serie.extras, dict) else {}
            if extras.get("idCedoc"):
                return str(extras["idCedoc"])

        for serie in pending:
            if not self.budget.has_time():
                break
            detail = self._fetch_serie_detail(serie.id_origem)
            id_cedoc = detail.get("idCedoc") if detail else None
            if id_cedoc:
                return str(id_cedoc)
        return None

    def _documents_for_parent(
        self,
        sample,
        parent: str,
        children: list[dict],
        id_cedoc: str,
    ) -> list[DocumentoData]:
        emission_code = emission_file_code(sample.numero_emissao)
        natureza = None
        if isinstance(sample.extras, dict):
            natureza = sample.extras.get("natureza")
        natureza = natureza or natureza_from_parent(parent)
        vehicle = vehicle_from_parent(parent)
        if not emission_code:
            return []

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
                    numero_emissao=sample.numero_emissao,
                    codigo_cetip=sample.codigo_cetip,
                    emissao_id=parent,
                    id_origem_arquivo=file_id,
                    extras=_slim_cedoc_extras(child, id_cedoc),
                )
            )
        return docs

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
        codigo = serie.id_origem
        updates: dict = {
            "link": portal_link(codigo),
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
            },
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
                    "link": mapped.link or portal_link(codigo),
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
        """Build docs for one parent (used by repair tooling; Phase B uses ``_documents_for_parent``)."""
        id_cedoc = detail.get("idCedoc")
        if not id_cedoc:
            return []
        children = self._get_cedoc_children(str(id_cedoc))
        return self._documents_for_parent(serie, parent, children, str(id_cedoc))
