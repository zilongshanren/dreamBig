-- =============================================================================
-- game_web_sources: raw public-web pages scraped per game for gameplay_intel
-- =============================================================================
-- One row per (game, source_site, url) — the same page is cached for 7 days
-- so we don't hammer search engines on every scheduler tick.
--
-- source_site values:
--   bing              — Bing 中文 search result (snippet + clicked-through page)
--   bilibili_article  — search.bilibili.com article page
--   gamelook          — gamelook.com.cn/?s=<name> hit
--   youxiputao        — youxiputao.com/?s=<name> hit
--
-- Idempotent — safe to re-run.
-- =============================================================================

CREATE TABLE IF NOT EXISTS game_web_sources (
    id              SERIAL PRIMARY KEY,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    source_site     TEXT    NOT NULL,
    url             TEXT    NOT NULL,
    title           TEXT,
    snippet         TEXT,
    content_text    TEXT,
    query           TEXT,
    http_status     INTEGER,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ttl_expires_at  TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS game_web_sources_game_site_url_key
    ON game_web_sources (game_id, source_site, url);

CREATE INDEX IF NOT EXISTS game_web_sources_game_fetched_idx
    ON game_web_sources (game_id, fetched_at DESC);

CREATE INDEX IF NOT EXISTS game_web_sources_ttl_idx
    ON game_web_sources (ttl_expires_at);

COMMENT ON TABLE game_web_sources IS
    'Raw public-web pages (Bing / Bilibili article / Gamelook / 游戏葡萄) '
    'scraped per game for the gameplay_intel processor. 7-day TTL cache.';
