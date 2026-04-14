"""Game report generation prompt.

Produces a structured IAA-oriented analysis for a single game. The
system prompt forces the model to cite evidence (review IDs, snapshot
sources) and to decline (confidence=0) when the provided data is
insufficient — we'd rather skip a report than fabricate one.

Since v3 the report can also embed the project_advice (立项决策) inline,
saving an entire Sonnet round-trip per game compared to the legacy
two-call pipeline (game_report → project_advice).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


IAAGrade = Literal["S", "A", "B", "C", "D"]
ProjectRecommendation = Literal["pursue", "monitor", "pass"]


class CoreLoop(BaseModel):
    description: str = Field(..., description="What the player does moment-to-moment.")
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Review IDs / snapshot sources that support this description.",
    )


class MetaLoop(BaseModel):
    description: str = Field(..., description="Longer-arc progression / retention loop.")
    evidence_refs: list[str] = Field(default_factory=list)


class IAAAdvice(BaseModel):
    overall_grade: IAAGrade = Field(
        ..., description="Overall IAA suitability, S (best) to D (poor)."
    )
    suitable_placements: list[str] = Field(
        default_factory=list,
        description="Ad placements that fit this game's loops (e.g., 'rewarded video at run-end').",
    )
    forbidden_placements: list[str] = Field(
        default_factory=list,
        description="Ad placements that would break flow or churn users.",
    )
    risks: list[str] = Field(
        default_factory=list,
        description="Monetization/UX risks to watch for.",
    )
    ab_test_order: list[str] = Field(
        default_factory=list,
        description="Prioritized list of A/B tests to run, most important first.",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)


class InlineProjectAdvice(BaseModel):
    """Project advice produced inline by the merged report prompt.

    Mirrors `prompts.project_advice.ProjectAdvice` so downstream readers
    don't need to branch — but defined here to avoid circular imports.
    """

    recommendation: ProjectRecommendation = Field(
        ..., description="Overall decision: pursue / monitor / pass"
    )
    reasoning: str = Field(
        ..., description="1-2 paragraph Chinese explanation citing input evidence"
    )
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    similar_shipped_projects: list[str] = Field(
        default_factory=list,
        description="Names selected from the similar_games input list",
    )
    resource_estimate_weeks: int = Field(..., ge=4, le=104)
    risk_factors: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)


class GameReport(BaseModel):
    """Full structured game report returned by chat_json."""

    positioning: str = Field(
        ...,
        description="One-sentence positioning statement for the game.",
    )
    core_loop: CoreLoop
    meta_loop: MetaLoop
    pleasure_points: list[str] = Field(
        default_factory=list,
        description="Specific moments/mechanics players enjoy.",
    )
    replay_drivers: list[str] = Field(
        default_factory=list,
        description="Reasons players come back — progression, social, collection, etc.",
    )
    iaa_advice: IAAAdvice
    spread_points: list[str] = Field(
        default_factory=list,
        description="Hooks/angles that drive organic spread (UGC, word-of-mouth).",
    )
    overall_confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="0 if data is insufficient — caller should discard low-confidence reports.",
    )
    project_advice: Optional[InlineProjectAdvice] = Field(
        default=None,
        description=(
            "Inline 立项决策. Populated only when the prompt provides "
            "potential_score and similar_games — caller decides whether to ask."
        ),
    )


GAME_REPORT_SYSTEM_PROMPT = """你是一位面向中国手游发行商的资深 IAA（广告变现）分析师 + 立项顾问。
输入是某款游戏的结构化数据——平台上架信息、评论主题聚类、社媒热词，可能还附带
潜力评分与相似已上线项目列表。请输出一份精炼、有证据支撑的中文战报，
必要时附带立项决策建议。

硬性规则（必须严格遵守）：
1. 只返回 JSON，不要 prose、markdown、代码块。
2. **所有字符串字段必须使用简体中文输出**，包括 positioning、
   core_loop.description、meta_loop.description、pleasure_points 列表项、
   replay_drivers 列表项、spread_points 列表项、iaa_advice.suitable_placements
   列表项、iaa_advice.forbidden_placements 列表项、iaa_advice.risks 列表项、
   iaa_advice.ab_test_order 列表项。**不要混入英文描述**。
3. core_loop 和 meta_loop 的 description 字段必须填写 evidence_refs，引用输入
   中出现的 review ID / snapshot ID / source label。若找不到至少一条证据，将
   该 description 置为空字符串。
4. 如果输入数据不足以做可靠判断，把 overall_confidence 和 iaa_advice.confidence
   都设为 0，其它字段保持最简。**严禁编造内容。**
