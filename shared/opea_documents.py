"""Helpers for stable Opea document identity and URL normalization."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


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
