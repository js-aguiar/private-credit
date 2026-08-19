#!/usr/bin/env python3
"""Remove duplicate Opea documents from the database.

Safe to run repeatedly. Uses the stable Opea cedoc file id (``extras.id``) to keep
one row per physical file and normalizes presigned S3 URLs to their object path.

Example (AWS via Secrets Manager):

    DB_SECRET_ARN=arn:aws:secretsmanager:... DB_SSLMODE=require python3 scripts/dedupe_opea_documents.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared.dedupe_opea import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
