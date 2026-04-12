-- =============================================================================
-- 20260413_split_scores — PotentialScore 拆分成 breakoutScore + iaaScore
-- =============================================================================
-- 底层逻辑: 产品层宣称做"微信爆款中心", 底层评分却只衡量"IAA 广告变现适配度".
-- 这是一个架构级的概念混淆 — 两个正交维度被压成一个 overallScore 数字.
-- 本 migration 把 potential_scores 拆成双维度并行, 新增 games 表的组合创新字段,
-- 新增 proven_mechanics 参照库. shadow mode: 旧 overall_score 保留, 不破坏任何现有功能.
--
-- 生效范围:
--   - games                    新增 5 列 (inherited_mechanics / innovation_points /
--                                         maturity_ratio / innovation_ratio / combination_pattern)
--   - potential_scores         新增 10 列 (breakout_score / mechanic_maturity /
--                                         combination_health / market_validation /
--                                         iaa_score / placement_fit / monetization_risk /
--                                         ad_opportunities / genre_iaa_fit + 隐含 scored_at 不变)
--   - proven_mechanics         新表 (30 条成熟玩法 seed 参照库)
--
-- 语义注释:
--   - maturity_ratio 和 innovation_ratio 由 LLM (v3_split prompt) 估算, 和应在 0.95-1.05
--   - combination_pattern 枚举: classic_stack / twist_hybrid / radical_new / copycat / unknown
--     健康爆款=twist_hybrid (maturity 0.70-0.85 且 innovation_points ≥ 1 条)
--   - breakout_score / iaa_score 允许 NULL, Sprint 1 只建字段, Sprint 2 才写入
--   - shadow mode 到 Sprint 5 结束, legacy overall_score 在 Sprint 5 停止写入
--
-- Idempotent — safe to re-run.
-- Rollback: 20260413_split_scores_rollback.sql
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. games: 80/20 组合创新拆解字段
-- -----------------------------------------------------------------------------
ALTER TABLE games
    ADD COLUMN IF NOT EXISTS inherited_mechanics  TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS innovation_points    TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS maturity_ratio       NUMERIC(3,2),
    ADD COLUMN IF NOT EXISTS innovation_ratio     NUMERIC(3,2),
    ADD COLUMN IF NOT EXISTS combination_pattern  TEXT;

COMMENT ON COLUMN games.inherited_mechanics IS
    '继承的成熟玩法 tag 数组 (必须是 proven_mechanics.tag 的 subset, 运行期白名单校验)';
COMMENT ON COLUMN games.innovation_points IS
    '核心创新点短句 (≤3 条, 每条 ≤20 字, 空数组 = 无差异化)';
COMMENT ON COLUMN games.maturity_ratio IS
    '成熟玩法占比 0-1, 由 LLM 估算, 健康区间 0.70-0.85';
COMMENT ON COLUMN games.innovation_ratio IS
    '创新占比 0-1, maturity_ratio + innovation_ratio 应在 0.95-1.05 之间';
COMMENT ON COLUMN games.combination_pattern IS
    'classic_stack(纯堆叠) / twist_hybrid(健康 80/20) / radical_new(激进) / copycat(抄袭) / unknown(证据不足)';

-- -----------------------------------------------------------------------------
-- 2. potential_scores: 双轨 breakout + iaa 维度
-- -----------------------------------------------------------------------------
ALTER TABLE potential_scores
    ADD COLUMN IF NOT EXISTS breakout_score     SMALLINT,
    ADD COLUMN IF NOT EXISTS mechanic_maturity  SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS combination_health SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS market_validation  SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS iaa_score          SMALLINT,
    ADD COLUMN IF NOT EXISTS placement_fit      SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS monetization_risk  SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ad_opportunities   SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS genre_iaa_fit      SMALLINT NOT NULL DEFAULT 0;

COMMENT ON COLUMN potential_scores.breakout_score IS
    '爆款潜质 0-100, NULL = 未评估. 由 mechanic_maturity/combination_health/ranking_velocity/market_validation/social_buzz 加权';
