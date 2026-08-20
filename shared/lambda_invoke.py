"""Shared helpers for Lambda handler entrypoints."""

from __future__ import annotations

from typing import Any

from .logging_config import bind_run_context


def log_invoke_start(logger, context: Any, source: str) -> None:
    run_id = getattr(context, "aws_request_id", None) if context is not None else None
    bind_run_context(execution_mode="lambda", run_id=run_id, source=source)
    logger.info("invoke_start", extra={"execution_mode": "lambda", "run_id": run_id, "source": source})
