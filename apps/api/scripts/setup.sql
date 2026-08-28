-- =====================================================================
-- Questkart — database setup (PostgreSQL 16)
-- Matches the Data Model & Schema Design doc.
-- Run once on a fresh database:
--     psql "$QK_DATABASE_URL" -f schema.sql
-- =====================================================================

-- ---------- 0. Extensions & schemas ----------
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: embeddings
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS citext;      -- case-insensitive email

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS runs;
CREATE SCHEMA IF NOT EXISTS platform;

-- Resolve unqualified names against our schemas, then public (vector type, functions).
-- Persist per database:  ALTER DATABASE <db> SET search_path = core, runs, platform, public;
SET search_path = core, runs, platform, public;

-- ---------- 1. Shared trigger: keep updated_at current ----------
CREATE OR REPLACE FUNCTION public.set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =====================================================================
-- SCHEMA core — setup & identity
-- =====================================================================

-- users: a mirror of Microsoft Entra ID (JIT-provisioned; keyed by the oid claim)
CREATE TABLE core.users (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid,
    auth_provider_id text        UNIQUE,                 -- Entra object id (oid)
    email            citext      NOT NULL UNIQUE,
    name             text        NOT NULL,
    role             text        NOT NULL DEFAULT 'operator'
                                   CHECK (role IN ('operator','admin')),
    last_login_at    timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.domains (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid,
    name               text        NOT NULL,
    type               text        NOT NULL CHECK (type IN ('predefined','custom')),
    description        text,
    compliance_profile text        NOT NULL DEFAULT 'standard'
                                     CHECK (compliance_profile IN ('standard','strict')),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_domains_tenant_type ON core.domains (tenant_id, type);

CREATE TABLE core.studies (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid,
    domain_id         uuid        NOT NULL REFERENCES core.domains(id) ON DELETE RESTRICT,
    owner_id          uuid        NOT NULL REFERENCES core.users(id)   ON DELETE RESTRICT,
    name              text        NOT NULL,
    description       text,
    intent            jsonb,
    outcome_dimension text,
    scale_min         integer     DEFAULT 1,
    scale_max         integer     DEFAULT 5,
    status            text        NOT NULL DEFAULT 'draft'
                                    CHECK (status IN ('draft','ready','archived')),
    expires_at        timestamptz,
    deleted_at        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_studies_owner_created ON core.studies (owner_id, created_at DESC, id DESC);
CREATE INDEX idx_studies_domain        ON core.studies (domain_id);
CREATE INDEX idx_studies_expires       ON core.studies (expires_at);

CREATE TABLE core.anchors (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid,
    scope_type  text        NOT NULL CHECK (scope_type IN ('domain','study')),
    scope_id    uuid        NOT NULL,     -- app-enforced ref to domains.id or studies.id
    scale_point integer     NOT NULL,
    text        text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_anchor_scope_point UNIQUE (scope_type, scope_id, scale_point)
);
CREATE INDEX idx_anchors_scope ON core.anchors (scope_type, scope_id);

CREATE TABLE core.sources (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid,
    study_id           uuid        NOT NULL REFERENCES core.studies(id) ON DELETE CASCADE,
    filename           text        NOT NULL,
    content_type       text        NOT NULL,
    size_bytes         bigint      NOT NULL DEFAULT 0,
    -- Stored in-database rather than in object storage (R2/S3) — a
    -- deliberate call for now, not the architecture doc's original plan.
    content            bytea       NOT NULL,
    priority           text        NOT NULL DEFAULT 'medium'
                                     CHECK (priority IN ('high','medium','low')),
    suggested_priority text        CHECK (suggested_priority IN ('high','medium','low')),
    ingest_status      text        NOT NULL DEFAULT 'pending'
                                     CHECK (ingest_status IN ('pending','processing','ready','failed')),
    summary            text,
    tags               jsonb,
    pii_flag           boolean     NOT NULL DEFAULT false,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_sources_study ON core.sources (study_id);

-- source_chunks: the vector store (pgvector). D = 1536 — set to your embedding model's dimension.
CREATE TABLE core.source_chunks (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid,
    source_id   uuid        NOT NULL REFERENCES core.sources(id) ON DELETE CASCADE,
    study_id    uuid        NOT NULL REFERENCES core.studies(id) ON DELETE CASCADE,
    position    integer     NOT NULL,
    text        text        NOT NULL,
    embedding   vector(1536),
    token_count integer,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_chunks_source ON core.source_chunks (source_id);
CREATE INDEX idx_chunks_study  ON core.source_chunks (study_id);
CREATE INDEX idx_chunks_embedding ON core.source_chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE TABLE core.messages (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid,
    study_id   uuid        NOT NULL REFERENCES core.studies(id) ON DELETE CASCADE,
    text       text        NOT NULL,
    "group"    text,
    version    integer     NOT NULL DEFAULT 1,
    position   integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_study ON core.messages (study_id);

CREATE TABLE core.avatars (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid,
    scope      text        NOT NULL CHECK (scope IN ('library','study')),
    domain_id  uuid        REFERENCES core.domains(id) ON DELETE RESTRICT,
    study_id   uuid        REFERENCES core.studies(id) ON DELETE CASCADE,
    name       text        NOT NULL,
    profile    text        NOT NULL,
    source     text        NOT NULL DEFAULT 'custom'
                             CHECK (source IN ('prebuilt','custom','llm_assisted')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_avatar_scope CHECK (
        (scope = 'library' AND domain_id IS NOT NULL AND study_id IS NULL) OR
        (scope = 'study'   AND study_id  IS NOT NULL)
    )
);
CREATE INDEX idx_avatars_scope_domain ON core.avatars (scope, domain_id);
CREATE INDEX idx_avatars_study        ON core.avatars (study_id);

CREATE TABLE core.study_avatars (
    study_id  uuid        NOT NULL REFERENCES core.studies(id) ON DELETE CASCADE,
    avatar_id uuid        NOT NULL REFERENCES core.avatars(id) ON DELETE RESTRICT,
    added_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (study_id, avatar_id)
);
CREATE INDEX idx_study_avatars_avatar ON core.study_avatars (avatar_id);

-- =====================================================================
-- SCHEMA platform — cross-cutting operational & financial records
-- (provider_keys and jobs are created before runs, which reference them)
-- =====================================================================

CREATE TABLE platform.provider_keys (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid,
    owner_id   uuid        NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
    provider   text        NOT NULL,
    label      text,
    secret_ref text        NOT NULL,   -- handle in secrets manager (KMS) — NOT the raw key
    last4      text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_provider_keys_owner ON platform.provider_keys (owner_id, provider);

CREATE TABLE platform.jobs (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid,
    kind          text        NOT NULL CHECK (kind IN ('export','reindex')),
    status        text        NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued','running','succeeded','failed')),
    resource_type text        CHECK (resource_type IN ('run','study')),
    resource_id   uuid,
    result_url    text,
    error         jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_jobs_resource ON platform.jobs (resource_type, resource_id);
CREATE INDEX idx_jobs_status   ON platform.jobs (status);

-- =====================================================================
-- SCHEMA runs — execution & results
-- =====================================================================

CREATE TABLE runs.runs (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid,
    study_id        uuid        NOT NULL REFERENCES core.studies(id) ON DELETE CASCADE,
    status          text        NOT NULL DEFAULT 'draft' CHECK (status IN
                                  ('draft','configured','estimated','approved','queued','running',
                                   'awaiting_review','finalized','failed','cancelled','expired')),
    config_snapshot jsonb,
    model_config    jsonb,
    provider_key_id uuid        REFERENCES platform.provider_keys(id) ON DELETE RESTRICT,
    estimate        jsonb,
    actuals         jsonb,
    coverage_pct    numeric(5,2),
    workflow_id     text,
    error           jsonb,
    started_at      timestamptz,
    finished_at     timestamptz,
    expires_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_runs_study_created ON runs.runs (study_id, created_at DESC, id DESC);
CREATE INDEX idx_runs_status        ON runs.runs (status);
CREATE INDEX idx_runs_expires       ON runs.runs (expires_at);

CREATE TABLE runs.run_reactions (
    id           uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid,
    run_id       uuid         NOT NULL REFERENCES runs.runs(id)     ON DELETE CASCADE,
    avatar_id    uuid         NOT NULL REFERENCES core.avatars(id)  ON DELETE RESTRICT,
    message_id   uuid         NOT NULL REFERENCES core.messages(id) ON DELETE RESTRICT,
    reaction     text,
    score        numeric(6,4),
    distribution jsonb,
    penalty      numeric(6,4),
    status       text         CHECK (status IN ('ok','failed')),
    embedding    vector(1536),   -- optional: reproducibility/debug (see ADR)
    created_at   timestamptz  NOT NULL DEFAULT now(),
    updated_at   timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_reaction UNIQUE (run_id, avatar_id, message_id)
);
CREATE INDEX idx_reactions_run_msg ON runs.run_reactions (run_id, message_id);

CREATE TABLE runs.run_message_results (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid,
    run_id          uuid        NOT NULL REFERENCES runs.runs(id)     ON DELETE CASCADE,
    message_id      uuid        NOT NULL REFERENCES core.messages(id) ON DELETE RESTRICT,
    aggregate_score numeric(6,4),
    bt_strength     numeric(8,6),
    rank            integer,
    recommendation  text        CHECK (recommendation IN ('recommended','runner_up','drop')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_msg_result UNIQUE (run_id, message_id)
);
CREATE INDEX idx_msg_results_run_rank ON runs.run_message_results (run_id, rank);

CREATE TABLE runs.run_reports (
    run_id            uuid        PRIMARY KEY REFERENCES runs.runs(id) ON DELETE CASCADE,
    tenant_id         uuid,
    report            text,
    baseline_lift_pct numeric(6,2),
    summary           jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE runs.exports (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid,
    run_id      uuid        NOT NULL REFERENCES runs.runs(id) ON DELETE CASCADE,
    format      text        NOT NULL CHECK (format IN ('markdown','pdf','docx','pptx')),
    storage_ref text,
    template_id uuid,
    job_id      uuid        REFERENCES platform.jobs(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_exports_run ON runs.exports (run_id);

-- ---------- platform tables that reference runs ----------
CREATE TABLE platform.usage_events (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid,
    run_id          uuid        REFERENCES runs.runs(id) ON DELETE SET NULL,
    owner_id        uuid        NOT NULL REFERENCES core.users(id) ON DELETE RESTRICT,
    provider        text        NOT NULL,
    model           text,
    provider_key_id uuid        REFERENCES platform.provider_keys(id) ON DELETE SET NULL,
    byo_key         boolean     NOT NULL DEFAULT false,
    tokens_in       integer,
    tokens_out      integer,
    cost_credits    numeric(14,4),
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_usage_owner_time ON platform.usage_events (owner_id, occurred_at);
CREATE INDEX idx_usage_run        ON platform.usage_events (run_id);

CREATE TABLE platform.credit_ledger (
    id         uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid,
    owner_id   uuid          NOT NULL REFERENCES core.users(id) ON DELETE RESTRICT,
    run_id     uuid          REFERENCES runs.runs(id) ON DELETE SET NULL,
    delta      numeric(14,4) NOT NULL,   -- negative = consumed, positive = grant/top-up
    reason     text          NOT NULL,
    byo_key    boolean       NOT NULL DEFAULT false,
    created_at timestamptz    NOT NULL DEFAULT now(),
    updated_at timestamptz    NOT NULL DEFAULT now()
);
CREATE INDEX idx_ledger_owner_created ON platform.credit_ledger (owner_id, created_at);

CREATE TABLE platform.audit_log (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid,
    actor_id    uuid        REFERENCES core.users(id) ON DELETE SET NULL,
    action      text        NOT NULL,
    target_type text,
    target_id   uuid,
    metadata    jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_actor_time ON platform.audit_log (actor_id, occurred_at);
CREATE INDEX idx_audit_target     ON platform.audit_log (target_type, target_id);

-- =====================================================================
-- 2. updated_at triggers (all tables)
-- =====================================================================
CREATE TRIGGER trg_users_updated           BEFORE UPDATE ON core.users               FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_domains_updated         BEFORE UPDATE ON core.domains             FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_studies_updated         BEFORE UPDATE ON core.studies             FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_anchors_updated         BEFORE UPDATE ON core.anchors             FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_sources_updated         BEFORE UPDATE ON core.sources             FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_chunks_updated          BEFORE UPDATE ON core.source_chunks       FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_messages_updated        BEFORE UPDATE ON core.messages            FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_avatars_updated         BEFORE UPDATE ON core.avatars             FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_provider_keys_updated   BEFORE UPDATE ON platform.provider_keys   FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_jobs_updated            BEFORE UPDATE ON platform.jobs            FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_runs_updated            BEFORE UPDATE ON runs.runs                FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_reactions_updated       BEFORE UPDATE ON runs.run_reactions       FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_msg_results_updated     BEFORE UPDATE ON runs.run_message_results FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_reports_updated         BEFORE UPDATE ON runs.run_reports         FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_exports_updated         BEFORE UPDATE ON runs.exports             FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_usage_updated           BEFORE UPDATE ON platform.usage_events    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_ledger_updated          BEFORE UPDATE ON platform.credit_ledger   FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_audit_updated           BEFORE UPDATE ON platform.audit_log       FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- =====================================================================
-- 3. Optional: enforce one active run per user at the DB level
--    (requires denormalizing owner_id onto runs.runs; open question #9)
-- =====================================================================
-- ALTER TABLE runs.runs ADD COLUMN owner_id uuid REFERENCES core.users(id);
-- CREATE UNIQUE INDEX uniq_active_run_per_owner ON runs.runs (tenant_id, owner_id)
--     WHERE status IN ('queued','running','awaiting_review');

-- =====================================================================
-- 4. Optional seed — predefined domains (personas/anchors are seeded by the app)
-- =====================================================================
-- INSERT INTO core.domains (name, type, description) VALUES
--   ('Pharmaceutical Marketing', 'predefined', 'HCP & payer messaging'),
--   ('IT & Enterprise Software',  'predefined', 'IT buyer messaging'),
--   ('Financial Services',        'predefined', 'Consumer & SMB messaging'),
--   ('Consumer / CPG & Retail',   'predefined', 'Shopper messaging');