COMMENT ON COLUMN potential_scores.mechanic_maturity IS
    '继承机制的平均成熟度 (查 proven_mechanics.maturity_level)';
COMMENT ON COLUMN potential_scores.combination_health IS
    '组合健康度, 由 games.combination_pattern 和 maturity_ratio 算出';
COMMENT ON COLUMN potential_scores.market_validation IS
    '市场验证度双峰曲线: 0-2=40(未验证) / 3-15=90(健康) / 16-30=60(饱和) / 31+=30(红海)';
COMMENT ON COLUMN potential_scores.iaa_score IS
    'IAA 变现适配度 0-100, NULL = 未评估. 由 placement_fit/genre_iaa_fit/ad_opportunities/monetization_risk/cross_platform 加权';
COMMENT ON COLUMN potential_scores.placement_fit IS
    '广告位契合度, 根据 GameReport.iaa.suitable_placements 数量和质量打分';
COMMENT ON COLUMN potential_scores.monetization_risk IS
    '变现风险反向分, 风险越多得分越低';
COMMENT ON COLUMN potential_scores.ad_opportunities IS
    '广告投放机会, 继承自旧 ad_activity 但口径独立';
COMMENT ON COLUMN potential_scores.genre_iaa_fit IS
    '品类 IAA 适配度, 继承自 shared/genres.json.iaa_score 查表';

-- -----------------------------------------------------------------------------
-- 3. proven_mechanics: 成熟玩法参照库 (由 seed_proven_mechanics.py 填充)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proven_mechanics (
    id                 SERIAL PRIMARY KEY,
    tag                TEXT        NOT NULL UNIQUE,
    label_zh           TEXT        NOT NULL,
    label_en           TEXT        NOT NULL,
    category           TEXT        NOT NULL,
    maturity_level     SMALLINT    NOT NULL DEFAULT 5,
    iaa_friendliness   SMALLINT    NOT NULL DEFAULT 50,
    description        TEXT        NOT NULL,
    reference_game_ids INTEGER[]   NOT NULL DEFAULT '{}',
    synonyms           TEXT[]      NOT NULL DEFAULT '{}',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS proven_mechanics_category_idx
    ON proven_mechanics (category);

COMMENT ON TABLE proven_mechanics IS
    '成熟玩法参照库 — LLM prompt 的白名单候选池. 任何 inherited_mechanics tag 必须命中此表的 tag 字段, 否则视为 LLM 编造被过滤.';
COMMENT ON COLUMN proven_mechanics.category IS
    'core_loop / meta_loop / monetization / social';
COMMENT ON COLUMN proven_mechanics.maturity_level IS
    '成熟度 1-10, 10 = 市场已极度验证';
COMMENT ON COLUMN proven_mechanics.iaa_friendliness IS
    '该机制对 IAA 变现的友好度 0-100, 注意这不等于玩法成熟度';
COMMENT ON COLUMN proven_mechanics.synonyms IS
    '中英文别名数组, 文本匹配时用';

-- -----------------------------------------------------------------------------
-- 4. 数据完整性验收 (Sprint 1 退出闸门)
-- -----------------------------------------------------------------------------
-- 以下查询应当全部成功, 任何一条失败说明 migration 未生效:
--
-- \d games      -- 应看到 inherited_mechanics / innovation_points / maturity_ratio / innovation_ratio / combination_pattern
-- \d potential_scores  -- 应看到 breakout_score / mechanic_maturity / combination_health / market_validation / iaa_score / placement_fit / monetization_risk / ad_opportunities / genre_iaa_fit
-- \d proven_mechanics  -- 应看到完整表结构
-- SELECT COUNT(*) FROM potential_scores WHERE overall_score > 0;  -- 旧数据完整, 零丢失
-- SELECT COUNT(*) FROM potential_scores WHERE breakout_score IS NOT NULL;  -- Sprint 1 应为 0, Sprint 2 才开始写入
