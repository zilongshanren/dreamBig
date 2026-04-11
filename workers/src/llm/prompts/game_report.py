"""Game report generation prompt.

Produces a structured IAA-oriented analysis for a single game. The
system prompt forces the model to cite evidence (review IDs, snapshot
sources) and to decline (confidence=0) when the provided data is
insufficient — we'd rather skip a report than fabricate one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


IAAGrade = Literal["S", "A", "B", "C", "D"]


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


GAME_REPORT_SYSTEM_PROMPT = """你是一位面向中国手游发行商的资深 IAA（广告变现）分析师。
输入是某款游戏的结构化数据——平台上架信息、评论主题聚类、社媒热词——
请输出一份精炼、有证据支撑的中文战报。

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
"""

GAME_REPORT_USER_TEMPLATE = """请为以下游戏生成一份面向 IAA 决策的中文战报。

游戏名称：{game_name}
品类：{genre}

=== 平台上架摘要 ===
{platform_summary}

=== 评论主题 Top N ===
{review_topics}

=== 社媒热词 ===
{social_hot_words}

只返回符合 schema 的 JSON。**所有描述性字段必须使用简体中文**。
每个 description 字段都要引用 evidence_refs。如果数据不足，将 overall_confidence
设为 0。
"""


GAME_REPORT_PROMPT = PromptTemplate(
    name="game_report_generation",
    version="v2",
    system=GAME_REPORT_SYSTEM_PROMPT,
    user_template=GAME_REPORT_USER_TEMPLATE,
)


def build_game_report_messages(
    game_name: str,
    genre: str,
    platform_summary: str,
    review_topics: str,
    social_hot_words: str,
) -> list[dict[str, str]]:
    """Helper: render the game report prompt."""
    return GAME_REPORT_PROMPT.render(
        game_name=game_name,
        genre=genre,
        platform_summary=platform_summary,
        review_topics=review_topics,
        social_hot_words=social_hot_words,
    )


__all__ = [
    "IAAGrade",
    "CoreLoop",
    "MetaLoop",
    "IAAAdvice",
    "GameReport",
    "GAME_REPORT_PROMPT",
    "build_game_report_messages",
]
