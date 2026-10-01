# BR Securitization Scrapers

Daily, incremental scrapers for five Brazilian securitization ("securitizadoras") websites.
Each run **discovers new séries** and **re-checks already-scraped séries for updated
information** — especially newly published documents.

Extracted data is stored in a single **Amazon RDS for PostgreSQL** database (Portuguese
column names matching the sites): `series` (primary entity), `documentos`, and the
`documentos_series` many-to-many junction. Rows require an `isin` and/or `codigo_cetip`
(UNIQUE when present); keyless or conflicting rows are skipped with a warning.

Infrastructure is defined with **AWS CDK (Python)**; each scraper runs as an **AWS
Lambda container image**. After an initial local backfill, **EventBridge Scheduler**
invokes the Lambdas twice daily at 10:00 and 18:00 America/Sao_Paulo (GMT-3).

## Target sites

| Source (`fonte`) | URL | Rendering | Strategy |
| --- | --- | --- | --- |
| `ecoagro` | https://ecoagro.agr.br/emissoes | Server-rendered HTML (paginated) | `httpx` + BeautifulSoup |
| `opea` | https://app.opea.com.br/pt/emissoes | Vue/Vite SPA (JSON API) | API-first, Playwright fallback |
| `riza` | https://investidor.rizasec.com/emissoes | Next.js SPA (JSON API) | Virgo BFF API via httpx |
| `vert` | https://data.vert-capital.app/ | React-Router SPA (JSON API) | API-first, Playwright fallback |
| `bari` | https://barisec.com.br/emissoes | Next.js SSG + Strapi CMS API | `httpx` (Strapi list + SSG detail JSON) |

## Repository layout

```
br-securitization-scrapers/
  shared/            # Shared library used by every scraper
  scrapers/          # One folder per website (Lambda handler + Dockerfile)
    ecoagro/
    opea/
    riza/
    vert/
    bari/
  infra/             # AWS CDK (Python) app and stacks
  scripts/           # Local DB init + time-unlimited local runner
  web/               # Document catalog UI (static) + read-only API Lambda
```

## Data model

`series` is the primary scraped entity. Business uniqueness is `UNIQUE(isin)` and
`UNIQUE(codigo_cetip)` (NULLs allowed). A stable surrogate `serie_id` is the PK;
`(fonte, id_origem)` is the per-source upsert key. Source emission grouping is a
denormalized string `emissao_id` (not an FK). Documents attach to one or more séries
via `documentos_series`. A `extras` JSONB column stores site-specific fields.

- `series` — one row per série (ISIN/CETIP, emission context, scrape metadata).
- `documentos` — one row per document (title, link, document date, insertion date, ...).
- `documentos_series` — M2M links between documents and séries.

See [`shared/schema.sql`](shared/schema.sql) for the authoritative DDL. Schema resets are
destructive — re-run `python scripts/init_db.py` and re-backfill after upgrading.

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
`db.t4g.micro` (single-AZ, private), five Lambda container functions (built from
`scrapers/*/Dockerfile`), IAM roles, SSM parameters, CloudWatch log groups + error
alarms, an SNS topic for alerts, **EventBridge Scheduler** schedules that invoke
each scraper twice daily, an **EC2 backfill launch template** (one-off full catalog
runs), and a **document catalog** (private S3 + CloudFront HTTPS + API Gateway HTTP
API + VPC Lambda).

The catalog URL is the `CatalogUrl` CloudFormation output
(`https://<distribution>.cloudfront.net`). The browser never talks to RDS: CloudFront
serves the static UI and proxies `/api/*` to a read-only Lambda that connects to
Postgres over TLS (`DB_SSLMODE=require`) using Secrets Manager. The site is public
and SELECT-only.

### Daily schedule

EventBridge Scheduler (timezone `America/Sao_Paulo`, GMT-3) invokes each Lambda at
**10:00 and 18:00**, with a default **5-minute stagger** so the shared NAT instance
and RDS are not hit by all five scrapers at once:

