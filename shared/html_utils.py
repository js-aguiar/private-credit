"""Small HTML helpers shared across the HTML/DOM-based scrapers."""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .parsing import clean_text

_DOC_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip", ".ppt", ".pptx", ".txt",
)
_DOC_HINTS = ("download", "documento", "arquivo", "/doc", "/file", "anexo", "prospecto")


def soupify(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def absolute_url(base_url: str, href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if not href or href.startswith(("javascript:", "#", "mailto:")):
        return None
    return urljoin(base_url, href)


def looks_like_document(href: str) -> bool:
    lowered = href.lower()
    if lowered.endswith(_DOC_EXTENSIONS):
        return True
    return any(hint in lowered for hint in _DOC_HINTS)


def extract_document_links(container, base_url: str) -> list[tuple[str | None, str]]:
    """Return (title, absolute_url) tuples for anchor tags that look like documents.

    Deduplicates by URL while preserving order.
    """
    results: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    if container is None:
        return results
    for anchor in container.find_all("a", href=True):
        url = absolute_url(base_url, anchor.get("href"))
        if not url or url in seen:
            continue
        if not looks_like_document(url):
            continue
        title = clean_text(anchor.get_text()) or clean_text(anchor.get("title"))
        seen.add(url)
        results.append((title, url))
    return results
