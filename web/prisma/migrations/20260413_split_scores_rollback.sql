-- =============================================================================
-- 20260413_split_scores_rollback — 撤销 split_scores migration
-- =============================================================================
-- 用途: 如果 Sprint 2 的 scoring 重构撞墙需要完整回滚, 运行此文件.
-- 前提: 确认没有生产数据在依赖新字段 (跑验收 SQL 5 号: breakout_score 应全部为 NULL).
-- =============================================================================

-- 1. 撤销 games 表新字段
ALTER TABLE games
    DROP COLUMN IF EXISTS inherited_mechanics,
    DROP COLUMN IF EXISTS innovation_points,
    DROP COLUMN IF EXISTS maturity_ratio,
    DROP COLUMN IF EXISTS innovation_ratio,
    DROP COLUMN IF EXISTS combination_pattern;

-- 2. 撤销 potential_scores 表新字段
ALTER TABLE potential_scores
    DROP COLUMN IF EXISTS breakout_score,
    DROP COLUMN IF EXISTS mechanic_maturity,
    DROP COLUMN IF EXISTS combination_health,
    DROP COLUMN IF EXISTS market_validation,
    DROP COLUMN IF EXISTS iaa_score,
    DROP COLUMN IF EXISTS placement_fit,
    DROP COLUMN IF EXISTS monetization_risk,
    DROP COLUMN IF EXISTS ad_opportunities,
    DROP COLUMN IF EXISTS genre_iaa_fit;

-- 3. 撤销 proven_mechanics 表
DROP TABLE IF EXISTS proven_mechanics;

-- 回滚后应当:
--   SELECT COUNT(*) FROM potential_scores WHERE overall_score > 0;  -- 旧数据完整
--   \d games      -- 没有 inherited_mechanics 等字段
--   \d potential_scores  -- 没有 breakout_score 等字段
--   DROP TABLE proven_mechanics 已执行
