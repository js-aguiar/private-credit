"""AWS Lambda entrypoint for the catalog API (series, documents, portfolios).

Handles API Gateway HTTP API (payload v2) events. The browser never receives database
credentials. HTTP Basic Auth (same credentials as CloudFront) blocks direct execute-api
access. Portfolio routes may INSERT/UPDATE/DELETE into ``carteiras``.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
from datetime import date
from urllib.parse import parse_qs, unquote

from portfolios import (
    add_serie_to_portfolio,
    create_portfolio_with_series,
    delete_portfolio,
    find_series_by_code,
    get_portfolio,
    list_portfolios,
    remove_serie_from_portfolio,
    rename_portfolio,
)
from queries import (
    DEFAULT_LIMIT,
    get_document,
    get_serie,
    list_documents,
    list_filters,
    list_series,
    list_series_filters,
    resolve_document_open_url,
)

from shared.config import ScraperConfig
from shared.db import ensure_schema, session_scope
from shared.logging_config import configure_logging, get_logger

_DOC_OPEN = re.compile(r"^/api/documents/(\d+)/open/?$")
_DOC_DETAIL = re.compile(r"^/api/documents/(\d+)/?$")
_DOC_LIST = re.compile(r"^/api/documents/?$")
_FILTERS = re.compile(r"^/api/filters/?$")
_SERIE_FILTERS = re.compile(r"^/api/series/filters/?$")
_SERIE_LOOKUP = re.compile(r"^/api/series/lookup/?$")
_SERIE_DETAIL = re.compile(r"^/api/series/(\d+)/?$")
_SERIE_LIST = re.compile(r"^/api/series/?$")
_EMISSAO_FILTERS = re.compile(r"^/api/emissoes/filters/?$")
_EMISSAO_DETAIL = re.compile(r"^/api/emissoes/(\d+)/?$")
_EMISSAO_LIST = re.compile(r"^/api/emissoes/?$")
_PORTFOLIO_SERIE = re.compile(r"^/api/portfolios/([^/]+)/series/(\d+)/?$")
_PORTFOLIO_SERIES = re.compile(r"^/api/portfolios/([^/]+)/series/?$")
_PORTFOLIO_DETAIL = re.compile(r"^/api/portfolios/([^/]+)/?$")
_PORTFOLIO_LIST = re.compile(r"^/api/portfolios/?$")

_DEFAULT_BASIC_USER = "admin"
_DEFAULT_BASIC_PASSWORD = "cloudf@990055"

_CORS_HEADERS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
    "access-control-allow-headers": "content-type,authorization",
}


def handler(event, context):
    configure_logging()
    logger = get_logger("handler.catalog")
    method, path, query = _parse_event(event)

    if method == "OPTIONS":
        return _response(204, None)

    if not _is_authorized(event):
        return _unauthorized()

    try:
        config = ScraperConfig.from_env("catalog")
        body = _parse_body(event)

        if path.endswith("/api") or path.endswith("/api/"):
            if method != "GET":
                return _response(405, {"error": "method_not_allowed"})
            return _response(200, {"ok": True, "service": "catalog"})

        # Portfolios (carteiras) — may write.
        if _PORTFOLIO_LIST.match(path) or path.rstrip("/") == "/api/portfolios":
            ensure_schema(config)
            return _handle_portfolios_collection(method, body, config)

        portfolio_serie = _PORTFOLIO_SERIE.match(path)
        if portfolio_serie:
            ensure_schema(config)
            nome = unquote(portfolio_serie.group(1))
            serie_id = int(portfolio_serie.group(2))
            return _handle_portfolio_serie(method, nome, serie_id, config)

        portfolio_series = _PORTFOLIO_SERIES.match(path)
        if portfolio_series:
            ensure_schema(config)
            nome = unquote(portfolio_series.group(1))
            return _handle_portfolio_series(method, nome, body, config)

        portfolio_detail = _PORTFOLIO_DETAIL.match(path)
        if portfolio_detail:
            ensure_schema(config)
            nome = unquote(portfolio_detail.group(1))
            return _handle_portfolio_detail(method, nome, body, config)

        if method != "GET":
            return _response(405, {"error": "method_not_allowed"})

        if _FILTERS.match(path):
            with session_scope(config) as session:
                return _response(200, list_filters(session))
        if _SERIE_FILTERS.match(path) or _EMISSAO_FILTERS.match(path):
            with session_scope(config) as session:
                return _response(200, list_series_filters(session))
        if _SERIE_LOOKUP.match(path):
            with session_scope(config) as session:
                return _response(
                    200,
                    {
                        "items": find_series_by_code(
                            session,
                            cetip=_opt_str(query.get("cetip")),
                            isin=_opt_str(query.get("isin")),
                            q=_opt_str(query.get("q")),
                        )
                    },
                )
        serie_detail = _SERIE_DETAIL.match(path) or _EMISSAO_DETAIL.match(path)
        if serie_detail:
            with session_scope(config) as session:
                payload = get_serie(session, int(serie_detail.group(1)))
            if payload is None:
                return _response(404, {"error": "not_found"})
            return _response(200, payload)
        if _SERIE_LIST.match(path) or _EMISSAO_LIST.match(path):
            params = _serie_list_params(query)
            with session_scope(config) as session:
                return _response(200, list_series(session, **params))
        open_match = _DOC_OPEN.match(path)
        if open_match:
            with session_scope(config) as session:
                target = resolve_document_open_url(session, int(open_match.group(1)))
            if target is None:
                return _response(404, {"error": "not_found"})
            return _redirect(target)
        detail = _DOC_DETAIL.match(path)
        if detail:
            with session_scope(config) as session:
                payload = get_document(session, int(detail.group(1)))
            if payload is None:
                return _response(404, {"error": "not_found"})
            return _response(200, payload)
        if _DOC_LIST.match(path):
            params = _list_params(query)
            with session_scope(config) as session:
                return _response(200, list_documents(session, **params))
        return _response(404, {"error": "not_found"})
    except ValueError as exc:
        logger.warning("bad_request", extra={"error": str(exc), "path": path})
        return _response(400, {"error": "bad_request", "message": str(exc)})
    except Exception:
        logger.exception("catalog_error", extra={"path": path})
        return _response(500, {"error": "internal_error"})


def _handle_portfolios_collection(method: str, body: dict, config) -> dict:
    if method == "GET":
        with session_scope(config) as session:
            return _response(200, list_portfolios(session))
    if method == "POST":
        nome = body.get("nome")
        serie_ids = body.get("serie_ids") or body.get("series") or []
        if body.get("serie_id") is not None:
            serie_ids = list(serie_ids) + [body.get("serie_id")]
        try:
            with session_scope(config) as session:
                payload = create_portfolio_with_series(
                    session, nome=nome, serie_ids=serie_ids
                )
            return _response(201, payload)
        except LookupError as exc:
            code = str(exc)
            if code == "portfolio_name_exists":
                return _response(409, {"error": "portfolio_name_exists"})
            if code.startswith("serie_not_found"):
                return _response(404, {"error": "serie_not_found"})
            return _response(404, {"error": code})
    return _response(405, {"error": "method_not_allowed"})


def _handle_portfolio_detail(method: str, nome: str, body: dict, config) -> dict:
    if method == "GET":
        with session_scope(config) as session:
            payload = get_portfolio(session, nome)
        if payload is None:
            return _response(404, {"error": "portfolio_not_found"})
        return _response(200, payload)
    if method == "PATCH":
        new_nome = body.get("nome") or body.get("new_nome")
        try:
            with session_scope(config) as session:
                payload = rename_portfolio(session, nome=nome, new_nome=new_nome)
            return _response(200, payload)
        except LookupError as exc:
            code = str(exc)
            status = 409 if code == "portfolio_name_exists" else 404
            return _response(status, {"error": code})
    if method == "DELETE":
        try:
            with session_scope(config) as session:
                payload = delete_portfolio(session, nome=nome)
            return _response(200, payload)
        except LookupError:
            return _response(404, {"error": "portfolio_not_found"})
    return _response(405, {"error": "method_not_allowed"})


def _handle_portfolio_series(method: str, nome: str, body: dict, config) -> dict:
    if method != "POST":
        return _response(405, {"error": "method_not_allowed"})
    try:
        with session_scope(config) as session:
            payload = add_serie_to_portfolio(
                session,
                nome=nome,
                serie_id=body.get("serie_id"),
                cetip=_opt_str(body.get("cetip")),
                isin=_opt_str(body.get("isin")),
            )
        return _response(200, payload)
    except LookupError as exc:
        return _response(404, {"error": str(exc)})


def _handle_portfolio_serie(method: str, nome: str, serie_id: int, config) -> dict:
    if method != "DELETE":
        return _response(405, {"error": "method_not_allowed"})
    try:
        with session_scope(config) as session:
            payload = remove_serie_from_portfolio(
                session, nome=nome, serie_id=serie_id
            )
        return _response(200, payload)
    except LookupError as exc:
        return _response(404, {"error": str(exc)})


def _basic_credentials() -> tuple[str, str]:
    user = os.environ.get("CATALOG_BASIC_USER") or _DEFAULT_BASIC_USER
    password = os.environ.get("CATALOG_BASIC_PASSWORD") or _DEFAULT_BASIC_PASSWORD
    return user, password


def _header(event: dict, name: str) -> str | None:
    headers = event.get("headers") or {}
    if not isinstance(headers, dict):
        return None
    lower = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lower:
            if isinstance(value, list):
                return value[0] if value else None
            return value
    return None


def _is_authorized(event: dict) -> bool:
    expected_user, expected_password = _basic_credentials()
    raw = _header(event, "authorization")
    if not raw:
        return False
    scheme, _, token = raw.strip().partition(" ")
    if scheme.lower() != "basic" or not token:
        return False
    try:
        decoded = base64.b64decode(token.encode("ascii"), validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        return False
    user, sep, password = decoded.partition(":")
    if not sep:
        return False
    return hmac.compare_digest(user, expected_user) and hmac.compare_digest(
        password, expected_password
    )


def _unauthorized() -> dict:
    headers = {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "www-authenticate": 'Basic realm="Catalog"',
        **_CORS_HEADERS,
    }
    return {
        "statusCode": 401,
        "headers": headers,
        "body": json.dumps({"error": "unauthorized"}, ensure_ascii=False),
    }


def _parse_event(event: dict) -> tuple[str, str, dict[str, str]]:
    ctx = event.get("requestContext") or {}
    http = ctx.get("http") or {}
    method = (http.get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or event.get("path") or "/"
    if not path.startswith("/api"):
        path = "/api" + (path if path.startswith("/") else f"/{path}")
    query = event.get("queryStringParameters")
    if query is None and event.get("rawQueryString"):
        query = {key: values[-1] for key, values in parse_qs(event["rawQueryString"]).items()}
    return method, path.rstrip("/") or "/", dict(query or {})


def _parse_body(event: dict) -> dict:
    raw = event.get("body")
    if raw is None or raw == "":
        return {}
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON body") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _list_params(query: dict[str, str]) -> dict:
    return {
        "fonte": _opt_str(query.get("fonte")),
        "devedor": _opt_str(query.get("devedor") or query.get("company")),
        "tipo_documento": _opt_str(query.get("tipo_documento") or query.get("type")),
        "date_from": _opt_date(query.get("date_from")),
        "date_to": _opt_date(query.get("date_to")),
        "limit": _opt_int(query.get("limit"), DEFAULT_LIMIT),
        "offset": _opt_int(query.get("offset"), 0),
    }


def _serie_list_params(query: dict[str, str]) -> dict:
    return {
        "fonte": _opt_str(query.get("fonte")),
        "company": _opt_str(query.get("company") or query.get("devedor")),
        "cetip": _opt_str(query.get("cetip")),
        "isin": _opt_str(query.get("isin")),
        "limit": _opt_int(query.get("limit"), DEFAULT_LIMIT),
        "offset": _opt_int(query.get("offset"), 0),
    }


def _opt_str(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _opt_date(value: str | None) -> date | None:
    cleaned = _opt_str(value)
    if cleaned is None:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"invalid date: {cleaned}") from exc


def _opt_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"invalid integer: {value}") from exc


def _response(status: int, body: dict | None) -> dict:
    headers = {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        **_CORS_HEADERS,
    }
    payload = "" if body is None else json.dumps(body, ensure_ascii=False, default=str)
    return {"statusCode": status, "headers": headers, "body": payload}


def _redirect(location: str) -> dict:
    headers = {
        "location": location,
        "cache-control": "no-store",
        **_CORS_HEADERS,
    }
    return {"statusCode": 302, "headers": headers, "body": ""}
