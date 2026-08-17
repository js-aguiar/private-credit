# BR Securitization Scrapers

Daily, incremental scrapers for four Brazilian securitization ("securitizadoras") websites.
Each run **discovers new operations (emissions)** and **re-checks already-scraped
operations for updated information** — especially newly published documents.

Extracted data is stored in a single **Amazon RDS for PostgreSQL** database, in three
tables whose names/columns are in Portuguese (matching the terminology used by the
websites): `emissoes`, `series`, and `documentos`.

Infrastructure is defined with **AWS CDK (Python)**; each scraper runs as an **AWS
Lambda container image**. After an initial local backfill, **EventBridge Scheduler**
invokes the Lambdas twice daily at 10:00 and 18:00 America/Sao_Paulo (GMT-3).

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
  web/               # Document catalog UI (static) + read-only API Lambda
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

# Document catalog (read-only UI against the local DB)
export DB_SSLMODE=disable   # required for a plain local Postgres
python web/api/local.py                              # API on http://127.0.0.1:8081
python -m http.server 8080 --directory web           # UI on http://127.0.0.1:8080
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
| `DB_SSLMODE` | `require` | Use `disable` against a local Postgres without TLS |
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
alarms, an SNS topic for alerts, **EventBridge Scheduler** schedules that invoke
each scraper twice daily, and a **document catalog** (private S3 + CloudFront
HTTPS + API Gateway HTTP API + VPC Lambda).

The catalog URL is the `CatalogUrl` CloudFormation output
(`https://<distribution>.cloudfront.net`). The browser never talks to RDS: CloudFront
serves the static UI and proxies `/api/*` to a read-only Lambda that connects to
Postgres over TLS (`DB_SSLMODE=require`) using Secrets Manager. The site is public
and SELECT-only.

### Daily schedule

EventBridge Scheduler (timezone `America/Sao_Paulo`, GMT-3) invokes each Lambda at
**10:00 and 18:00**, with a default **5-minute stagger** so the shared NAT instance
and RDS are not hit by all four scrapers at once:

| Function | 10h slot | 18h slot |
| --- | --- | --- |
| `ecoagro` | 10:00 | 18:00 |
| `opea` | 10:05 | 18:05 |
| `riza` | 10:10 | 18:10 |
| `vert` | 10:15 | 18:15 |

Daily runs only invoke the existing handlers (idempotent upserts). Schema is **not**
auto-created on Lambda (`AUTO_CREATE_SCHEMA=false`); create tables once with
`scripts/init_db.py` (or the first local backfill).

The **initial full backfill** is still best run with `scripts/run_local.py` (no
15-minute Lambda limit). After that, these incremental scheduled invocations stay
well within the limit.

Override at deploy time (or in `infra/cdk.json`):

```bash
# Disable all schedules (manual invoke only)
cdk deploy --all -c schedules_enabled=false

# Fire all four at exactly 10:00 and 18:00 (no stagger)
cdk deploy --all -c schedule_stagger_minutes=0
```

You can also disable individual schedules in the EventBridge Scheduler console.
Manual invoke still works at any time:

```bash
aws lambda invoke --function-name br-sec-scrapers-ecoagro /tmp/ecoagro.json
```

## Document catalog

The catalog lists rows from `documentos` joined to `emissoes`:

| Filter / column | Database field |
| --- | --- |
| Company | `emissoes.devedor`, falling back to `emissoes.operacao` |
| Date | `documentos.data_documento` |
| Securitization company | `documentos.fonte` |
| Document type | `documentos.tipo_documento` |

List rows show company, date, and document type. Clicking a row opens a detail sheet
with the document URL and remaining fields. Local development uses
`python web/api/local.py` (port 8081) plus `python -m http.server 8080 --directory web`.

## Incremental & re-check behavior

- Discovery upserts the emission list; new operations start with `detalhes_coletados = false`.
- Every run also re-opens existing operations (oldest `ultima_verificacao_detalhe` first)
  and re-parses the full document list, so **new documents on old emissions are captured**.
- All writes are idempotent upserts; documents dedupe by `link_documento`.

## Legal / operational note

These are public financial-disclosure pages. Scraping here is throttled and read-only,
but review each site's Terms of Service before running in production.
