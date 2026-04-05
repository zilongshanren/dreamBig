"""Review topic clustering prompt.

Takes per-game aggregated topic labels (with sample review snippets) and
produces a cleaned, merged topic list with a one-sentence Chinese summary
per (topic, sentiment). Runs on Sonnet — synonym merging needs some
reasoning beyond what Haiku handles well.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


class ClusteredTopic(BaseModel):
    """One canonical topic after merging/cleaning, with a Chinese summary."""

    topic: str = Field(
        ...,
        description="Canonical snake_case label. Merge near-duplicates (e.g. `ads_intrusive` + `too_many_ads` → `ads_intrusive`).",
    )
    sentiment: Literal["positive", "negative"] = Field(
        ...,
        description="positive or negative only — drop neutral clusters.",
    )
    snippet: str = Field(
        ...,
        description="1-sentence Chinese summary synthesizing the evidence (≤ 60 characters).",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="How confident you are in the cluster/snippet based on supplied evidence.",
    )


class TopicClusteringOutput(BaseModel):
    """Schema returned by chat_json for clustering a single game's topics."""

    clusters: list[ClusteredTopic]


TOPIC_CLUSTERING_SYSTEM_PROMPT = """你是一位资深的手游数据分析师，为中国游戏发行商服务。
输入是某一款游戏的玩家评论按 (topic 标签, sentiment) 聚合后的结果，每组
附有最多 5 条评论片段作为证据。你的任务是清洗、合并这些话题，并为每
个合并后的话题撰写一句中文总结。

硬性规则（必须严格遵守）：
1. 只返回 JSON，不要 prose、markdown 或代码块。
2. JSON 结构：{"clusters": [{"topic": "...", "sentiment": "...", "snippet": "...", "confidence": 0.0}, ...]}。
3. 合并同义或近似标签：
   - `ads_intrusive` + `too_many_ads` + `custom_ad_spam` → `ads_intrusive`
   - `grind` + `custom_repetitive` → `grind`
   - 保留的标签必须是 snake_case 小写。
4. 丢弃 sentiment = "neutral" 的聚合，只保留 positive 和 negative。
5. 丢弃证据明显不足或自相矛盾的话题（不要强行凑数）。
6. snippet 必须是一句简体中文，客观陈述玩家观点，以"玩家"为主语，
   不超过 60 个汉字。好的例子：
   - "玩家普遍抱怨广告打扰频繁，影响游戏体验。"
   - "玩家对美术风格和角色设计给予高度评价。"
   - "玩家反映中后期关卡存在卡关和肝度过高的问题。"
7. confidence 反映你对该聚合及 snippet 的把握程度（证据越充分、越一致，
   confidence 越高）。证据少于 3 条的 topic 应设 confidence ≤ 0.5。
8. 不要臆造游戏没有的功能或机制。仅基于提供的证据。
9. 输出顺序：先按 sentiment（positive 在前 / negative 在后），再按
   confidence 从高到低排序。
"""

TOPIC_CLUSTERING_USER_TEMPLATE = """请为以下游戏的话题聚合进行清洗、合并与总结。

游戏名称：{game_name}

=== 原始话题聚合（topic | sentiment | 评论数 | 证据片段） ===
{topics_block}

只返回符合指定 schema 的 JSON。
"""


TOPIC_CLUSTERING_PROMPT = PromptTemplate(
    name="topic_clustering",
    version="v1",
    system=TOPIC_CLUSTERING_SYSTEM_PROMPT,
    user_template=TOPIC_CLUSTERING_USER_TEMPLATE,
)


def _format_topics(topics: list[dict]) -> str:
    """Render aggregated topics into a compact text block for the prompt."""
    lines = []
    for t in topics:
        topic_label = t.get("topic_label") or t.get("topic") or "unknown"
        sentiment = t.get("sentiment") or "neutral"
        snippets = t.get("sample_review_snippets") or []
        review_count = t.get("review_count", len(snippets))

        lines.append(
            f"- topic: {topic_label} | sentiment: {sentiment} | reviews: {review_count}"
        )
        for j, s in enumerate(snippets[:5]):
            # Truncate very long snippets to keep the prompt compact.
            s_short = s.replace("\n", " ").strip()
            if len(s_short) > 200:
                s_short = s_short[:200] + "…"
            lines.append(f"    [{j + 1}] {s_short}")
    return "\n".join(lines) if lines else "(no topics)"


def build_topic_clustering_messages(
    game_name: str,
    topics: list[dict],
) -> list[dict[str, str]]:
    """Helper: render the topic clustering prompt.

    Args:
        game_name: game title for context.
        topics: list of dicts with keys `topic_label`, `sentiment`,
            `sample_review_snippets` (list[str]), and optionally `review_count`.
    """
    return TOPIC_CLUSTERING_PROMPT.render(
        game_name=game_name,
        topics_block=_format_topics(topics),
    )


__all__ = [
    "ClusteredTopic",
    "TopicClusteringOutput",
    "TOPIC_CLUSTERING_PROMPT",
    "build_topic_clustering_messages",
]