5. 不要推测开发者的路线图、商业目标或未发布特性，只讨论数据支持的结论。
6. iaa_advice.overall_grade 必须是 S / A / B / C / D 中的一个。
7. 所有 confidence 值必须在 0.0 - 1.0 之间。
8. 列表字段保持精炼（3-7 项），给出具体可执行的建议而不是空话套话。
9. 具体措辞参考：
   - positioning 示例："一款快节奏的塔防放置手游，主打休闲策略与英雄收集"
   - core_loop.description 示例："玩家每 30 秒完成一波防守，击败小怪获得金币升级塔"
   - suitable_placements 示例：["关卡失败后复活激励视频", "双倍金币领取激励视频"]
   - forbidden_placements 示例：["战斗中插屏", "关卡开始前插屏"]

立项建议（project_advice）规则：
A. 仅当输入提供了 "潜力评分" 与 "相似已上线项目" 段落时才输出 project_advice 字段；
   否则把 project_advice 设为 null。
B. recommendation 必须是 pursue / monitor / pass 之一：
   - pursue: 玩法清晰、IAA 适配度高 (S/A)、赛道有机会
   - monitor: 信号不够明确，需要 2-4 周观察
   - pass: 玩法过重、IAA 适配差 (C/D)、赛道饱和
C. resource_estimate_weeks 基于玩法复杂度：休闲/合成 8-16，中度策略 20-36，重度 RPG 50+
D. similar_shipped_projects 只能引用输入 similar_games 列表中的名字
E. project_advice.confidence 与 overall_confidence 独立，但都须 0.0-1.0
F. project_advice.reasoning 用中文，2 段以内
"""

GAME_REPORT_USER_TEMPLATE = """请为以下游戏生成一份面向 IAA 决策的中文战报{advice_clause}。

游戏名称：{game_name}
品类：{genre}

=== 平台上架摘要 ===
{platform_summary}

=== 评论主题 Top N ===
{review_topics}

=== 社媒热词 ===
{social_hot_words}
{advice_block}
只返回符合 schema 的 JSON。**所有描述性字段必须使用简体中文**。
每个 description 字段都要引用 evidence_refs。如果数据不足，将 overall_confidence
设为 0。{advice_reminder}
"""


GAME_REPORT_PROMPT = PromptTemplate(
    name="game_report_generation",
    version="v3",
    system=GAME_REPORT_SYSTEM_PROMPT,
    user_template=GAME_REPORT_USER_TEMPLATE,
)


def _format_similar_games(similar_games: list[dict] | None) -> str:
    """Format the similar_games section. Used by the merged advice prompt."""
    if not similar_games:
        return "无相似已上线项目可参考"
    lines: list[str] = []
    for g in similar_games:
        name = g.get("name") or "Unknown"
        grade = g.get("iaa_grade") or "-"
        score = g.get("overall_score")
        score_txt = f"潜力 {score}" if score is not None else "潜力 -"
        lines.append(f"- {name} (IAA: {grade}, {score_txt})")
    return "\n".join(lines)


def build_game_report_messages(
    game_name: str,
    genre: str,
    platform_summary: str,
    review_topics: str,
    social_hot_words: str,
    similar_games: list[dict] | None = None,
    potential_score: int | None = None,
) -> list[dict[str, str]]:
    """Render the game report prompt.

    When ``similar_games`` and ``potential_score`` are both provided, the
    prompt asks the model to also fill in ``project_advice`` inline,
    eliminating the need for a second project_advice LLM call. When either
    is absent the model returns ``project_advice: null``.
    """
    if similar_games is not None and potential_score is not None:
        advice_clause = "，并给出立项决策建议"
        advice_block = (
            f"\n=== 潜力评分 (0-100) ===\n{potential_score}\n"
            f"\n=== 相似已上线项目 ===\n{_format_similar_games(similar_games)}\n"
        )
        advice_reminder = (
            " 同时填写 project_advice 字段（recommendation/strengths/weaknesses/"
            "similar_shipped_projects/risk_factors/resource_estimate_weeks/confidence/"
            "reasoning），similar_shipped_projects 只能从上面列出的相似项目里选。"
        )
    else:
        advice_clause = ""
        advice_block = ""
        advice_reminder = " project_advice 字段设为 null。"

    return GAME_REPORT_PROMPT.render(
        game_name=game_name,
        genre=genre,
        platform_summary=platform_summary,
        review_topics=review_topics,
        social_hot_words=social_hot_words,
        advice_clause=advice_clause,
        advice_block=advice_block,
        advice_reminder=advice_reminder,
    )


__all__ = [
    "IAAGrade",
    "ProjectRecommendation",
    "CoreLoop",
    "MetaLoop",
    "IAAAdvice",
    "InlineProjectAdvice",
    "GameReport",
    "GAME_REPORT_PROMPT",
    "build_game_report_messages",
]
