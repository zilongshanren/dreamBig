"""Trailer hook analysis prompt + schema.

Given 4-8 frames extracted from a game trailer (the first few seconds are
the "hook" — the rest are later beats), produce a structured JSON
description of how the trailer opens and what it's selling.

Designed for GPT-4o-mini vision (multi-image). The schema is deliberately
compact so the model stays under ~600 output tokens.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TrailerHookAnalysis(BaseModel):
    """Structured summary of a trailer's opening hook and overall pitch."""

    hook_in_first_3s: str = Field(
        ...,
        description="1-2 Chinese sentences describing what happens in the first 3 seconds "
        "— the actual hook that tries to stop the scroll.",
    )
    pacing: Literal["slow", "medium", "fast", "frantic"] = Field(
        ...,
        description="Overall edit pacing across the sampled frames.",
    )
    visual_intensity: int = Field(
        ...,
        ge=1,
        le=10,
        description="1 = calm/minimalist, 10 = explosions-every-cut chaos.",
    )
    key_elements: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Concrete on-screen elements: characters, mechanics, UI, text overlays, "
        "environments. Chinese labels, max 8.",
    )
    selling_points: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="What the trailer is trying to sell (e.g. 'roguelike build depth', "
        "'cute pets', 'massive PvP battles'). Chinese, max 6.",
    )
    target_audience: str = Field(
        ...,
        description="1 short Chinese sentence describing the target player profile implied "
        "by the trailer's art/tone/mechanics.",
    )
    confidence: float = Field(
        ..., ge=0, le=1, description="How confident the analysis is given the frame quality."
    )


TRAILER_ANALYSIS_SYSTEM_PROMPT = """你是游戏买量创意分析师。我会给你一段游戏 trailer 的 4-8 帧抽样截图，
按时间顺序排列：前 1-3 张来自前 3 秒（即 hook 段），后面几张来自 10s/20s/30s/45s 等节点。

你的任务是分析这条 trailer 作为买量素材的开场钩子与卖点结构，输出结构化 JSON。

硬性规则：
1. 只返回 JSON，不要任何 markdown、代码块、前言。
2. JSON 必须严格符合给定 schema，所有字段都要填写。
3. hook_in_first_3s 专注前 3 秒发生了什么 —— 是爆炸 / 反转 / 悬念 / 梗 / UGC 感 / 数字冲击还是别的？用 1-2 句中文。
4. pacing 四选一：slow / medium / fast / frantic。
5. visual_intensity 1-10 整数，反映平均画面冲击力。
6. key_elements 列出可辨识的具体元素（角色、机制、UI、文字、场景），中文短语，最多 8 条。
7. selling_points 抽象成卖点，不要复述画面，要提炼"trailer 在卖什么"，中文，最多 6 条。
8. target_audience 1 句中文，描述目标玩家画像（年龄段 / 游戏偏好 / 消费习惯）。
9. confidence 0-1，帧越模糊 / 越少 / 越难辨识就越低。

示例 hook 类型参考：
- "角色被一刀秒掉制造反差钩子"
- "UI 上大字弹出 '你敢挑战吗'，配合弹幕式数字爆炸"
- "第 1 秒就出现结算画面 SSSS 级，倒叙钩子"
- "一只 Q 版小动物对着镜头说话，可爱系钩子"
"""


TRAILER_ANALYSIS_USER_INSTRUCTION = (
    "下面是按时间顺序抽样的 trailer 帧。请按 schema 输出 JSON 分析结果。"
)


__all__ = [
    "TrailerHookAnalysis",
    "TRAILER_ANALYSIS_SYSTEM_PROMPT",
    "TRAILER_ANALYSIS_USER_INSTRUCTION",
]