| Function | 10h slot | 18h slot |
| --- | --- | --- |
| `ecoagro` | 10:00 | 18:00 |
| `opea` | 10:05 | 18:05 |
| `riza` | 10:10 | 18:10 |
| `vert` | 10:15 | 18:15 |
| `bari` | 10:20 | 18:20 |

Daily runs only invoke the existing handlers (idempotent upserts). Schema is **not**
auto-created on Lambda (`AUTO_CREATE_SCHEMA=false`); create tables once with
`scripts/init_db.py` (or the first local backfill).

The **initial full backfill** can run on a one-off **EC2 instance** (recommended for
production RDS) or locally with `scripts/run_local.py` (no 15-minute Lambda limit).
After that, scheduled Lambda invocations stay within the limit and finish any rows
the backfill did not reach.

### EC2 full backfill

The `br-sec-scrapers-backfill` stack defines a launch template (default **`t4g.small`**,
overridable via `-c backfill_instance_type=...`), backfill-specific SSM tunables under
`/{prefix}/backfill/` (default **2s** delay, **20** req/min — faster than Lambda but
still polite), and a CloudWatch log group `/{prefix}/ec2-backfill`.

Deploy the stack (included in `cdk deploy --all`):

```bash
cd infra && cdk deploy br-sec-scrapers-backfill
```

Launch a backfill run (does **not** start automatically on deploy):

```bash
python scripts/launch_ec2_backfill.py
aws logs tail /br-sec-scrapers/ec2-backfill --follow
```

The instance runs all five scrapers sequentially via `scripts/run_backfill_all.py`,
connects to RDS using Secrets Manager, logs JSON lines with `execution_mode=ec2_backfill`
and a shared `run_id`, then **terminates itself** when finished or after **24 hours**
(configurable with `-c backfill_max_hours=24`). Root EBS is deleted on termination;
any extra attached volumes are explicitly deleted in `scripts/ec2_backfill_teardown.sh`.

If the deadline is hit before every emission has details, **scheduled Lambdas** continue
incrementally using the same detail queue (`detalhes_coletados=false` first).

Override at deploy time (or in `infra/cdk.json`):

```bash
# Disable all schedules (manual invoke only)
cdk deploy --all -c schedules_enabled=false

# Fire all scrapers at exactly 10:00 and 18:00 (no stagger)
cdk deploy --all -c schedule_stagger_minutes=0
```

You can also disable individual schedules in the EventBridge Scheduler console.
Manual invoke still works at any time:

```bash
aws lambda invoke --function-name br-sec-scrapers-ecoagro /tmp/ecoagro.json
```

## Document / series catalog

Home (`/`) lists **séries** (`/api/series`). `/documentos` lists documents joined through
`documentos_series` → `series`:

| Filter / column | Database field |
| --- | --- |
| Company | `series.devedor`, falling back to `series.operacao` |
| Date | `documentos.data_documento` |
| Securitization company | `documentos.fonte` / `series.fonte` |
| Document type | `documentos.tipo_documento` |
| CETIP / ISIN | `series.codigo_cetip` / `series.isin` |

Local development uses `python web/api/local.py` (port 8081) plus
`python -m http.server 8080 --directory web`.

## Incremental & re-check behavior

- Discovery upserts séries that already have ISIN and/or CETIP; new rows start with
  `detalhes_coletados = false`. Keyless or conflicting keys are skipped with a warning.
- Every run also re-opens existing séries (oldest `ultima_verificacao` first) and
  re-parses documents, so **new documents on old séries are captured**.
- All writes are idempotent upserts. Documents dedupe by `(fonte, id_origem_arquivo)`
  (when set) or `(fonte, link_documento)`, then attach to one or more séries via
  `documentos_series`.
- To remove existing Opea duplicates: `python3 scripts/dedupe_opea_documents.py` (or invoke
  the Opea Lambda with `{"action": "dedupe_opea_documents"}`).

## Legal / operational note

These are public financial-disclosure pages. Scraping here is throttled and read-only,
but review each site's Terms of Service before running in production.
