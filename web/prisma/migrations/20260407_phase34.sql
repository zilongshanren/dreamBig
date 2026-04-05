-- =============================================================================
-- Phase 3/4 migration: Experiments + Asset Analysis + Feishu Bot + Generated Reports
-- =============================================================================
-- This file is idempotent — safe to re-run.
--
-- To apply:
--   psql $DATABASE_URL -f web/prisma/migrations/20260407_phase34.sql
-- Or (in dev):
--   cd web && npx prisma db push   (will diff against schema.prisma)
-- =============================================================================

-- =============================================================================
-- 1. Experiments (P3-3 — A/B test tracker)
-- =============================================================================
CREATE TABLE IF NOT EXISTS experiments (
    id SERIAL PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    variant_a JSONB NOT NULL,
    variant_b JSONB NOT NULL,
    success_metric TEXT NOT NULL,
    sample_size INTEGER,
    status TEXT NOT NULL DEFAULT 'draft',
    priority SMALLINT NOT NULL DEFAULT 3,
    expected_lift DECIMAL(5,2),
    actual_lift DECIMAL(5,2),
    notes TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS experiments_game_created_idx
    ON experiments (game_id, created_at DESC);
CREATE INDEX IF NOT EXISTS experiments_status_idx
    ON experiments (status);

-- =============================================================================
-- 2. Game asset analysis (P4-1 — visual analysis results)
-- =============================================================================
CREATE TABLE IF NOT EXISTS game_asset_analysis (
    id SERIAL PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL,
    asset_url TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    result JSONB NOT NULL,
    model_used TEXT NOT NULL,
    confidence DECIMAL(3,2),
    tokens_used INTEGER,
    cost_usd DECIMAL(10,4),
    analyzed_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS game_asset_analysis_game_type_idx
    ON game_asset_analysis (game_id, asset_type);
CREATE INDEX IF NOT EXISTS game_asset_analysis_type_analyzed_idx
    ON game_asset_analysis (analysis_type, analyzed_at DESC);

-- =============================================================================
-- 3. Feishu bot commands (P4-3 — bot interaction log)
-- =============================================================================
CREATE TABLE IF NOT EXISTS feishu_bot_commands (
    id SERIAL PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    user_open_id TEXT,
    chat_id TEXT,
    command TEXT NOT NULL,
    args TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    response TEXT,
    error_msg TEXT,
    response_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS feishu_bot_commands_status_created_idx
    ON feishu_bot_commands (status, created_at);
CREATE INDEX IF NOT EXISTS feishu_bot_commands_user_created_idx
    ON feishu_bot_commands (user_open_id, created_at DESC);

-- =============================================================================
-- 4. Generated reports (P3-1 weekly / P3-2 batch advice storage)
-- =============================================================================
CREATE TABLE IF NOT EXISTS generated_reports (
    id SERIAL PRIMARY KEY,
    report_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    payload JSONB NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    model_used TEXT,
    tokens_used INTEGER,
    cost_usd DECIMAL(10,4),
    generated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT generated_reports_type_scope_uniq UNIQUE (report_type, scope)
);

CREATE INDEX IF NOT EXISTS generated_reports_type_generated_idx
    ON generated_reports (report_type, generated_at DESC);

-- =============================================================================
-- End of migration
-- =============================================================================
