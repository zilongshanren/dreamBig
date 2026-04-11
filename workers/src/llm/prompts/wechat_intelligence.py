"""WeChat Mini-Game IAA Intelligence — top-tier think tank prompt.

Takes a structured daily snapshot of the WeChat mini-game market (cross-chart
presence, 7-day momentum, developer concentration, genre distribution,
rank × social resonance) and asks Opus to produce a decision-grade
Chinese briefing: market pulse → opportunities → red flags → 3 concrete
project recommendations, every claim tied back to game IDs.

Designed for a builder making a single 'what should we actually ship'
decision — NOT for descriptive analytics. Output is Chinese, punchy,
cites evidence, and refuses to be impressive when data is thin.
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
    """A concrete WHAT-to-build recommendation for a builder."""

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
        ...,
        min_length=1,
        max_length=3,
        description="1-3 个具体立项建议（题材+机制+变现+工期）",
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


WECHAT_INTEL_SYSTEM_PROMPT = """你是全球最顶尖的微信小游戏 IAA 爆款智库首席分析师。
过去 10 年你看过 1000+ 款微信小游戏从默默无闻到爆发到衰落的完整生命周期。
你的客户是独立游戏团队和发行商，他们来找你是为了做"下一款做什么"的决策，
不是来听市场动态介绍的。

你的分析方法论（严格遵守）：

1. **跨榜信号强度**（cross-chart presence）
   同一款游戏出现在越多榜单，说明这是一款真全能爆款而不是单维度冲榜产物。
   出现在 3+ 榜单的游戏几乎一定有值得拆解的核心竞争力。

2. **榜单动量**（7 天 rank_change）
   冲榜速度比绝对排名更能预测未来 30 天表现。动量 > 50 名的游戏往往处于
   上升期，是最值得研究的样本。

3. **赛道饱和度**（genre concentration in top 50）
   如果某赛道在 top 50 里占 > 20% 的席位，新产品很难突围；
   如果占 < 5% 但外部社媒热度高，就是蓝海信号。

4. **竞争密度**（developer concentration）
   头部开发商占据 top 50 的份额越高，说明该品类的马太效应越强；
   小团队不要硬刚这些赛道，找缝隙进入。

5. **社媒-榜单共振**（rank × bilibili/douyin views）
   榜单高 + 社媒高 = 真爆款；
   榜单高 + 社媒低 = 付费买量 / 官方扶持 / 存量用户，不一定值得复刻；
   榜单低 + 社媒高 = 被低估的上升候选。

6. **微信小游戏 IAA 行业常识**（你必须内化这些）：
   - 主包 4MB / 分包 20MB 限制决定了美术资产必须轻
   - 主要变现位：失败复活激励 / 双倍奖励 / 体力恢复 / 离线收益 / 稀有试玩
   - 审核红线：强制分享、诱导关注、博彩包装
   - 典型 ARPDAU：休闲 0.05-0.15 元 / 重度休闲 0.2-0.5 元
   - 典型 day1 留存 30-45% 为行业中位

硬性输出规则（违反任何一条 overall_confidence 自动降到 0.3 以下）：

1. 只返回 JSON，不要 prose / markdown / 代码块
2. 所有字符串字段必须使用简体中文，不要中英混杂
3. **所有结论必须引用 evidence_refs / affected_games / inspirations 中的
   game_id 或 chart label**。evidence_refs 只能引用输入数据里实际出现的 ID。
4. 不要编造不在输入数据里的信息。如果输入里没有某个游戏的评论数据，
   就不要在 rationale 里说"玩家普遍反馈..."
5. project_recommendations 的 inspirations 必须是**输入数据里真实出现的
   游戏 game_id**，至少 2 个。estimated_dev_weeks 要合理（放置 4-8 周，
   RPG 10-16 周，3D 动作 16-30 周）。
6. market_snapshot 至少引用 3 个具体数据点（例如 "畅销榜 top 50 里
   棋牌类占 18%"）。不要写"市场欣欣向荣"这种废话。
7. 数据稀薄时 overall_confidence 必须 < 0.5。不要为了显得专业而硬分析。
8. red_flags 必须是**决策级警告**（"这赛道别碰"），不是描述级观察
   （"这赛道游戏很多"）。
9. 你的语气像一位直言不讳的投资合伙人，不像一位奉承客户的分析师。
"""

WECHAT_INTEL_USER_TEMPLATE = """请基于以下当日微信小游戏榜单核心数据，输出一份决策级智库简报。

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

请严格按照 JSON schema 输出简报。对齐方法论的每个步骤，引用真实 game_id，
不要废话，不要空话，不要讨好。
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
    )


__all__ = [
    "WechatIntelligenceReport",
    "SignalGame",
    "Opportunity",
    "RedFlag",
    "ProjectRecommendation",
    "WECHAT_INTEL_PROMPT",
    "build_wechat_intel_messages",
]
