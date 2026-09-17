#!/usr/bin/env python3
"""Run all scrapers sequentially on a long-lived host (EC2 backfill).

Uses backfill-specific SSM tunables (``SSM_PREFIX=/{prefix}/backfill/``) and stops when
every source has ``detalhes_coletados=true`` for all discovered emissions, or when the
global deadline (default 24h) is reached.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared.config import ScraperConfig  # noqa: E402
from shared.db import ensure_schema, session_scope  # noqa: E402
from shared.logging_config import bind_run_context, configure_logging, get_logger  # noqa: E402
from shared.repository import count_emissoes_sem_detalhe  # noqa: E402
from shared.scraper_base import DeadlineTimeBudget  # noqa: E402

SCRAPERS: dict[str, tuple[str, str]] = {
    "ecoagro": ("scrapers.ecoagro.scraper", "EcoagroScraper"),
    "opea": ("scrapers.opea.scraper", "OpeaScraper"),
    "riza": ("scrapers.riza.scraper", "RizaScraper"),
    "vert": ("scrapers.vert.scraper", "VertScraper"),
    "bari": ("scrapers.bari.scraper", "BariScraper"),
}


def _load_scraper_class(name: str):
    module_path, class_name = SCRAPERS[name]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)

SCRAPER_ORDER = ("ecoagro", "opea", "riza", "vert", "bari")

_SUMMARY_KEYS = (
    "descobertas",
    "detalhes_processados",
    "series_gravadas",
    "documentos_gravados",
    "erros",
)


def _empty_totals() -> dict:
    return {key: 0 for key in _SUMMARY_KEYS}


def _merge_summary(totals: dict, summary: dict) -> None:
    for key in _SUMMARY_KEYS:
        totals[key] += int(summary.get(key) or 0)
    totals["interrompido_por_tempo"] = bool(summary.get("interrompido_por_tempo"))


def _run_source(source: str, deadline: float, logger) -> dict:
    totals = _empty_totals()
    totals["source"] = source
    batches = 0

    bind_run_context(source=source)
    logger.info("backfill_scraper_start", extra={"source": source})

    while time.monotonic() < deadline:
        klass = _load_scraper_class(source)
        config = ScraperConfig.from_env(source)
        config.use_browser_fallback = False
        config.auto_create_schema = False

        scraper = klass(config, context=None)
        scraper.budget = DeadlineTimeBudget(deadline, reserve_ms=config.time_reserve_ms)
        summary = scraper.run()
        batches += 1
        _merge_summary(totals, summary)

        with session_scope(config) as session:
            pending = count_emissoes_sem_detalhe(session, source)

        logger.info(
            "backfill_scraper_batch",
            extra={
                "source": source,
                "batch": batches,
                "pending_sem_detalhe": pending,
                **{k: summary.get(k) for k in _SUMMARY_KEYS},
            },
        )

        if pending == 0:
            break
        if not scraper.budget.has_time():
            totals["interrompido_por_tempo"] = True
            break

    if time.monotonic() >= deadline:
        totals["interrompido_por_tempo"] = True

    totals["batches"] = batches
    logger.info("backfill_scraper_complete", extra=totals)
    return totals


def _resolve_sources(requested: str | None) -> list[str]:
    """Return ordered scraper names from a comma-separated request (or full order)."""
    if not requested or not requested.strip():
        return list(SCRAPER_ORDER)
    wanted = {part.strip().lower() for part in requested.split(",") if part.strip()}
    unknown = wanted - set(SCRAPERS)
    ordered = [name for name in SCRAPER_ORDER if name in wanted]
    if unknown:
        raise SystemExit(f"Unknown scraper source(s): {', '.join(sorted(unknown))}")
    if not ordered:
        raise SystemExit("No valid scrapers selected via --sources / BACKFILL_SOURCES")
    return ordered


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scrapers for EC2/RDS backfill.")
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=int(os.getenv("BACKFILL_MAX_SECONDS", "86400")),
        help="Global wall-clock budget (default 24h).",
    )
    parser.add_argument(
        "--sources",
        default=os.getenv("BACKFILL_SOURCES"),
        help="Comma-separated scrapers to run (default: all).",
    )
    args = parser.parse_args()

    sources = _resolve_sources(args.sources)
    run_id = os.getenv("RUN_ID") or str(uuid.uuid4())
    configure_logging()
    bind_run_context(execution_mode="ec2_backfill", run_id=run_id)
    logger = get_logger("run_backfill_all")

    deadline = time.monotonic() + max(60, args.max_seconds)
    started_at = time.time()

    logger.info(
        "backfill_start",
        extra={
            "max_seconds": args.max_seconds,
            "scrapers": sources,
            "ssm_prefix": os.getenv("SSM_PREFIX"),
        },
    )

    # Apply schema.sql once (CREATE IF NOT EXISTS) so new tables like isin_contestados
    # exist on RDS even though per-batch runs keep auto_create_schema=False.
    schema_config = ScraperConfig.from_env(sources[0])
    ensure_schema(schema_config)

    per_source: dict[str, dict] = {}
    for source in sources:
        if time.monotonic() >= deadline:
            logger.info("backfill_deadline_reached_before_source", extra={"source": source})
            break
        per_source[source] = _run_source(source, deadline, logger)

    timed_out = time.monotonic() >= deadline or any(
        s.get("interrompido_por_tempo") for s in per_source.values()
    )
    result = {
        "run_id": run_id,
        "execution_mode": "ec2_backfill",
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started_at, 1),
        "sources": per_source,
    }
    logger.info("backfill_complete", extra=result)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
