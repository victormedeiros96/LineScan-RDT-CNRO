-- RDT01 — schema relacional inicial (PostgreSQL 14+)
-- Rascunho da tarefa RDT1-8. Deve ser convertido em revisão Alembic na RDT1-9.
-- Ver ERD e decisões em Paca Docs: "Schema e ERD do Banco de Dados".

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ───────────────────────────── projeto ─────────────────────────────
CREATE TABLE projeto (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nome                  text NOT NULL,
    rodovia               text,
    trecho                text,
    modelos_selecionados  jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz
);

CREATE UNIQUE INDEX ux_projeto_nome_ativo
    ON projeto (lower(nome))
    WHERE deleted_at IS NULL;

CREATE TRIGGER trg_projeto_updated_at
    BEFORE UPDATE ON projeto
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ───────────────────────────── viagem ──────────────────────────────
CREATE TABLE viagem (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projeto_id  uuid NOT NULL REFERENCES projeto (id) ON DELETE RESTRICT,
    nome        text NOT NULL,
    pasta       text,
    km_inicial  numeric(10, 3),
    km_final    numeric(10, 3),
    tipo_pista  text NOT NULL DEFAULT 'simples'
        CHECK (tipo_pista IN ('simples', 'dupla')),
    sentido     text NOT NULL DEFAULT 'crescente'
        CHECK (sentido IN ('crescente', 'decrescente')),
    faixa       smallint CHECK (faixa IN (1, 2)),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz,
    CONSTRAINT ck_viagem_faixa_tipo
        CHECK (tipo_pista = 'dupla' OR faixa IS NULL),
    CONSTRAINT ck_viagem_km
        CHECK (km_inicial IS NULL OR km_final IS NULL OR km_final >= km_inicial)
);

CREATE UNIQUE INDEX ux_viagem_projeto_nome_ativo
    ON viagem (projeto_id, lower(nome))
    WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX ux_viagem_projeto_pasta
    ON viagem (projeto_id, pasta)
    WHERE deleted_at IS NULL AND pasta IS NOT NULL;

CREATE INDEX ix_viagem_projeto ON viagem (projeto_id);

CREATE TRIGGER trg_viagem_updated_at
    BEFORE UPDATE ON viagem
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ───────────────────────────── bloco ──────────────────────────────
CREATE TABLE bloco (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    viagem_id   uuid NOT NULL REFERENCES viagem (id) ON DELETE CASCADE,
    indice      integer NOT NULL CHECK (indice >= 0),
    lado        text NOT NULL DEFAULT 'left'
        CHECK (lado IN ('left', 'right')),
    km_inicio   numeric(10, 3),
    km_fim      numeric(10, 3),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz,
    CONSTRAINT uq_bloco_id_viagem UNIQUE (id, viagem_id),
    CONSTRAINT ck_bloco_km
        CHECK (km_inicio IS NULL OR km_fim IS NULL OR km_fim >= km_inicio)
);

CREATE UNIQUE INDEX ux_bloco_viagem_indice
    ON bloco (viagem_id, indice)
    WHERE deleted_at IS NULL;

CREATE INDEX ix_bloco_viagem_km ON bloco (viagem_id, km_inicio, km_fim);

CREATE TRIGGER trg_bloco_updated_at
    BEFORE UPDATE ON bloco
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ───────────────────────────── faixa ──────────────────────────────
CREATE TABLE faixa (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    viagem_id             uuid NOT NULL,
    bloco_id              uuid NOT NULL,
    faixa_index           smallint NOT NULL CHECK (faixa_index >= 0),
    arquivo               text NOT NULL,
    km_inicio             numeric(10, 3),
    km_fim                numeric(10, 3),
    altura_px             integer CHECK (altura_px IS NULL OR altura_px > 0),
    imagens_raw           jsonb,
    offset_y_px           integer,
    percentual_ultima_img numeric(6, 3)
        CHECK (percentual_ultima_img IS NULL OR percentual_ultima_img BETWEEN 0 AND 100),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz,
    CONSTRAINT fk_faixa_bloco
        FOREIGN KEY (bloco_id, viagem_id)
        REFERENCES bloco (id, viagem_id)
        ON DELETE CASCADE,
    CONSTRAINT ck_faixa_km
        CHECK (km_inicio IS NULL OR km_fim IS NULL OR km_fim >= km_inicio)
);

CREATE UNIQUE INDEX ux_faixa_bloco_index
    ON faixa (bloco_id, faixa_index)
    WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX ux_faixa_viagem_arquivo
    ON faixa (viagem_id, arquivo)
    WHERE deleted_at IS NULL;

CREATE INDEX ix_faixa_viagem_km ON faixa (viagem_id, km_inicio, km_fim);

