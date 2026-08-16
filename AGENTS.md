# AGENTS.md

## Cursor Cloud specific instructions

This repo is **BR Securitization Scrapers**: a set of Python 3.12 batch/CLI scrapers for four
Brazilian securitization sites (`ecoagro`, `opea`, `riza`, `vert`) that upsert into a
PostgreSQL database (`emissoes`, `series`, `documentos`). There is **no long-running dev
server** — "running the app" means invoking a scraper via `scripts/run_local.py`. Standard
setup/run commands live in `README.md`; only non-obvious notes are captured below.

### Product code lives on a feature branch
The `main` branch contains only `LICENSE`. All application code is on `feat/br-scrapers`
(and branches based on it). Do dev work against that branch.

### Environment (already provisioned by the update script + snapshot)
- Python venv at `.venv/` — activate with `source .venv/bin/activate` before running anything.
  Deps are installed editable via `pip install -e ".[dev]"` (the update script keeps this current).
- PostgreSQL 16 is installed locally but is **not auto-started on boot**. Start it each session
  with `sudo pg_ctlcluster 16 main start`. The `securitizacao` database and a `postgres`/`postgres`
  superuser login already exist in the snapshot.
- Playwright Chromium is preinstalled (for the optional SPA browser fallback).

### Required env vars to run scrapers/scripts against the local DB
```bash
export DB_HOST=localhost DB_PORT=5432 DB_NAME=securitizacao DB_USER=postgres DB_PASSWORD=postgres DB_SSLMODE=disable
```
`DB_SSLMODE=disable` is important: `db_sslmode` defaults to `require` (for AWS RDS), which
fails against a plain local Postgres.

### Running / testing
- Init schema (idempotent): `python scripts/init_db.py`
- Run a scraper: `python scripts/run_local.py ecoagro [--max-items N] [--create-schema]`
- `ecoagro` is plain HTML (no browser needed) and is the most reliable end-to-end smoke test.
  `opea`/`riza`/`vert` are SPAs (API-first, Playwright fallback).
- Scrapers require **outbound internet** to the live target sites; a run can succeed with
  `descobertas: 0` if a site's API/markup changed — that is product behavior, not an env failure.
- The politeness throttle is slow by default (8s delay, 6 req/min). For quick local smoke tests
  set `REQUEST_DELAY_SECONDS=0 REQUEST_JITTER_SECONDS=0 MAX_REQUESTS_PER_MINUTE=600` and a small
  `--max-items`. Do **not** use these aggressive values for real backfills against the live sites.

### Lint / tests
- Lint: `ruff check .` (config in `pyproject.toml`). The repo currently reports pre-existing
  ruff findings — that is expected, not caused by setup.
- Tests: `pytest`. `testpaths=["tests"]` is configured but there is **no `tests/` directory**, so
  pytest currently collects 0 tests (exit code 5).
