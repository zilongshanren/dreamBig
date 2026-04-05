"""Hook phrase extraction prompt.

Given a batch of game-related video titles, extract one short Chinese
"吸睛点" (hook phrase) per title that explains why a user would click.
Designed for Haiku — cheap, good at short Chinese text.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


class HookPhraseItem(BaseModel):
    """One hook phrase extracted from a single title."""

    index: int = Field(..., description="Zero-based index into the input list.")
    hook_phrase: str = Field(
        ...,
        max_length=50,
        description="10–25 Chinese characters describing the click hook.",
    )


class HookPhraseBatch(BaseModel):
    """Schema returned by chat_json for batch hook extraction."""

    items: list[HookPhraseItem]


HOOK_PHRASE_SYSTEM_PROMPT = """你是一位游戏买量素材分析师。给定一组游戏相关视频的标题，
为每个视频抽取一个"吸睛点"（hook phrase）。这个 hook 要能解释为什么用户会点击它。

硬性规则（必须严格遵守）：
1. 只返回 JSON，不要任何前言、解释、markdown、代码块。
2. JSON 结构：{"items": [{"index": 0, "hook_phrase": "..."}, ...]}
3. 每条输入对应一条输出，index 必须匹配输入顺序。
4. hook_phrase 用中文，10-25 个字，不超过 25 字。
5. 聚焦创意钩子类型（视觉冲击 / 反差 / 好奇心 / 情感 / 数字 / 悬念 / 梗 / 挑战）。
6. 不要简单复述标题，要抽象出"为什么这个视频能吸引点击"。
7. 每个 hook 必须非空；实在抽不出明确钩子则用"平铺直叙型内容展示"占位。

示例：
- 标题"氪 10 万才抽到的 SSR 终于下场了" → hook "氪金数字+稀有卡出战的反差"
- 标题"一分钟教你通关这个困难副本" → hook "短时间速通的实用教程承诺"
- 标题"这个游戏玩到后期居然是恐怖游戏" → hook "类型反转带来的好奇心钩子"
"""

HOOK_PHRASE_USER_TEMPLATE = """为以下 {count} 个游戏视频标题抽取吸睛点（hook phrase）。
只返回 JSON。

标题列表：
{numbered_titles}
"""


HOOK_PHRASE_PROMPT = PromptTemplate(
    name="hook_phrase_extraction",
    version="v1",
    system=HOOK_PHRASE_SYSTEM_PROMPT,
    user_template=HOOK_PHRASE_USER_TEMPLATE,
)


def build_hook_phrase_messages(titles: list[str]) -> list[dict[str, str]]:
    """Helper: render the hook-phrase prompt for a list of video titles."""
    numbered = "\n".join(f"[{i}] {title}" for i, title in enumerate(titles))
    return HOOK_PHRASE_PROMPT.render(count=len(titles), numbered_titles=numbered)


__all__ = [
    "HookPhraseItem",
    "HookPhraseBatch",
    "HOOK_PHRASE_PROMPT",
    "build_hook_phrase_messages",
]
