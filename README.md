# BR Securitization Scrapers

Daily, incremental scrapers for four Brazilian securitization ("securitizadoras") websites.
Each run **discovers new operations (emissions)** and **re-checks already-scraped
operations for updated information** — especially newly published documents.

Extracted data is stored in a single **Amazon RDS for PostgreSQL** database, in three
tables whose names/columns are in Portuguese (matching the terminology used by the
websites): `emissoes`, `series`, and `documentos`.

Infrastructure is defined with **AWS CDK (Python)**; each scraper runs as an **AWS
Lambda container image**. Scheduling/automation is intentionally **not** provisioned
(per project scope) — the scrapers are deployable and runnable on demand.

## Target sites

| Source (`fonte`) | URL | Rendering | Strategy |
| --- | --- | --- | --- |
| `ecoagro` | https://ecoagro.agr.br/emissoes | Server-rendered HTML (paginated) | `httpx` + BeautifulSoup |
| `opea` | https://app.opea.com.br/pt/emissoes | Vue/Vite SPA (JSON API) | API-first, Playwright fallback |
| `riza` | https://investidor.rizasec.com/emissoes | Next.js SPA (JSON API) | API-first, Playwright fallback |
| `vert` | https://data.vert-capital.app/ | React-Router SPA (JSON API) | API-first, Playwright fallback |

## Repository layout

```
br-securitization-scrapers/
  shared/            # Shared library used by every scraper
  scrapers/          # One folder per website (Lambda handler + Dockerfile)
    ecoagro/
    opea/
    riza/
    vert/
  infra/             # AWS CDK (Python) app and stacks
  scripts/           # Local DB init + time-unlimited local runner
```

## Data model

`series.isin` (ISIN) is the natural business key that links the tables, but because an
emission can have several séries (each with its own ISIN/CETIP code) the tables use
stable surrogate keys internally and carry `isin`, `numero_emissao`, and `codigo_cetip`
on every table for cross-linking. A `extras` JSONB column on each table stores any
site-specific fields so richer sources never lose information.

- `emissoes` — one row per operation/emission (list + detail fields, scrape metadata).
- `series` — one row per série of an emission.
- `documentos` — one row per document (title, link, document date, insertion date, ...).

See [`shared/schema.sql`](shared/schema.sql) for the authoritative DDL.

## Local development

Requirements: Python 3.12+, a reachable PostgreSQL instance, and (for SPA fallback)
Playwright browsers.

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m playwright install chromium                # only needed for SPA fallback

# Point at your database (or use a DB_SECRET_ARN on AWS)
export DB_HOST=localhost DB_PORT=5432 DB_NAME=securitizacao DB_USER=postgres DB_PASSWORD=postgres

# Create the tables
python scripts/init_db.py

# Run a scraper locally (no Lambda time limit). --once processes a single pass.
python scripts/run_local.py ecoagro
python scripts/run_local.py vert --max-items 20
```

### Configuration (environment variables / SSM)

All tunables are read from environment variables and can be overridden at runtime from
SSM Parameter Store (prefix set via `SSM_PREFIX`). Key settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `REQUEST_DELAY_SECONDS` | `8` | Base pause between requests (politeness) |
| `REQUEST_JITTER_SECONDS` | `4` | Random extra delay added to each request |
| `MAX_REQUESTS_PER_MINUTE` | `6` | Hard rate cap per scraper |
| `REQUEST_TIMEOUT_SECONDS` | `45` | Per-request timeout |
| `MAX_RETRIES` | `4` | Retries on 429/5xx/network errors |
| `USE_BROWSER_FALLBACK` | `true` | Enable Playwright fallback for SPAs |
| `DETAIL_BATCH_LIMIT` | `5000` | Max detail pages processed per invocation |
| `TIME_RESERVE_MS` | `90000` | Time kept in reserve before Lambda timeout |
| `AUTO_CREATE_SCHEMA` | `false` | Create tables on startup if missing |
| `DB_SECRET_ARN` | – | Secrets Manager ARN with DB credentials |
| `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD` | – | Direct DB config (local) |
| `SSM_PREFIX` | – | e.g. `/br-sec-scrapers/ecoagro/` |

The delay is deliberately a "sufficient" pause (tunable per site) rather than a fixed
several-second wait, to avoid blocking or overloading the sites/APIs.

## Deploying to AWS

```bash
cd infra
pip install -r requirements.txt
cdk bootstrap        # first time only
cdk deploy --all
```

This provisions: a VPC (with a low-cost NAT instance), an RDS PostgreSQL
`db.t4g.micro` (single-AZ, private), four Lambda container functions (built from
`scrapers/*/Dockerfile`), IAM roles, SSM parameters, CloudWatch log groups + error
alarms, and an SNS topic for alerts. **No schedule is created** — invoke the Lambdas
manually (or wire your own trigger later).

The initial full backfill is best run with `scripts/run_local.py` (no 15-minute limit);
afterwards, incremental daily invocations of the Lambdas stay well within the limit.

## Incremental & re-check behavior

- Discovery upserts the emission list; new operations start with `detalhes_coletados = false`.
- Every run also re-opens existing operations (oldest `ultima_verificacao_detalhe` first)
  and re-parses the full document list, so **new documents on old emissions are captured**.
- All writes are idempotent upserts; documents dedupe by `link_documento`.

## Legal / operational note

These are public financial-disclosure pages. Scraping here is throttled and read-only,
but review each site's Terms of Service before running in production.