CREATE TRIGGER trg_faixa_updated_at
    BEFORE UPDATE ON faixa
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ──────────────────────────── deteccao ────────────────────────────
CREATE TABLE deteccao (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    faixa_id        uuid NOT NULL REFERENCES faixa (id) ON DELETE CASCADE,
    classe          text NOT NULL,
    area_pixels     integer NOT NULL DEFAULT 0 CHECK (area_pixels >= 0),
    area_m2         numeric(12, 4) CHECK (area_m2 IS NULL OR area_m2 >= 0),
    confidence      real CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    confidence_max  real CHECK (confidence_max IS NULL OR confidence_max BETWEEN 0 AND 1),
    linha           smallint CHECK (linha IS NULL OR linha BETWEEN 0 AND 19),
    coluna          smallint CHECK (coluna IS NULL OR coluna BETWEEN 0 AND 2),
    global_box      jsonb,
    global_polygon  jsonb,
    origem          text NOT NULL DEFAULT 'inferencia'
        CHECK (origem IN ('inferencia', 'manual')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);

CREATE INDEX ix_deteccao_faixa
    ON deteccao (faixa_id)
    WHERE deleted_at IS NULL;

CREATE INDEX ix_deteccao_classe
    ON deteccao (classe)
    WHERE deleted_at IS NULL;

CREATE INDEX ix_deteccao_polygon_gin
    ON deteccao USING gin (global_polygon jsonb_path_ops);

CREATE TRIGGER trg_deteccao_updated_at
    BEFORE UPDATE ON deteccao
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ────────────────────────── parametro_km ──────────────────────────
CREATE TABLE parametro_km (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    viagem_id   uuid NOT NULL REFERENCES viagem (id) ON DELETE CASCADE,
    km          integer NOT NULL CHECK (km >= 0),
    tri_serie   jsonb CHECK (tri_serie IS NULL OR jsonb_typeof(tri_serie) = 'array'),
    tre_serie   jsonb CHECK (tre_serie IS NULL OR jsonb_typeof(tre_serie) = 'array'),
    extras      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz
);

CREATE UNIQUE INDEX ux_parametro_km_viagem_km
    ON parametro_km (viagem_id, km)
    WHERE deleted_at IS NULL;

CREATE TRIGGER trg_parametro_km_updated_at
    BEFORE UPDATE ON parametro_km
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────── job ──────────────────────────────
CREATE TABLE job (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rq_job_id      text UNIQUE,
    viagem_id      uuid REFERENCES viagem (id) ON DELETE SET NULL,
    viagem_nome    text NOT NULL,
    tipo           text NOT NULL
        CHECK (tipo IN ('concatenacao', 'inferencia', 'upload_zip')),
    status         text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'started', 'finished', 'failed', 'canceled')),
    meta           jsonb NOT NULL DEFAULT '{}'::jsonb,
    progresso      jsonb NOT NULL DEFAULT '{}'::jsonb,
    resultado      jsonb,
    erro           text,
    iniciado_em    timestamptz,
    finalizado_em  timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz
);

CREATE INDEX ix_job_viagem ON job (viagem_id);
CREATE INDEX ix_job_status ON job (status);
CREATE INDEX ix_job_created_at ON job (created_at DESC);

CREATE TRIGGER trg_job_updated_at
    BEFORE UPDATE ON job
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ──────────────────────── resultado_analise ───────────────────────
CREATE TABLE resultado_analise (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    viagem_id     uuid NOT NULL REFERENCES viagem (id) ON DELETE CASCADE,
    km            integer NOT NULL CHECK (km >= 0),
    tipo_analise  text NOT NULL CHECK (tipo_analise IN ('igg', 'icp')),
    norma         text,
    valor         numeric(10, 2) NOT NULL,
    conceito      text,
    n_estacoes    integer CHECK (n_estacoes IS NULL OR n_estacoes > 0),
    detalhe       jsonb NOT NULL DEFAULT '{}'::jsonb,
    calculado_em  timestamptz NOT NULL DEFAULT now(),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz
);

CREATE UNIQUE INDEX ux_resultado_viagem_km_tipo
    ON resultado_analise (viagem_id, km, tipo_analise)
    WHERE deleted_at IS NULL;

CREATE INDEX ix_resultado_viagem_tipo_km
    ON resultado_analise (viagem_id, tipo_analise, km)
    WHERE deleted_at IS NULL;

CREATE TRIGGER trg_resultado_analise_updated_at
    BEFORE UPDATE ON resultado_analise
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ────────────────────────── arquivo_upload ────────────────────────
CREATE TABLE arquivo_upload (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    viagem_id         uuid REFERENCES viagem (id) ON DELETE SET NULL,
    viagem_nome       text NOT NULL,
    nome              text NOT NULL,
    caminho_relativo  text NOT NULL,
    tamanho_bytes     bigint CHECK (tamanho_bytes IS NULL OR tamanho_bytes >= 0),
    content_type      text,
    md5               text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz
);

CREATE UNIQUE INDEX ux_arquivo_upload_viagem_nome
    ON arquivo_upload (viagem_nome, nome)
    WHERE deleted_at IS NULL;

CREATE INDEX ix_arquivo_upload_viagem ON arquivo_upload (viagem_id);

CREATE TRIGGER trg_arquivo_upload_updated_at
    BEFORE UPDATE ON arquivo_upload
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
