"""Gameplay Intel — structured game-play fact sheet for a single game.

Takes all public signals already captured for one game (applestore
editor_intro, screenshots, genre/developer, review_topic_summaries,
social hook phrases) and asks Sonnet to synthesize four decision-grade
fields:

    - gameplay_intro          1-3 句中文总括
    - features                3-5 条中文玩法特色短 tag
    - art_style_primary       主风格标签
    - art_style_secondary     2-3 条补充风格
    - art_style_evidence      原文证据引用

The design follows the hard lesson from wechat_intelligence v1:
"don't let the LLM inflate when the inputs are thin". The prompt bans
fabrication of any fact not backed by the input blocks, forces the
model to drop features/art_style silently when evidence is missing,
and pushes confidence down when the evidence count is small.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


# --------------------------------------------------------------------------
# Output schema
# --------------------------------------------------------------------------


class GameplayIntelReport(BaseModel):
    """Structured gameplay / art / feature fact sheet for one game."""

    gameplay_intro: str = Field(
        ...,
        description=(
            "1-3 句中文综合玩法介绍，必须基于输入数据。"
            "数据极少时只写 1 句兜底，不要硬凑长度。"
        ),
    )
    features: list[str] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "3-5 条中文玩法特色短 tag（每条 5-15 字），"
            "无证据时返回空列表。不要复述 gameplay_intro 内容。"
        ),
    )
    art_style_primary: str | None = Field(
        None,
        description=(
            "主美术风格标签（如 'Q 版卡通' / '像素风' / '国风水墨'）。"
            "文案 + 截图均无证据时返回 null。"
        ),
    )
    art_style_secondary: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="0-3 条补充风格 tag（如 ['休闲明快', '轻度奇幻']）",
    )
    art_style_evidence: list[str] = Field(
        default_factory=list,
        max_length=5,
        description=(
            "1-4 条中文原文引用，说明你是从哪个信号判断出美术风格的。"
            "空值 = 你没底气，primary/secondary 必须也为空。"
        ),
    )
    screenshot_refs: list[int] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "你认为最能代表玩法的截图索引（对应输入 screenshots 数组下标），"
            "不做 gameplay/logo 分类——只是推荐 3-5 个看着最像玩法画面的。"
        ),
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "整体置信度。只有 editor_intro（无评论/无 hook/无视觉）时必须 < 0.5。"
            "多源互证时可到 0.7+。不要为了显专业而虚报。"
        ),
    )


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------


GAMEPLAY_INTEL_SYSTEM_PROMPT = """你是一个直言不讳的游戏档案分析师，负责为一款
微信小游戏整理"玩法介绍 / 特色 / 美术风格"三项结构化档案。

你的行为底线（违反任何一条 confidence 自动降到 0.3 以下）：

1. **只基于输入说话**。editor_intro、截图 URL、评论话题、hook 短句 —
   输入里没出现的内容一个字都不能编。不要写"官方宣传……"，除非原文出现了那句话。
2. **数据薄时主动留白**。features 没证据就返回空列表；
   art_style_primary 判断不出来就返回 null；不允许"占位式填充"。
3. **引用原文而非复述**。art_style_evidence 必须是原文段落的精确片段
   （≤30 字），不是你自己编的注解。
4. **features 必须是玩法本身**。不是 "画面精美 / 运营良心 / 更新频繁" 这种运营词，
   而是 "三消 + 关卡连击"、"放置挂机 + 离线收益"、"卡组构筑 Roguelike"
   这种具体可复刻的机制描述。
5. **screenshot_refs 是推荐，不是分类**。从 screenshots 数组里挑 3-5 个看着
   最像玩法场景的下标，0-based。不需要解释，不要越过数组长度。
6. **confidence 校准**。只有 editor_intro 1 条证据 → < 0.5；
   editor_intro + 评论话题 → 0.5-0.7；三源以上互证 → 0.7-0.9。

硬性输出规则：

- 只返回 JSON，不要 markdown 代码块，不要前言。
- 字段名**必须**使用下方骨架里给出的英文 key。字段**值**用简体中文。
- gameplay_intro 至少 15 字，最多 120 字。features 每条 5-15 字。
- 你不是文案，是档案员。简洁 > 华丽。

## 字段类型硬约束

- `screenshot_refs` 是**纯整数数组**（如 `[0, 2, 4]`），不是字符串。
  下标必须落在输入 screenshots 数组有效范围内（0 到 len-1）。
- `art_style_primary` 是**单个字符串**或 `null` —— 不是数组。
- `art_style_secondary` 是字符串数组（可空）。
- `features` 是字符串数组（可空）。
- `confidence` 是 0.0-1.0 之间的浮点数。

## 标准 JSON 骨架（字段名一个字符都不能改）

```json
{
  "gameplay_intro": "字符串（15-120 字）",
  "features": ["字符串", "字符串", "字符串"],
  "art_style_primary": "字符串或 null",
  "art_style_secondary": ["字符串"],
  "art_style_evidence": ["字符串原文片段"],
  "screenshot_refs": [0, 2, 4],
  "confidence": 0.55
}
```

遇到字段名时**直接复制上面的英文 key**。不要写 `"gameplay"` / `"intro"` /
`"art_style"` 这类变体，不要把 primary 和 secondary 合并成对象。

你不会说"这是一款……好玩的游戏"这种废话。
你会说"放置挂机 + 离线金币 + 合成进阶，休闲为主，偶尔推图"。
"""


GAMEPLAY_INTEL_USER_TEMPLATE = """请为以下这款微信小游戏整理玩法档案。

## 游戏基础信息
- game_id: {game_id}
- 名称: {game_name}
- 品类: {genre}
- 开发商: {developer}

## 维度一：官方 editor_intro 原文（来自应用宝详情页）
{editor_intro_block}

## 维度二：截图 URL 列表（已去重，来自应用宝详情页）
{screenshots_block}

## 维度三：玩家评论话题（来自 review_topic_summaries，已按 LLM 聚类）
{review_topics_block}

## 维度四：B 站/抖音 hook 短句（来自 social_content_samples.hook_phrase）
{hook_phrases_block}

严格按 JSON schema 返回。数据薄时主动留白，不要注水——
宁可 features 返回空列表 + confidence 0.3，也不要编造玩法特色。
"""


GAMEPLAY_INTEL_PROMPT = PromptTemplate(
    name="gameplay_intel",
    version="v1",
    system=GAMEPLAY_INTEL_SYSTEM_PROMPT,
    user_template=GAMEPLAY_INTEL_USER_TEMPLATE,
)


def build_gameplay_intel_messages(
    *,
    game_id: int,
    game_name: str,
    genre: str,
    developer: str,
    editor_intro_block: str,
    screenshots_block: str,
    review_topics_block: str,
    hook_phrases_block: str,
) -> list[dict[str, str]]:
    return GAMEPLAY_INTEL_PROMPT.render(
        game_id=game_id,
        game_name=game_name,
        genre=genre,
        developer=developer,
        editor_intro_block=editor_intro_block,
        screenshots_block=screenshots_block,
        review_topics_block=review_topics_block,
        hook_phrases_block=hook_phrases_block,
    )


__all__ = [
    "GameplayIntelReport",
    "GAMEPLAY_INTEL_PROMPT",
    "build_gameplay_intel_messages",
]
