"""Generic helpers for mapping loosely-structured JSON (from SPA APIs) into records.

The three SPA sites (opea/riza/vert) return JSON whose exact field names must be
confirmed against live payloads. These helpers make the mapping tolerant: keys are
matched case-insensitively and several common container shapes are understood, so the
scrapers keep working across minor API differences.
"""

from __future__ import annotations

from typing import Any, Iterable

# Keys under which paginated APIs commonly nest their record arrays.
_LIST_CONTAINER_KEYS = ("content", "items", "data", "results", "records", "rows", "list")


def pick(mapping: Any, *keys: str, default: Any = None) -> Any:
    """Return the first present, non-null value among ``keys`` (case-insensitive)."""
    if not isinstance(mapping, dict):
        return default
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, "", []):
            return value
    return default


def as_records(payload: Any) -> list[dict]:
    """Coerce a JSON payload into a list of record dicts.

    Handles: a bare list, ``{"content": [...]}``-style wrappers, and single objects.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in _LIST_CONTAINER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        # Nested one level deep (e.g. {"data": {"content": [...]}}).
        for value in payload.values():
            if isinstance(value, dict):
                nested = as_records(value)
                if nested:
                    return nested
        # A single record object.
        return [payload]
    return []


def find_document_dicts(payload: Any) -> list[dict]:
    """Recursively find dicts that look like documents (have a URL + a title/name)."""
    found: list[dict] = []
    _walk_for_documents(payload, found)
    # Deduplicate by the resolved URL.
    seen: set[str] = set()
    unique: list[dict] = []
    for doc in found:
        url = pick(doc, "url", "link", "arquivo", "href", "path", "downloadUrl", "urlArquivo")
        if url and url not in seen:
            seen.add(url)
            unique.append(doc)
    return unique


_DOC_URL_KEYS = ("url", "link", "arquivo", "href", "path", "downloadurl", "urlarquivo")
_DOC_TITLE_KEYS = ("titulo", "title", "nome", "name", "descricao", "descrition", "label")


def _walk_for_documents(obj: Any, found: list[dict]) -> None:
    if isinstance(obj, dict):
        keys = {str(k).lower() for k in obj.keys()}
        has_url = keys & set(_DOC_URL_KEYS)
        has_title = keys & set(_DOC_TITLE_KEYS)
        url_value = pick(obj, *_DOC_URL_KEYS)
        if has_url and has_title and isinstance(url_value, str) and _is_doc_url(url_value):
            found.append(obj)
        for value in obj.values():
            _walk_for_documents(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_documents(item, found)


def _is_doc_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(("http", "/")) and (
        lowered.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".csv"))
        or any(hint in lowered for hint in ("download", "documento", "arquivo", "/doc", "file"))
    )


def iter_all_records(payloads: Iterable[Any]) -> list[dict]:
    out: list[dict] = []
    for payload in payloads:
        out.extend(as_records(payload))
    return out


def find_dicts_with_key(payload: Any, keys: Iterable[str]) -> list[dict]:
    """Recursively collect dicts that directly contain any of ``keys`` (case-insensitive).

    Used to locate per-série objects inside loosely-structured detail payloads.
    """
    wanted = {k.lower() for k in keys}
    found: list[dict] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if wanted & {str(k).lower() for k in obj.keys()}:
                found.append(obj)
            for value in obj.values():
                _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(payload)
    return found
