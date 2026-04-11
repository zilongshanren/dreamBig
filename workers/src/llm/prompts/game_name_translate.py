"""Game name translation prompt.

Translates English game names to simplified Chinese in batch. Targets
games scraped from global/en stores that have no CN listing, so name_zh
is null. The LLM should prefer well-known official CN names (e.g.
"Clash of Clans" → "部落冲突") and fall back to semantic translation
when no localized name exists.

Runs on Haiku — cheap, batched, tens of names per call.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


class GameNameTranslation(BaseModel):
    """One translated name in the batch."""

    index: int = Field(..., description="Zero-based index into the input list.")
    zh: str = Field(
        ...,
        description="Simplified Chinese translation. Empty string if no reasonable translation exists.",
    )
    source: str = Field(
        ...,
        description="One of: 'official' (known localized name), 'semantic' (translated meaning), 'transliteration' (phonetic), 'keep' (leave original).",
    )


class GameNameTranslationBatch(BaseModel):
    """Schema returned by chat_json for the translation batch."""

    items: list[GameNameTranslation]


GAME_NAME_TRANSLATE_SYSTEM_PROMPT = """你是一位资深的游戏中文本地化编辑，专门为中国手游发行商提供游戏名称的中文对应名。
你会收到一批英文游戏名（每条包含品类和开发商作为上下文），为每条生成一个简体中文名称。

硬性规则（必须严格遵守）：
1. 只返回 JSON，不要 prose、markdown 或代码块。
2. JSON 结构：{"items": [{"index": 0, "zh": "部落冲突", "source": "official"}, ...]}。
3. 每条输入对应一条输出，index 严格匹配。
4. source 字段必须是以下之一：
   - "official"：该游戏有公认的官方/大陆上架中文名（如 "Clash of Clans" → "部落冲突"、
     "Subway Surfers" → "地铁跑酷"、"Candy Crush Saga" → "糖果传奇"）。
     只在你**确定**这是官方发行名时才用此值。
   - "semantic"：没有官方名，但可以做一个贴切的语义翻译（如 "Zombie Farm" → "僵尸农场"、
     "Word Puzzle Master" → "单词解谜大师"）。
   - "transliteration"：名称主要是专有名词 / 品牌名，无对应中文但可以音译
     （如 "Temple Run" → "神庙逃亡"、"Monument Valley" → "纪念碑谷"）。
   - "keep"：名称是英文缩写、型号数字、或者过于抽象难以翻译时保留原文，zh 字段设为空字符串。
5. 翻译风格偏向中国大陆玩家熟悉的叫法，不要港台用语（用"熊出没"不要"熊大與熊二"）。
6. 不要臆造你不确定的"官方名"。如果拿不准是不是官方名，降级为 semantic。
7. zh 字段长度控制在 2-12 个汉字，避免过长。
8. 品类和开发商只作为消歧义的参考，不要出现在译名里
   （例如 "Super Mario Run (Nintendo)" → "超级马里奥酷跑"，不要带 "任天堂"）。

示例：
输入：
[0] Clash of Clans (strategy, Supercell)
[1] Monster Crunch: The Breakfast Battle
[2] EA FC 25
[3] 放置江湖 (already Chinese)

输出：
{"items": [
  {"index": 0, "zh": "部落冲突", "source": "official"},
  {"index": 1, "zh": "怪兽脆脆：早餐大作战", "source": "semantic"},
  {"index": 2, "zh": "", "source": "keep"},
  {"index": 3, "zh": "放置江湖", "source": "keep"}
]}
"""

GAME_NAME_TRANSLATE_USER_TEMPLATE = """请为下列 {count} 条英文游戏名生成简体中文对应名。

游戏列表（格式：[索引] 名称 (品类, 开发商)）：
{numbered_games}

只返回 JSON。
"""


GAME_NAME_TRANSLATE_PROMPT = PromptTemplate(
    name="game_name_translate",
    version="v1",
    system=GAME_NAME_TRANSLATE_SYSTEM_PROMPT,
    user_template=GAME_NAME_TRANSLATE_USER_TEMPLATE,
)


def build_game_name_translate_messages(
    games: list[tuple[str, str | None, str | None]],
) -> list[dict[str, str]]:
    """Helper: render the translation prompt for a list of (name_en, genre, developer) tuples."""
    lines = []
    for i, (name, genre, developer) in enumerate(games):
        ctx_parts = []
        if genre:
            ctx_parts.append(genre)
        if developer:
            ctx_parts.append(developer)
        ctx = f" ({', '.join(ctx_parts)})" if ctx_parts else ""
        lines.append(f"[{i}] {name}{ctx}")
    numbered = "\n".join(lines)
    return GAME_NAME_TRANSLATE_PROMPT.render(
        count=len(games),
        numbered_games=numbered,
    )


__all__ = [
    "GameNameTranslation",
    "GameNameTranslationBatch",
    "GAME_NAME_TRANSLATE_PROMPT",
    "build_game_name_translate_messages",
]
