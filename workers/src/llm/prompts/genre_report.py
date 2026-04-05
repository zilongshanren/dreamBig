"""Genre weekly trend report prompt.

Produces a narrative Chinese-language weekly report that summarizes which
game genres are trending up, cooling off, and which present the strongest
IAA opportunity for the upcoming week. The system prompt requires the
model to ground every claim in the provided data and keep recommendations
actionable for a Chinese game publisher audience.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


TrendDirection = Literal["rising", "stable", "declining"]


class GenreInsight(BaseModel):
    """One-genre summary used inside the weekly report."""

    genre_key: str = Field(..., description="Genre key (e.g., 'idle').")
    label_zh: str = Field(..., description="Chinese genre label.")
    trend: TrendDirection = Field(
        ..., description="Direction derived from momentum."
    )
    hot_games_count: int = Field(..., ge=0)
    momentum: float = Field(
        ..., description="7d average score delta across active games in this genre."
    )
    key_movement: str = Field(
        ...,
        description="One Chinese sentence summarizing what is happening.",
    )
    top_game_names: list[str] = Field(
        default_factory=list, max_length=3
    )


class GenreWeeklyReport(BaseModel):
    """Full weekly genre trend report returned by chat_json."""

    week: str = Field(..., description="ISO week code like '2026-W15'.")
    headline: str = Field(
        ...,
        description="One Chinese sentence attention-grabbing headline.",
    )
    summary: str = Field(
        ...,
        description="2-3 sentence Chinese executive summary.",
    )
    top_rising: list[GenreInsight] = Field(
        default_factory=list, max_length=3
    )
    top_declining: list[GenreInsight] = Field(
        default_factory=list, max_length=3
    )
    best_iaa_opportunity: GenreInsight = Field(
        ...,
        description="Genre with the highest IAA opportunity this week.",
    )
    emerging_themes: list[str] = Field(
        default_factory=list,
        description="Cross-genre themes in Chinese, 2-5 short items.",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="2-3 actionable Chinese suggestions for publishers.",
    )
    overall_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0 if the data is too thin to trust the narrative.",
    )


GENRE_REPORT_SYSTEM_PROMPT = """你是一名面向中国手游发行商的资深赛道分析师，专注于 IAA（广告变现）赛道的趋势洞察。
你会收到过去 7 天的赛道聚合数据——包含每个赛道的热门游戏数量、7 日势能变化、IAA 基础分、
头部游戏名称等——需要基于数据输出一份精炼的中文周报。

硬性规则（必须严格遵守）：
1. 只返回 JSON，不要 Markdown、不要代码围栏、不要任何解释性文字。
2. 所有输出字段均使用简体中文，面向发行商决策者，语气专业、结论先行。
3. 每一处判断都必须基于输入数据——不得臆测未提供的发行动向、营收数字或竞品未公开信息。
4. `top_rising` 最多 3 项，按 momentum 从大到小；`top_declining` 最多 3 项，按 momentum 从小到大。
5. `trend` 取值严格为 "rising" / "stable" / "declining"，按 momentum 阈值推断：
   momentum >= 1.0 → rising；momentum <= -1.0 → declining；其他 → stable。
6. `best_iaa_opportunity` 应综合考虑 iaa_baseline（越高越好）、momentum（正向势能）、
   hot_games_count（生态是否活跃）三者——不是简单取最高 baseline，而是"当下最值得切入的"那个。
7. `emerging_themes` 为跨赛道主题（例如"女性向+放置融合在抬头"），2-5 条，每条不超过 20 字。
8. `recommendations` 给出 2-3 条立项 / 买量 / 广告位改造层面的可执行建议，避免空话套话。
9. 如果数据过少（< 3 个赛道有活跃游戏）或势能全部为 0，将 `overall_confidence` 设为 0。
10. `key_movement` 每条不超过 40 字，说清楚"发生了什么"而不是"结论"。
"""

GENRE_REPORT_USER_TEMPLATE = """请基于以下 ISO 周 {week} 的赛道聚合数据，生成本周的中文赛道周报。

=== 赛道数据（过去 7 天）===
{genres_json}

字段说明：
- key / label_zh / label_en: 赛道标识与中英文名
- iaa_baseline: 赛道 IAA 适配基础分（0-100，越高越适合 IAA）
- hot_games_count: 综合评分 >= 60 的活跃游戏数
- momentum: 7 日平均评分变化（正值 = 升温，负值 = 降温）
- top_game_names: 赛道内本周评分最高的游戏名称（最多 3 个）

请严格按照 JSON Schema 返回结果，week 字段保持为 "{week}"。
"""


GENRE_REPORT_PROMPT = PromptTemplate(
    name="genre_weekly_report",
    version="v1",
    system=GENRE_REPORT_SYSTEM_PROMPT,
    user_template=GENRE_REPORT_USER_TEMPLATE,
)


def build_genre_report_messages(
    week: str, genres_data: list[dict]
) -> list[dict[str, str]]:
    """Render the genre weekly report prompt into a messages list.

    Args:
        week: ISO week code, e.g. "2026-W15".
        genres_data: list of dicts with keys {key, label_zh, label_en,
            iaa_baseline, hot_games_count, momentum, top_game_names}.
    """
    genres_json = json.dumps(genres_data, ensure_ascii=False, indent=2)
    return GENRE_REPORT_PROMPT.render(week=week, genres_json=genres_json)


__all__ = [
    "TrendDirection",
    "GenreInsight",
    "GenreWeeklyReport",
    "GENRE_REPORT_PROMPT",
    "build_genre_report_messages",
]
