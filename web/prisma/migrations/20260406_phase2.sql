-- =============================================================================
-- Phase 2 migration: Auth (NextAuth v5) + Audit + Subscriptions + Genres + Social content
-- =============================================================================
-- This file is idempotent — safe to re-run.
--
-- To apply:
--   psql $DATABASE_URL -f web/prisma/migrations/20260406_phase2.sql
-- Or (in dev):
--   cd web && npx prisma db push   (will diff against schema.prisma)
-- =============================================================================

-- =============================================================================
-- 1. Users (NextAuth v5 compatible)
-- =============================================================================
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    email_verified TIMESTAMP,
    name TEXT,
    image TEXT,
    password_hash TEXT,
    role TEXT NOT NULL DEFAULT 'viewer',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMP
);

-- =============================================================================
-- 2. Accounts (OAuth providers, NextAuth standard)
-- =============================================================================
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    refresh_token TEXT,
    access_token TEXT,
    expires_at INTEGER,
    token_type TEXT,
    scope TEXT,
    id_token TEXT,
    session_state TEXT,
    CONSTRAINT accounts_provider_account_uniq UNIQUE (provider, provider_account_id)
);

-- =============================================================================
-- 3. Sessions (NextAuth standard)
-- =============================================================================
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    session_token TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires TIMESTAMP NOT NULL
);

-- =============================================================================
-- 4. Verification tokens (NextAuth standard)
-- =============================================================================
CREATE TABLE IF NOT EXISTS verification_tokens (
    identifier TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    expires TIMESTAMP NOT NULL,
    CONSTRAINT verification_tokens_identifier_token_uniq UNIQUE (identifier, token)
);

-- =============================================================================
-- 5. Audit logs
-- =============================================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    diff JSONB,
    ip TEXT,
    user_agent TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS audit_logs_user_created_idx
    ON audit_logs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_logs_resource_idx
    ON audit_logs (resource);

-- =============================================================================
-- 6. Subscriptions (订阅中心)
-- =============================================================================
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    value TEXT NOT NULL,
    channel TEXT NOT NULL,
    channel_config JSONB NOT NULL DEFAULT '{}',
    schedule TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_sent_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS subscriptions_user_idx
    ON subscriptions (user_id);
CREATE INDEX IF NOT EXISTS subscriptions_dimension_value_idx
    ON subscriptions (dimension, value);

-- =============================================================================
-- 7. Genres (aggregation/rollup table — refreshed daily)
-- =============================================================================
CREATE TABLE IF NOT EXISTS genres (
    key TEXT PRIMARY KEY,
    label_zh TEXT NOT NULL,
    label_en TEXT NOT NULL,
    iaa_baseline INTEGER NOT NULL DEFAULT 0,
    hot_games_count INTEGER NOT NULL DEFAULT 0,
    momentum DECIMAL(6,3) NOT NULL DEFAULT 0,
    top_game_ids INTEGER[] NOT NULL DEFAULT '{}',
    last_computed_at TIMESTAMP
);

-- =============================================================================
-- 8. Social content samples (video titles + hook phrases)
-- =============================================================================
CREATE TABLE IF NOT EXISTS social_content_samples (
    id SERIAL PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    content_type TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    author_name TEXT,
    hashtags TEXT[] NOT NULL DEFAULT '{}',
    view_count BIGINT NOT NULL DEFAULT 0,
    like_count BIGINT,
    comment_count INTEGER,
    hook_phrase TEXT,
    url TEXT,
    posted_at TIMESTAMP NOT NULL,
    scraped_at TIMESTAMP NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}',
    CONSTRAINT social_content_samples_platform_external_uniq UNIQUE (platform, external_id)
);

CREATE INDEX IF NOT EXISTS social_content_samples_game_posted_idx
    ON social_content_samples (game_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS social_content_samples_platform_views_idx
    ON social_content_samples (platform, view_count DESC);

-- =============================================================================
-- End of migration
-- =============================================================================
