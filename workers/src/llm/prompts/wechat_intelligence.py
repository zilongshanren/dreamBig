"""WeChat Mini-Game IAA Intelligence — honest investor analyst prompt.

Takes a structured daily snapshot of the WeChat mini-game market (cross-chart
presence, 7-day momentum, developer concentration, genre distribution,
rank × social resonance, player review voice, hook phrases, 7d market
history) and asks Opus to produce a decision-grade Chinese briefing.

Design philosophy — explicitly *not* a "world's top think tank":
- The value is the SQL signal layer, not role-play. The LLM only synthesizes
  signals that the pipeline actually extracted.
- The report must declare `data_blind_spots` — what it did NOT use — so the
  reader cannot be fooled by confident-sounding prose over thin data.
- When signals are thin, `overall_confidence` drops and the recommendations
  downgrade to "暂无可信推荐", instead of inventing content.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


# --------------------------------------------------------------------------
# Output schema
# --------------------------------------------------------------------------


class SignalGame(BaseModel):
    """A game highlighted because it shows multi-signal strength."""

    game_id: int = Field(..., description="Internal games.id")
    name: str = Field(..., description="Chinese game name")
    signal_strength: str = Field(
        ...,
        description="1-2 句中文，为什么这款游戏信号强（跨榜/动量/社媒/评分哪些共振）",
    )
    iaa_angle: str = Field(
        ...,
        description="1-2 句中文，对 IAA 立项决策的具体含义（值得借鉴什么/值得警惕什么）",
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="引用输入中的 chart_type / game_id / genre 等 label",
    )


class Opportunity(BaseModel):
    """A market opportunity the analyst spotted in the data."""

    opportunity: str = Field(
        ..., description="1 句中文概括：机会类型（赛道 / 玩法 / 机制 / 发行窗口）"
    )
    reasoning: str = Field(
        ...,
        description="2-3 句中文，引用具体数据点证明为什么 '此刻' 这是一个机会",
    )
    why_now: str = Field(
        ..., description="1 句中文，时间维度的紧迫性（窗口期多久）"
    )
    risk_factors: list[str] = Field(
        default_factory=list,
        description="3 个以内的风险项（每项 1 句中文）",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)


class RedFlag(BaseModel):
    """A saturation / decline / anti-pattern signal in the data."""

    pattern: str = Field(
        ..., description="1 句中文描述这是一个什么样的红灯信号"
    )
    affected_games: list[int] = Field(
        default_factory=list,
        description="命中这个红灯的 game_id 列表（2-8 个）",
    )
    implication: str = Field(
        ..., description="2 句中文，对想进入该赛道的新团队意味着什么"
    )


class ProjectRecommendation(BaseModel):
    """A concrete WHAT-to-build recommendation for a builder.

    Only generate this when data is strong enough to justify a bet.
    If signals are thin, prefer returning zero recommendations and
    explaining *why* in `data_blind_spots` rather than filling the slot.
    """

    title: str = Field(
        ..., description="5-15 字中文标题，如 '放置+合成 cross over 方向'"
    )
    genre: str = Field(
        ..., description="品类 key（对齐 shared/genres.json 的 key）"
    )
    core_mechanic: str = Field(
        ..., description="1 句中文，核心玩法机制（不是类型标签，是具体交互）"
    )
    inspirations: list[int] = Field(
        ...,
        description="参考标的的 game_id 列表（2-5 个），必须来自输入数据",
    )
    iaa_placement_hint: str = Field(
        ..., description="1 句中文，变现位点建议（微信小游戏 SDK 位点词汇）"
    )
    rationale: str = Field(
        ...,
        description="3-5 句中文，为什么这个方向是值得 bet 的（引用数据）",
    )
    target_audience: str = Field(
        ..., description="1 句中文，目标玩家画像"
    )
    estimated_dev_weeks: int = Field(
        ..., ge=2, le=52, description="对微信小游戏工程量的估算（2-52 周）"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)


class BlindSpot(BaseModel):
    """Honest disclosure: a signal this report did NOT actually use,
    either because the data was missing, too thin, or not yet wired.
    The reader should see this as a ceiling on the report's confidence.
    """

    signal: str = Field(
        ..., description="信号名称中文，如 '玩家评论情感分布' / '7 天前榜单对照'"
    )
    reason: str = Field(
        ...,
        description="1 句中文说明为什么没用到（未采集 / 样本 < N / 未接入），避免含糊表述",
    )
    impact: str = Field(
        ...,
        description="1 句中文，这个缺口对结论的影响（为什么读者需要知道）",
    )


Pulse = Literal["hot", "warming", "stable", "cooling", "cold"]


class WechatIntelligenceReport(BaseModel):
    """Full decision-grade briefing for one day's WeChat mini-game snapshot."""

    headline: str = Field(
        ..., description="8-20 字中文一句话当日 headline"
    )
    market_pulse: Pulse = Field(
        ..., description="市场整体状态：hot/warming/stable/cooling/cold"
    )
    market_snapshot: str = Field(
        ...,
        description="150-250 字中文市场快照。必须包含具体数据点而不是空话。",
    )
    top_signal_games: list[SignalGame] = Field(
        default_factory=list,
        description="3-7 款信号最强的游戏。顺序按信号强度排列。",
    )
    market_opportunities: list[Opportunity] = Field(
        default_factory=list,
        description="2-4 个最值得 bet 的机会",
    )
    red_flags: list[RedFlag] = Field(
        default_factory=list,
        description="2-4 个红灯（赛道饱和、模仿者扎堆、评分下滑等）",
    )
    project_recommendations: list[ProjectRecommendation] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "0-3 个具体立项建议。**数据稀薄时必须返回空列表**，"
            "并在 data_blind_spots 里解释为什么没有可信建议，"
            "不要为了填充而编造。"
        ),
    )
    data_blind_spots: list[BlindSpot] = Field(
        ...,
        min_length=1,
        description=(
            "**必填**。本次报告没有使用的信号清单 — 对应数据不存在 / "
            "样本过少 / 未接入。至少 1 条，最多 8 条。没有盲区 = 你在撒谎。"
        ),
    )
    overall_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="整体置信度。数据稀薄时必须主动降低,不要假装专业。",
    )


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------


