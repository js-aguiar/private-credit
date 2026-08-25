#!/usr/bin/env python3
"""Truncate emissoes / series / documentos (all rows).

Intended for local use or via Lambda:
  aws lambda invoke --function-name br-sec-scrapers-opea \\
    --payload '{"action":"truncate_all_tables"}' /tmp/truncate.json
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared.logging_config import configure_logging, get_logger  # noqa: E402
from shared.truncate_db import truncate_all_tables  # noqa: E402


def main() -> int:
    configure_logging()
    logger = get_logger("truncate_db")
    logger.info("truncate_all_start")
    summary = truncate_all_tables()
    print(json.dumps(summary.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
