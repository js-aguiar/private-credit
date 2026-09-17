"""Helpers for stable Opea document identity and URL normalization."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

_CEDOC_FILES_URL = "https://app.opea.com.br/bff/v1/api/cedoc/files"
# Cedoc lists are large (institution-wide). Cache briefly so opening several docs
# from the same emission does not re-download the payload each time.
_CEDOC_CHILDREN_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CEDOC_CACHE_TTL_SECONDS = 300.0


def normalize_opea_document_url(url: str) -> str:
    """Return the stable S3 object path without presigned query parameters."""
    parsed = urlparse(url.strip())
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def opea_file_id(extras: dict | None) -> str | None:
    """Return Opea's stable file UUID from cedoc metadata, if present."""
    if not extras:
        return None
    file_id = extras.get("id")
    if file_id is None:
        return None
    cleaned = str(file_id).strip()
    return cleaned or None


def _cedoc_children(id_cedoc: str, timeout_seconds: float) -> list[dict]:
    now = time.monotonic()
    cached = _CEDOC_CHILDREN_CACHE.get(id_cedoc)
    if cached and now - cached[0] < _CEDOC_CACHE_TTL_SECONDS:
        return cached[1]

    query = urlencode({"idCedoc": id_cedoc})
    request = Request(
        f"{_CEDOC_FILES_URL}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "br-securitization-scrapers/catalog",
            "Origin": "https://app.opea.com.br",
            "Referer": "https://app.opea.com.br/",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        payload: Any = json.loads(response.read().decode("utf-8"))
    children = payload.get("children") if isinstance(payload, dict) else None
    if not isinstance(children, list):
        return []
    rows = [child for child in children if isinstance(child, dict)]
    _CEDOC_CHILDREN_CACHE[id_cedoc] = (now, rows)
    return rows


def refresh_opea_presigned_url(
    *,
    id_cedoc: str,
    file_id: str | None = None,
    stored_url: str | None = None,
    timeout_seconds: float = 60.0,
) -> str | None:
    """Fetch a fresh cedoc presigned URL for an Opea file.

    Stored catalog links intentionally strip ``X-Amz-*`` query params so dedupe stays
    stable; opening those bare S3 paths returns AccessDenied XML. Callers should use
    this helper (or ``/api/documents/{id}/open``) when a browser needs a working link.
    """
    cedoc = (id_cedoc or "").strip()
    if not cedoc:
        return None

    target_id = (file_id or "").strip() or None
    target_path = urlparse(stored_url.strip()).path if stored_url else None
    if not target_id and not target_path:
        return None

    try:
        children = _cedoc_children(cedoc, timeout_seconds)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    for child in children:
        url = child.get("url")
        if not url:
            continue
        if target_id and opea_file_id(child) == target_id:
            return str(url)
        if target_path and urlparse(str(url)).path == target_path:
            return str(url)
    return None
