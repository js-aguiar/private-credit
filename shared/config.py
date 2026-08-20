"""Runtime configuration for the scrapers.

Values are read from environment variables and may optionally be overridden at runtime
from AWS SSM Parameter Store (when ``SSM_PREFIX`` is set). This lets operators tune the
politeness delay, rate cap, timeouts, etc. without rebuilding/redeploying the images.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Any

try:  # boto3 is only required on AWS; keep imports lazy-friendly for local use.
    import boto3
except ImportError:  # pragma: no cover - boto3 always present in the Lambda image.
    boto3 = None


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _get_str(name: str, default: str | None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in _TRUE_VALUES


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass
class ScraperConfig:
    """All tunables for a single scraper run."""

    source_name: str

    # --- HTTP politeness / robustness ---
    # A "sufficient" pause between calls, tuned per site to avoid blocking/overloading.
    request_delay_seconds: float = 8.0
    request_jitter_seconds: float = 4.0
    max_requests_per_minute: float = 6.0
    request_timeout_seconds: float = 45.0
    max_retries: int = 4
    backoff_base_seconds: float = 2.0
    user_agent: str = (
        "Mozilla/5.0 (compatible; BR-Securitization-Scraper/1.0; "
        "data-collection; contact: admin@example.com)"
    )

    # --- Scope / execution limits ---
    detail_batch_limit: int = 5000
    time_reserve_ms: int = 90_000
    use_browser_fallback: bool = True
    auto_create_schema: bool = False

    # --- Database ---
    db_secret_arn: str | None = None
    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "securitizacao"
    db_user: str | None = None
    db_password: str | None = None
    db_sslmode: str = "require"

    # --- AWS ---
    aws_region: str | None = None
    ssm_prefix: str | None = None

    # Numeric fields that SSM/env overrides may target (name -> caster).
    _NUMERIC_OVERRIDES = {
        "request_delay_seconds": float,
        "request_jitter_seconds": float,
        "max_requests_per_minute": float,
        "request_timeout_seconds": float,
        "backoff_base_seconds": float,
        "max_retries": int,
        "detail_batch_limit": int,
        "time_reserve_ms": int,
        "db_port": int,
    }

    @classmethod
    def from_env(cls, source_name: str) -> "ScraperConfig":
        cfg = cls(
            source_name=source_name,
            request_delay_seconds=_get_float("REQUEST_DELAY_SECONDS", cls.request_delay_seconds),
            request_jitter_seconds=_get_float("REQUEST_JITTER_SECONDS", cls.request_jitter_seconds),
            max_requests_per_minute=_get_float(
                "MAX_REQUESTS_PER_MINUTE", cls.max_requests_per_minute
            ),
            request_timeout_seconds=_get_float(
                "REQUEST_TIMEOUT_SECONDS", cls.request_timeout_seconds
            ),
            max_retries=_get_int("MAX_RETRIES", cls.max_retries),
            backoff_base_seconds=_get_float("BACKOFF_BASE_SECONDS", cls.backoff_base_seconds),
            user_agent=_get_str("USER_AGENT", cls.user_agent),
            detail_batch_limit=_get_int("DETAIL_BATCH_LIMIT", cls.detail_batch_limit),
            time_reserve_ms=_get_int("TIME_RESERVE_MS", cls.time_reserve_ms),
            use_browser_fallback=_get_bool("USE_BROWSER_FALLBACK", cls.use_browser_fallback),
            auto_create_schema=_get_bool("AUTO_CREATE_SCHEMA", cls.auto_create_schema),
            db_secret_arn=_get_str("DB_SECRET_ARN", None),
            db_host=_get_str("DB_HOST", None),
            db_port=_get_int("DB_PORT", 5432),
            db_name=_get_str("DB_NAME", cls.db_name),
            db_user=_get_str("DB_USER", None),
            db_password=_get_str("DB_PASSWORD", None),
            db_sslmode=_get_str("DB_SSLMODE", cls.db_sslmode),
            aws_region=_get_str("AWS_REGION", None) or _get_str("AWS_DEFAULT_REGION", None),
            ssm_prefix=_get_str("SSM_PREFIX", None),
        )
        cfg._apply_ssm_overrides()
        return cfg

    def _apply_ssm_overrides(self) -> None:
        """Override numeric/string tunables from SSM Parameter Store (best-effort)."""
        if not self.ssm_prefix or boto3 is None:
            return
        try:
            client = boto3.client("ssm", region_name=self.aws_region)
            params: dict[str, str] = {}
            paginator = client.get_paginator("get_parameters_by_path")
            for page in paginator.paginate(
                Path=self.ssm_prefix, Recursive=True, WithDecryption=True
            ):
                for param in page.get("Parameters", []):
                    key = param["Name"].rsplit("/", 1)[-1].lower()
                    params[key] = param["Value"]
        except Exception:  # pragma: no cover - never fail a run because of SSM.
            return

        field_names = {f.name for f in fields(self)}
        for key, raw in params.items():
            if key not in field_names:
                continue
            self._set_override(key, raw)

    def _set_override(self, key: str, raw: str) -> None:
        caster = self._NUMERIC_OVERRIDES.get(key)
        value: Any
        if caster is not None:
            try:
                value = caster(raw)
            except ValueError:
                return
        elif isinstance(getattr(self, key), bool):
            value = raw.strip().lower() in _TRUE_VALUES
        else:
            value = raw
        setattr(self, key, value)
