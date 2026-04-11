-- =============================================================================
-- Phase 3-4 multi-team workspace migration
-- =============================================================================
-- Adds workspace-scoped tenancy to "business action" tables only.
-- Game data (games / platform_listings / ranking_snapshots / reviews / reports)
-- stays globally shared — that's the platform-wide value.
-- Per-tenant tables: alerts, subscriptions, experiments, game_tags, audit_logs.
--
-- Idempotent: safe to re-run.
-- =============================================================================

-- =============================================================================
-- 1. Workspaces and members
-- =============================================================================
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS workspaces_is_default_idx ON workspaces (is_default);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'analyst',
    -- super_admin/analyst/publisher/monetization/viewer
    joined_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS workspace_members_user_idx
    ON workspace_members (user_id);

-- =============================================================================
-- 2. Bootstrap default workspace
-- =============================================================================
INSERT INTO workspaces (id, name, slug, description, is_default)
VALUES ('default', '默认工作区', 'default', 'Auto-created legacy workspace', TRUE)
ON CONFLICT (id) DO NOTHING;

-- Backfill all existing users into the default workspace
INSERT INTO workspace_members (workspace_id, user_id, role)
SELECT 'default', id, role FROM users
ON CONFLICT (workspace_id, user_id) DO NOTHING;

-- =============================================================================
-- 3. Add workspace_id columns to per-tenant tables
-- =============================================================================
ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE;
UPDATE alerts SET workspace_id = 'default' WHERE workspace_id IS NULL;
ALTER TABLE alerts ALTER COLUMN workspace_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS alerts_workspace_idx ON alerts (workspace_id);

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE;
UPDATE subscriptions SET workspace_id = 'default' WHERE workspace_id IS NULL;
ALTER TABLE subscriptions ALTER COLUMN workspace_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS subscriptions_workspace_idx ON subscriptions (workspace_id);

ALTER TABLE experiments
    ADD COLUMN IF NOT EXISTS workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE;
UPDATE experiments SET workspace_id = 'default' WHERE workspace_id IS NULL;
ALTER TABLE experiments ALTER COLUMN workspace_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS experiments_workspace_idx ON experiments (workspace_id);

ALTER TABLE game_tags
    ADD COLUMN IF NOT EXISTS workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE;
UPDATE game_tags SET workspace_id = 'default' WHERE workspace_id IS NULL;
ALTER TABLE game_tags ALTER COLUMN workspace_id SET NOT NULL;
-- Update PK to include workspace_id (drop old PK, add new composite PK)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='game_tags' AND constraint_type='PRIMARY KEY'
    ) THEN
        ALTER TABLE game_tags DROP CONSTRAINT IF EXISTS game_tags_pkey;
    END IF;
END$$;
ALTER TABLE game_tags ADD PRIMARY KEY (game_id, tag, workspace_id);
CREATE INDEX IF NOT EXISTS game_tags_workspace_idx ON game_tags (workspace_id);

ALTER TABLE audit_logs
    ADD COLUMN IF NOT EXISTS workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL;
UPDATE audit_logs SET workspace_id = 'default' WHERE workspace_id IS NULL;
CREATE INDEX IF NOT EXISTS audit_logs_workspace_idx ON audit_logs (workspace_id);

-- =============================================================================
-- 4. User.last_workspace_id (remembers last selection across logins)
-- =============================================================================
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS last_workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL;
UPDATE users SET last_workspace_id = 'default' WHERE last_workspace_id IS NULL;

-- =============================================================================
-- End of migration
-- =============================================================================
