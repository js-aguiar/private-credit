-- Authoritative DDL for the Brazilian securitization scrapers database (PostgreSQL).
-- Series-first model: series is the primary entity (emissoes dropped).
-- Business keys: UNIQUE(isin) and UNIQUE(codigo_cetip) when present (NULLs allowed).
-- Documents attach to many series via documentos_series.
-- Breaking schema reset (local/dev and re-backfill). Existing data must be re-scraped.
-- Do not put semicolons in comments: ensure_schema splits on semicolon.
DROP TABLE IF EXISTS documentos_series CASCADE;
DROP TABLE IF EXISTS documentos CASCADE;
DROP TABLE IF EXISTS series CASCADE;
DROP TABLE IF EXISTS emissoes CASCADE;
DROP TABLE IF EXISTS isin_contestados CASCADE;

CREATE TABLE IF NOT EXISTS series (
    serie_id                    BIGSERIAL PRIMARY KEY,
    fonte                       VARCHAR(30)  NOT NULL,
    id_origem                   VARCHAR(255) NOT NULL,
    link                        TEXT,
    -- Business keys (each UNIQUE when present, both missing means skip at upsert)
    isin                        VARCHAR(20),
    codigo_cetip                VARCHAR(30),
    -- Source emission grouping (string, not an FK)
    emissao_id                  VARCHAR(255),
    numero_emissao              VARCHAR(50),
    numero_serie                VARCHAR(50)  NOT NULL DEFAULT '',
    operacao                    TEXT,
    devedor                     TEXT,
    ano_emissao                 INTEGER,
    tipo_ativo                  VARCHAR(50),
    series_raw                  TEXT,
    valor_total                 NUMERIC(20, 2),
    valor                       NUMERIC(20, 2),
    remuneracao                 TEXT,
    indexador                   VARCHAR(120),
    data_emissao                DATE,
    data_vencimento             DATE,
    quantidade                  BIGINT,
    rating                      VARCHAR(80),
    data_scraping               TIMESTAMPTZ,
    detalhes_coletados          BOOLEAN      NOT NULL DEFAULT FALSE,
    ultima_verificacao          TIMESTAMPTZ,
    extras                      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    criado_em                   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    atualizado_em               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_series_fonte_id_origem UNIQUE (fonte, id_origem),
    CONSTRAINT uq_series_isin UNIQUE (isin),
    CONSTRAINT uq_series_codigo_cetip UNIQUE (codigo_cetip)
);

CREATE INDEX IF NOT EXISTS ix_series_numero_emissao ON series (numero_emissao);
CREATE INDEX IF NOT EXISTS ix_series_emissao_id ON series (emissao_id);
CREATE INDEX IF NOT EXISTS ix_series_devedor ON series (devedor);
CREATE INDEX IF NOT EXISTS ix_series_recheck
    ON series (fonte, detalhes_coletados, ultima_verificacao);

CREATE TABLE IF NOT EXISTS documentos (
    documento_id       BIGSERIAL PRIMARY KEY,
    fonte              VARCHAR(30)  NOT NULL,
    -- Denormalized source grouping / linking helpers (not FKs)
    emissao_id         VARCHAR(255),
    isin               VARCHAR(20),
    numero_emissao     VARCHAR(50),
    codigo_cetip       VARCHAR(30),
    titulo             TEXT,
    tipo_documento     VARCHAR(120),
    link_documento     TEXT         NOT NULL,
    id_origem_arquivo  VARCHAR(255),
    data_documento     DATE,
    data_insercao      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    extras             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    criado_em          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    atualizado_em      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_documentos_fonte_link UNIQUE (fonte, link_documento)
);

CREATE INDEX IF NOT EXISTS ix_documentos_emissao_id ON documentos (emissao_id);
CREATE INDEX IF NOT EXISTS ix_documentos_isin ON documentos (isin);
CREATE INDEX IF NOT EXISTS ix_documentos_fonte ON documentos (fonte);
CREATE INDEX IF NOT EXISTS ix_documentos_tipo ON documentos (tipo_documento);
CREATE INDEX IF NOT EXISTS ix_documentos_data ON documentos (data_documento);
CREATE UNIQUE INDEX IF NOT EXISTS uq_documentos_fonte_id_origem_arquivo
    ON documentos (fonte, id_origem_arquivo)
    WHERE id_origem_arquivo IS NOT NULL;

-- Many-to-many: one document can attach to multiple séries.
CREATE TABLE IF NOT EXISTS documentos_series (
    documento_id BIGINT NOT NULL REFERENCES documentos (documento_id) ON DELETE CASCADE,
    serie_id     BIGINT NOT NULL REFERENCES series (serie_id) ON DELETE CASCADE,
    PRIMARY KEY (documento_id, serie_id)
);

CREATE INDEX IF NOT EXISTS ix_documentos_series_serie_id ON documentos_series (serie_id);

-- Contested business keys observed during scrapes (audit / diagnostics).
CREATE TABLE IF NOT EXISTS isin_contestados (
    isin         VARCHAR(20) PRIMARY KEY,
    fonte        VARCHAR(64),
    detectado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
