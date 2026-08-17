"""AWS Lambda entrypoint for the read-only document catalog API.

Handles API Gateway HTTP API (payload v2) events. SQL is SELECT-only; the browser
never receives database credentials.
"""

from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import parse_qs

from queries import DEFAULT_LIMIT, get_document, list_documents, list_filters

from shared.config import ScraperConfig
from shared.db import session_scope
from shared.logging_config import configure_logging, get_logger

_DOC_DETAIL = re.compile(r"^/api/documents/(\d+)/?$")
_DOC_LIST = re.compile(r"^/api/documents/?$")
_FILTERS = re.compile(r"^/api/filters/?$")

_CORS_HEADERS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,OPTIONS",
    "access-control-allow-headers": "content-type",
}


def handler(event, context):
    configure_logging()
    logger = get_logger("handler.catalog")
    method, path, query = _parse_event(event)

    if method == "OPTIONS":
        return _response(204, None)

    if method != "GET":
        return _response(405, {"error": "method_not_allowed"})

    try:
        config = ScraperConfig.from_env("catalog")
        if path.endswith("/api") or path.endswith("/api/"):
            return _response(200, {"ok": True, "service": "catalog"})
        if _FILTERS.match(path):
            with session_scope(config) as session:
                return _response(200, list_filters(session))
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


def _parse_event(event: dict) -> tuple[str, str, dict[str, str]]:
    ctx = event.get("requestContext") or {}
    http = ctx.get("http") or {}
    method = (http.get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or event.get("path") or "/"
    if not path.startswith("/api"):
        # Allow local.py and stripped proxy paths.
        path = "/api" + (path if path.startswith("/") else f"/{path}")
    query = event.get("queryStringParameters")
    if query is None and event.get("rawQueryString"):
        query = {key: values[-1] for key, values in parse_qs(event["rawQueryString"]).items()}
    return method, path.rstrip("/") or "/", dict(query or {})


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
