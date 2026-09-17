-- Authoritative DDL for the Brazilian securitization scrapers database (PostgreSQL).
-- Table/column names are in Portuguese to match the source websites' terminology.
-- Business linking keys (isin, numero_emissao, codigo_cetip) are carried on every table.

CREATE TABLE IF NOT EXISTS emissoes (
    emissao_id                  BIGSERIAL PRIMARY KEY,
    fonte                       VARCHAR(30)  NOT NULL,
    id_origem                   VARCHAR(255) NOT NULL,
    link                        TEXT,
    isin                        VARCHAR(20),
    numero_emissao              VARCHAR(50),
    codigos_cetip               TEXT,
    operacao                    TEXT,
    devedor                     TEXT,
    ano_emissao                 INTEGER,
    tipo_ativo                  VARCHAR(50),
    series                      TEXT,               -- raw série list as shown (e.g. "1-2-3")
    valor_total                 NUMERIC(20, 2),
    indexador                   VARCHAR(120),
    data_emissao                DATE,
    data_vencimento             DATE,
    rating                      VARCHAR(80),
    data_scraping               TIMESTAMPTZ,
    detalhes_coletados          BOOLEAN     NOT NULL DEFAULT FALSE,
    ultima_verificacao_detalhe  TIMESTAMPTZ,
    extras                      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    criado_em                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_emissoes_fonte_id_origem UNIQUE (fonte, id_origem)
);

CREATE INDEX IF NOT EXISTS ix_emissoes_isin           ON emissoes (isin);
CREATE INDEX IF NOT EXISTS ix_emissoes_numero_emissao ON emissoes (numero_emissao);
CREATE INDEX IF NOT EXISTS ix_emissoes_recheck
    ON emissoes (fonte, detalhes_coletados, ultima_verificacao_detalhe);

CREATE TABLE IF NOT EXISTS series (
    serie_id        BIGSERIAL PRIMARY KEY,
    emissao_id      BIGINT      NOT NULL REFERENCES emissoes (emissao_id) ON DELETE CASCADE,
    fonte           VARCHAR(30) NOT NULL,
    isin            VARCHAR(20),
    numero_emissao  VARCHAR(50),
    numero_serie    VARCHAR(50) NOT NULL DEFAULT '',
    codigo_cetip    VARCHAR(30),
    valor           NUMERIC(20, 2),
    remuneracao     TEXT,
    indexador       VARCHAR(120),
    data_emissao    DATE,
    data_vencimento DATE,
    quantidade      BIGINT,
    rating          VARCHAR(80),
    extras          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_series_isin           UNIQUE (isin),
    CONSTRAINT uq_series_emissao_numero UNIQUE (emissao_id, numero_serie)
);

CREATE INDEX IF NOT EXISTS ix_series_emissao_id ON series (emissao_id);

CREATE TABLE IF NOT EXISTS documentos (
    documento_id   BIGSERIAL PRIMARY KEY,
    emissao_id     BIGINT      NOT NULL REFERENCES emissoes (emissao_id) ON DELETE CASCADE,
    fonte          VARCHAR(30) NOT NULL,
    isin           VARCHAR(20),
    numero_emissao VARCHAR(50),
    codigo_cetip   VARCHAR(30),
    titulo             TEXT,
    tipo_documento     VARCHAR(120),
    link_documento     TEXT        NOT NULL,
    id_origem_arquivo  VARCHAR(255),
    data_documento DATE,
    data_insercao  TIMESTAMPTZ NOT NULL DEFAULT now(),
    extras         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_documentos_emissao_link UNIQUE (emissao_id, link_documento)
);

CREATE INDEX IF NOT EXISTS ix_documentos_emissao_id ON documentos (emissao_id);
CREATE INDEX IF NOT EXISTS ix_documentos_isin       ON documentos (isin);
CREATE INDEX IF NOT EXISTS ix_documentos_fonte      ON documentos (fonte);
CREATE INDEX IF NOT EXISTS ix_documentos_tipo       ON documentos (tipo_documento);
CREATE INDEX IF NOT EXISTS ix_documentos_data       ON documentos (data_documento);
CREATE INDEX IF NOT EXISTS ix_emissoes_devedor      ON emissoes (devedor);
CREATE UNIQUE INDEX IF NOT EXISTS uq_documentos_fonte_id_origem_arquivo
    ON documentos (fonte, id_origem_arquivo)
    WHERE id_origem_arquivo IS NOT NULL;

-- ISINs that appeared on more than one série (bad source data). Remembered so re-scrapes
-- do not resurrect the value on only one side.
CREATE TABLE IF NOT EXISTS isin_contestados (
    isin         VARCHAR(20) PRIMARY KEY,
    fonte        VARCHAR(64),
    detectado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

