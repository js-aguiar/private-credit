# AGENTS.md

## Cursor Cloud specific instructions

This repo is **BR Securitization Scrapers**: a set of Python 3.12 batch/CLI scrapers for five
Brazilian securitization sites (`ecoagro`, `opea`, `riza`, `vert`, `bari`) that upsert into a
PostgreSQL database (`emissoes`, `series`, `documentos`). There is **no long-running dev
server** — "running the app" means invoking a scraper via `scripts/run_local.py`. Standard
setup/run commands live in `README.md`; only non-obvious notes are captured below.

### Product code lives on `main`
Application code is on `main` (historically developed on `feat/br-scrapers` and
`scrapers-and-first-webpage`). Do dev work against `main` or a branch based on it.

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
  `opea` is API-first with a Playwright fallback; `riza`, `vert`, and `bari` use public JSON
  APIs via httpx only (no browser).
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

### EC2 full backfill (AWS)
- Stack: `br-sec-scrapers-backfill` (launch template, backfill SSM prefix, log group).
- Launch: `python scripts/launch_ec2_backfill.py` after `cdk deploy br-sec-scrapers-backfill`.
- Logs: CloudWatch `/{prefix}/ec2-backfill` — JSON fields include `execution_mode` (`ec2_backfill`
  vs `lambda`) and `run_id` (same shape as Lambda handlers).
- Politeness: EC2 reads `/{prefix}/backfill/` SSM (default 2s delay, 20 req/min). Lambdas keep
  per-scraper `/{prefix}/{source}/` defaults (8s, 6 req/min).
- Max runtime: 24h (`backfill_max_hours` CDK context); instance self-terminates and deletes EBS.
- Incomplete backfill is OK — twice-daily Lambdas drain remaining `detalhes_coletados=false` rows.

### Public catalog (S3 + CloudFront + VPC Lambda)
- Stack: `br-sec-scrapers-web`. Deploy with `cd infra && cdk deploy br-sec-scrapers-web`.
- CatalogUrl output is the CDN base. **`/` is Emissões** (list/detail + company/CETIP/ISIN filters);
  **`/documentos` is the documents catalog** (CloudFront Function rewrites to `documentos.html` —
  without that rewrite, SPA 403/404 fallback would serve the emissoes `index.html`).
- API: `/api/emissoes`, `/api/emissoes/{id}`, `/api/emissoes/filters`, plus existing `/api/documents*`.
- Local: serve `web/` static files and run `python web/api/local.py` (see `web/api/local.py`).
