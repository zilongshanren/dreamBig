-- =============================================================================
-- Phase 1 iteration migration: review pipeline + LLM reports + alert severity
-- =============================================================================
-- Run after ensuring the pg_trgm / btree_gin / vector extensions are available
-- (see scripts/init-db.sql). This file is idempotent — safe to re-run.
--
-- To apply:
--   psql $DATABASE_URL -f web/prisma/migrations/20260405_phase1_iteration.sql
-- Or (in dev):
--   cd web && npx prisma db push   (will diff against schema.prisma)
-- =============================================================================

-- Ensure pgvector is available (idempotent)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- =============================================================================
-- 1. Alert extensions: alertType + severity
-- =============================================================================
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS alert_type TEXT NOT NULL DEFAULT 'ranking_jump';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS severity TEXT NOT NULL DEFAULT 'P2';

ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS alert_type TEXT;
ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS severity TEXT;
ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS reason TEXT;

-- =============================================================================
-- 2. Game structured fields (LLM-generated gameplay teardown)
-- =============================================================================
ALTER TABLE games ADD COLUMN IF NOT EXISTS positioning TEXT;
ALTER TABLE games ADD COLUMN IF NOT EXISTS core_loop TEXT;
ALTER TABLE games ADD COLUMN IF NOT EXISTS meta_loop TEXT;
ALTER TABLE games ADD COLUMN IF NOT EXISTS pleasure_points TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE games ADD COLUMN IF NOT EXISTS replay_drivers TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE games ADD COLUMN IF NOT EXISTS iaa_grade TEXT;

-- =============================================================================
-- 3. New table: reviews
-- =============================================================================
CREATE TABLE IF NOT EXISTS reviews (
    id SERIAL PRIMARY KEY,
    platform_listing_id INTEGER NOT NULL REFERENCES platform_listings(id),
    external_id TEXT NOT NULL,
    rating SMALLINT,
    content TEXT NOT NULL,
    author_name TEXT,
    helpful_count INTEGER,
    language TEXT,
    posted_at TIMESTAMP NOT NULL,
    sentiment TEXT,
    topics TEXT[] NOT NULL DEFAULT '{}',
    sentiment_confidence DECIMAL(3,2),
    scraped_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT reviews_listing_external_uniq UNIQUE (platform_listing_id, external_id)
);

CREATE INDEX IF NOT EXISTS reviews_listing_posted_idx
    ON reviews (platform_listing_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS reviews_posted_idx
    ON reviews (posted_at DESC);

-- =============================================================================
-- 4. New table: review_topic_summaries
-- =============================================================================
CREATE TABLE IF NOT EXISTS review_topic_summaries (
    id SERIAL PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id),
    topic TEXT NOT NULL,
    sentiment TEXT NOT NULL,           -- positive | negative
    sample_review_ids INTEGER[] NOT NULL DEFAULT '{}',
    snippet TEXT NOT NULL,
    review_count INTEGER NOT NULL,
    computed_at DATE NOT NULL,
    CONSTRAINT review_topic_summaries_uniq
        UNIQUE (game_id, topic, sentiment, computed_at)
);

CREATE INDEX IF NOT EXISTS review_topic_summaries_game_idx
    ON review_topic_summaries (game_id, computed_at DESC);

-- =============================================================================
-- 5. New table: game_reports (one-per-game LLM teardown report)
-- =============================================================================
CREATE TABLE IF NOT EXISTS game_reports (
    id SERIAL PRIMARY KEY,
    game_id INTEGER NOT NULL UNIQUE REFERENCES games(id),
    payload JSONB NOT NULL,
    prompt_version TEXT NOT NULL,
    model_used TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    confidence DECIMAL(3,2) NOT NULL,
    tokens_used INTEGER,
    cost_usd DECIMAL(10,4),
    generated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS game_reports_generated_idx
    ON game_reports (generated_at DESC);

-- =============================================================================
-- 6. New table: game_embeddings (pgvector for similarity search)
-- =============================================================================
CREATE TABLE IF NOT EXISTS game_embeddings (
    game_id INTEGER PRIMARY KEY REFERENCES games(id),
    embedding vector(1536) NOT NULL,
    source TEXT NOT NULL,
    dim INTEGER NOT NULL DEFAULT 1536,
    computed_at TIMESTAMP NOT NULL
);

-- Optional: IVFFlat index for approximate nearest-neighbour queries.
-- Build once the table has a few hundred rows; tune `lists` to sqrt(rowcount).
-- CREATE INDEX IF NOT EXISTS game_embeddings_ivfflat_idx
--     ON game_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- =============================================================================
-- End of migration
-- =============================================================================
