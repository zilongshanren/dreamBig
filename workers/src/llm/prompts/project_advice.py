"""Project advice prompt — pursue/monitor/pass decision for publishing.

Given an existing GameReport payload plus similar games and platform data,
produce a structured project-level recommendation for the publishing team:
立项/观察/放弃 with reasoning, strengths, risks, and resource estimate.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


ProjectRecommendation = Literal["pursue", "monitor", "pass"]


class ProjectAdvice(BaseModel):
    """Structured project advice decision."""

    recommendation: ProjectRecommendation = Field(
        ..., description="Overall decision: pursue / monitor / pass"
    )
    reasoning: str = Field(
        ..., description="1-2 paragraph Chinese explanation citing input evidence"
    )
    strengths: list[str] = Field(
        default_factory=list, description="3-5 key strengths"
    )
    weaknesses: list[str] = Field(
        default_factory=list, description="3-5 key concerns"
    )
    similar_shipped_projects: list[str] = Field(
        default_factory=list,
        description="Names of similar games from the input, referenced for comparison",
    )
    resource_estimate_weeks: int = Field(
        ...,
        ge=4,
        le=104,
        description="Estimated dev weeks if pursued",
    )
    risk_factors: list[str] = Field(
        default_factory=list, description="2-4 risks to watch"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)


PROJECT_ADVICE_SYSTEM_PROMPT = """你是一位资深游戏发行决策顾问，帮助团队判断是否立项复刻/代理一款爆款。

决策框架：
- pursue (立项/推进): 玩法清晰、IAA 适配度高 (S/A)、赛道有机会、可操作
- monitor (观察): 信号不够明确，需要 2-4 周观察
- pass (放弃): 玩法过重、IAA 适配差 (C/D)、赛道饱和、风险过高

规则：
- 返回 JSON，不要 markdown
- reasoning 用中文，2 段以内
- resource_estimate_weeks 基于玩法复杂度：休闲/合成 8-16 周，中度策略 20-36 周，重度 RPG 50+ 周
- 每条意见要引用输入数据，不要凭空臆测
- 如果数据太少，confidence 设为 0
- strengths / weaknesses 各 3-5 条，risk_factors 2-4 条，列表项要具体可操作
- similar_shipped_projects 只能从输入的 similar_games 列表中选取名字
"""

PROJECT_ADVICE_USER_TEMPLATE = """请为以下游戏产出立项决策建议。

游戏名称: {game_name}
品类: {genre}
潜力评分 (0-100): {potential_score}

=== 平台数据摘要 ===
{platform_summary}

=== 已有游戏报告 (GameReport payload) ===
{game_report_json}

=== 相似已上线项目 ===
{similar_games}

请输出 JSON，严格匹配 schema。reasoning 使用中文，每条 strengths/weaknesses/risk_factors
都要能从上面的输入数据中找到依据。如果数据不足以做判断，confidence=0。
"""


PROJECT_ADVICE_PROMPT = PromptTemplate(
    name="project_advice",
    version="v1",
    system=PROJECT_ADVICE_SYSTEM_PROMPT,
    user_template=PROJECT_ADVICE_USER_TEMPLATE,
)


def _format_similar_games(similar_games: list[dict]) -> str:
    """Format similar games list for the prompt.

    Expected keys per dict: name, iaa_grade, overall_score.
    """
    if not similar_games:
        return "无相似已上线项目可参考"

    lines: list[str] = []
    for game in similar_games:
        name = game.get("name") or "Unknown"
        grade = game.get("iaa_grade") or "-"
        score = game.get("overall_score")
        score_txt = f"潜力 {score}" if score is not None else "潜力 -"
        lines.append(f"- {name} (IAA: {grade}, {score_txt})")
    return "\n".join(lines)


def build_project_advice_messages(
    game_name: str,
    genre: str,
    game_report_payload: dict,
    similar_games: list[dict],
    potential_score: int,
    platform_summary: str,
) -> list[dict[str, str]]:
    """Render the project advice prompt to an OpenAI-format messages list."""
    import json as _json

    game_report_json = _json.dumps(
        game_report_payload, ensure_ascii=False, indent=2, default=str
    )
    # Cap the embedded report JSON so we stay well under token limits.
    if len(game_report_json) > 6000:
        game_report_json = game_report_json[:6000] + "\n... (truncated)"

    return PROJECT_ADVICE_PROMPT.render(
        game_name=game_name,
        genre=genre or "unknown",
        potential_score=potential_score,
        platform_summary=platform_summary or "无平台数据",
        game_report_json=game_report_json,
        similar_games=_format_similar_games(similar_games),
    )


__all__ = [
    "ProjectRecommendation",
    "ProjectAdvice",
    "PROJECT_ADVICE_PROMPT",
    "build_project_advice_messages",
]
