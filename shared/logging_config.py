"""Structured JSON logging.

Logs are written to stdout as single-line JSON objects, which CloudWatch Logs captures
directly and which are easy to query with CloudWatch Logs Insights.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

_CONFIGURED = False

_run_context: ContextVar[dict[str, Any] | None] = ContextVar("run_context", default=None)


def bind_run_context(**fields: Any) -> None:
    """Attach structured fields to every subsequent log line in this execution context."""
    current = dict(_run_context.get() or {})
    for key, value in fields.items():
        if value is not None:
            current[key] = value
    _run_context.set(current)


def get_run_context() -> dict[str, Any]:
    ctx = _run_context.get()
    return dict(ctx) if ctx else {}


class JsonFormatter(logging.Formatter):
    """Render log records as compact JSON lines."""

    RESERVED = {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info", "thread",
        "threadName", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(get_run_context())
        # Attach any structured extras passed via logger.info(..., extra={...}).
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str | None = None) -> None:
    """Idempotently configure root logging for JSON output to stdout."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quiet down noisy third-party libraries.
    for noisy in ("httpx", "httpcore", "botocore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