WECHAT_INTEL_SYSTEM_PROMPT = """你是一位直言不讳、**拒绝夸大**的微信小游戏 IAA 投资合伙人分析师。
你为一个独立团队服务，他们要用你的结论做"下一款做什么"的立项决策——
一次错判可能让他们烧掉三个月。

你的立身之本不是"全球最顶尖"，而是**诚实**：
- 你只基于本次输入数据说话。输入里没有的信息，你**绝不编造**，不会
  说"玩家普遍反馈……"除非评论数据块里真的有证据。
- 数据薄时你会主动降级——置信度打低，推荐返回空列表，
  并在 `data_blind_spots` 里把没用到的信号逐条点出来。
- 你拒绝为了"显得专业"而填充三项推荐。**宁可 0 条，不要 3 条注水。**

你可以使用的判断维度（严格按输入数据块作出，不要超出）：

1. **跨榜信号强度**（cross-chart presence）
   同一款游戏出现在越多榜单，越可能是真全能爆款，而非单维度冲榜。
2. **榜单动量**（7 天 rank_change）
   冲榜速度比绝对排名更能预测未来 30 天表现。
3. **赛道饱和度**（genre concentration in top 50）
   某赛道在 top 50 里占比越高，新产品突围越难。
4. **竞争密度**（developer concentration）
   头部开发商对 top 50 的占比决定了小团队能不能硬刚。
5. **社媒-榜单共振**（rank × bilibili views）
   榜单高 + 社媒高 = 真爆款；榜单高 + 社媒低 = 可能是买量/扶持；
   榜单低 + 社媒高 = 被低估的上升候选。
6. **玩家声音**（review voice blocks）
   评论情感 + review topic summaries 是"为什么火"的直接证据。
   如果本块为空，对应游戏的"玩家普遍反馈"你**一个字都不能写**。
7. **Hook 素材**（social hook phrases）
   B 站/抖音高播放量内容里提取的 hook phrase 直接映射到"这款游戏的
   传播钩子是什么"。如果本块为空，就不要给"如何做买量"的建议。
8. **市场历史对照**（market history block）
   top-50 的新进入率 / 留存率 / 品类漂移揭示市场是在变热还是退潮。

**你不能假装内化的行业常识**（下列这些你不会编造，只能引用输入块里出现的数据）：
- 具体 ARPDAU / 留存数字
- 具体流水规模
- 历史爆款的玩家心理
- 任何你没从数据块里看到的 benchmark

硬性输出规则（违反任何一条 overall_confidence 自动降到 0.3 以下）：

1. 只返回 JSON，不要 prose / markdown / 代码块。
2. 所有字符串字段必须使用简体中文，不要中英混杂。
3. **所有结论必须引用 evidence_refs / affected_games / inspirations 中的
   game_id 或 chart label**，且 ID 必须是输入数据里实际出现的。
4. **不要编造不在输入数据里的信息。** 如果输入里没有某款游戏的评论数据，
   你不能说"玩家反馈……"。如果没有 hook 数据，不能说"B 站传播点是……"。
5. project_recommendations 的 inspirations 必须是输入里真实出现的 game_id。
   数据不足时 **返回空列表** 并在 data_blind_spots 里解释原因。
6. market_snapshot 至少引用 3 个具体数据点（例如 "畅销榜 top 50 里
   棋牌类占 18%"）。不要写"市场欣欣向荣"这种废话。
7. 数据稀薄时 overall_confidence **必须 < 0.5**。数据极稀薄时 < 0.3。
8. red_flags 必须是**决策级警告**（"这赛道别碰"），不是描述级观察。
9. **data_blind_spots 至少 1 条**。没有盲区 = 你在撒谎 = 置信度归零。
   常见盲区举例：没有历史流水数据 / 本次未使用广告投放数据 /
   评论样本 < 10 条 / 无法访问 7 天前 ranking。
"""

