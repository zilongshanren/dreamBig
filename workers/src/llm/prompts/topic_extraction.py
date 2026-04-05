"""Review topic extraction prompt.

Assigns 1-3 topic tags from a controlled vocabulary to each review in a
batch, given the review text and its (already classified) sentiment.
Designed for Haiku — cheap batch processing alongside sentiment.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


# Controlled topic vocabulary — snake_case only.
# The model MAY add new labels if nothing matches, prefixed with `custom_`.
TOPIC_VOCABULARY: dict[str, list[str]] = {
    "Gameplay": [
        "level_design",
        "difficulty",
        "controls",
        "grind",
        "pacing",
        "rng",
        "combat",
        "strategy",
    ],
    "Monetization": [
        "ads_intrusive",
        "ads_fair",
        "pay_to_win",
        "iap_value",
        "pricing",
        "pity_system",
    ],
    "Content": [
        "story",
        "characters",
        "art_style",
        "music",
        "voice_acting",
        "endgame_content",
        "variety",
    ],
    "Technical": [
        "bugs",
        "crashes",
        "performance",
        "battery",
        "login_issues",
        "loading",
        "server",
    ],
    "Social": [
        "community",
        "multiplayer",
        "events",
        "pvp",
        "guilds",
    ],
    "Meta": [
        "progression",
        "rewards",
        "daily_tasks",
        "collection",
        "customization",
    ],
}


def _format_vocabulary() -> str:
    """Render the controlled vocabulary as a readable block for the prompt."""
    lines = []
    for category, tags in TOPIC_VOCABULARY.items():
        lines.append(f"- {category}: {', '.join(tags)}")
    return "\n".join(lines)


class ReviewTopicsItem(BaseModel):
    """Topic tags assigned to one review in the batch."""

    index: int = Field(..., description="Zero-based index into the input list.")
    topics: list[str] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="1-3 snake_case topic tags. Prefer controlled vocabulary; new tags must be prefixed `custom_`.",
    )


class ReviewTopicsBatchOutput(BaseModel):
    """Schema returned by chat_json for batch topic extraction."""

    items: list[ReviewTopicsItem]


TOPIC_EXTRACTION_SYSTEM_PROMPT = """You are a precise topic tagger for mobile-game user reviews.
For each review (with its already-classified sentiment) you assign 1-3
topic tags that describe what the review is actually about.

Hard rules (follow exactly):
1. Return JSON only, no prose, no markdown, no code fences.
2. The JSON must have this shape: {{"items": [{{"index": 0, "topics": ["ads_intrusive", "progression"]}}, ...]}}.
3. Produce exactly one entry per input review, with matching indices.
4. Use 1-3 tags per review — not more, not fewer.
5. All tags must be lowercase snake_case.
6. Prefer labels from the controlled vocabulary below. If nothing fits,
   you MAY invent a new tag but it MUST start with `custom_` (e.g.
   `custom_tutorial_confusion`). Do not use spaces, uppercase, or
   punctuation other than underscore.
7. Pick tags that reflect the review's specific complaint or praise —
   not generic sentiment. "The ads ruin the game" → `ads_intrusive`,
   not `gameplay`.
8. Ignore the sentiment polarity when choosing topic labels — the same
   tag (e.g. `ads_intrusive`) may appear on both negative and neutral
   reviews. The sentiment is provided only for disambiguation.

Controlled vocabulary (snake_case, grouped by category):
{vocabulary}
"""

TOPIC_EXTRACTION_USER_TEMPLATE = """Tag the following {count} review(s) with 1-3 topic labels each.
Respond with JSON only.

Reviews (format: [index] (sentiment) content):
{numbered_reviews}
"""


TOPIC_EXTRACTION_PROMPT = PromptTemplate(
    name="topic_extraction",
    version="v1",
    system=TOPIC_EXTRACTION_SYSTEM_PROMPT.format(vocabulary=_format_vocabulary()),
    user_template=TOPIC_EXTRACTION_USER_TEMPLATE,
)


def build_topic_extraction_messages(
    reviews: list[tuple[str, str]],
) -> list[dict[str, str]]:
    """Helper: render the topic extraction prompt for a list of (content, sentiment) tuples."""
    numbered = "\n".join(
        f"[{i}] ({sentiment}) {content}"
        for i, (content, sentiment) in enumerate(reviews)
    )
    return TOPIC_EXTRACTION_PROMPT.render(
        count=len(reviews),
        numbered_reviews=numbered,
    )


__all__ = [
    "TOPIC_VOCABULARY",
    "ReviewTopicsItem",
    "ReviewTopicsBatchOutput",
    "TOPIC_EXTRACTION_PROMPT",
    "build_topic_extraction_messages",
]
