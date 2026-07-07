"""Shared library for the Brazilian securitization scrapers.

Contains configuration, logging, database access, parsing helpers, the polite HTTP
client, the Playwright fallback, and the abstract scraper base class that implements the
incremental discover + re-check workflow shared by every scraper.
"""

__all__ = [
    "config",
    "logging_config",
    "parsing",
    "db",
    "models",
    "records",
    "repository",
    "http_client",
    "browser",
    "scraper_base",
]