WECHAT_INTEL_USER_TEMPLATE = """请基于以下当日微信小游戏榜单核心数据，输出一份决策级简报。

## 全局快照
日期：{snapshot_date}
已追踪微信游戏总数：{total_games}
今日榜单条目数：{total_chart_rows}
高潜力游戏（score ≥ 60）：{high_potential_count}
评分已生成：{games_with_score}
已有 Bilibili 评论样本：{games_with_reviews}

## 维度一：跨榜信号最强 TOP 20
（同时出现在多个榜单 top 100 以内的游戏，按出现榜单数降序）
{cross_chart_block}

## 维度二：7 天动量 TOP 15
（对比 7 天前的同榜排名，上升最快的游戏）
{momentum_block}

## 维度三：开发商集中度 TOP 10
（按 top 50 席位数降序）
{developer_block}

## 维度四：品类分布（按 top 50 里的游戏数量）
{genre_block}

## 维度五：社媒-榜单共振 TOP 10
（同时拿到 top 50 排名和 B 站高播放量的游戏）
{resonance_block}

## 维度六：评分 + IAA 等级 TOP 10
（今日综合评分最高的微信游戏 + IAA 等级）
{iaa_top_block}

## 维度七：玩家声音（评论情感 + 话题聚类）
（针对今日 cross-chart / IAA 榜单里的 TOP 游戏，从 reviews + review_topic_summaries
  聚合出来。**如果下方为空或样本极少，你不能在 rationale 里写"玩家反馈……"**）
{review_voice_block}

## 维度八：Hook 素材（B 站/抖音高播放短视频 hook phrase）
（来自 social_content_samples.hook_phrase，按播放量排序。空块 = 无 hook 证据，
  不可编造"传播钩子"。）
{hook_signals_block}

## 维度九：市场历史对照（今日 vs 7 天前）
（top-50 的新进入 / 留存 / 品类漂移 —— 用来回答"市场在变热还是退潮"。
  空块 = 历史数据不足，你必须把这点写进 data_blind_spots。）
{market_history_block}

请严格按照 JSON schema 输出简报。对齐方法论的每个维度，引用真实 game_id，
不要废话，不要空话，不要讨好。**数据薄时必须主动降级并列盲区**，
宁可返回 0 条推荐，也不要注水 3 条。
"""


WECHAT_INTEL_PROMPT = PromptTemplate(
    name="wechat_intelligence",
    version="v1",
    system=WECHAT_INTEL_SYSTEM_PROMPT,
    user_template=WECHAT_INTEL_USER_TEMPLATE,
)


def build_wechat_intel_messages(
    *,
    snapshot_date: str,
    total_games: int,
    total_chart_rows: int,
    high_potential_count: int,
    games_with_score: int,
    games_with_reviews: int,
    cross_chart_block: str,
    momentum_block: str,
    developer_block: str,
    genre_block: str,
    resonance_block: str,
    iaa_top_block: str,
    review_voice_block: str,
    hook_signals_block: str,
    market_history_block: str,
) -> list[dict[str, str]]:
    return WECHAT_INTEL_PROMPT.render(
        snapshot_date=snapshot_date,
        total_games=total_games,
        total_chart_rows=total_chart_rows,
        high_potential_count=high_potential_count,
        games_with_score=games_with_score,
        games_with_reviews=games_with_reviews,
        cross_chart_block=cross_chart_block,
        momentum_block=momentum_block,
        developer_block=developer_block,
        genre_block=genre_block,
        resonance_block=resonance_block,
        iaa_top_block=iaa_top_block,
        review_voice_block=review_voice_block,
        hook_signals_block=hook_signals_block,
        market_history_block=market_history_block,
    )


__all__ = [
    "WechatIntelligenceReport",
    "SignalGame",
    "Opportunity",
    "RedFlag",
    "ProjectRecommendation",
    "BlindSpot",
    "WECHAT_INTEL_PROMPT",
    "build_wechat_intel_messages",
]
