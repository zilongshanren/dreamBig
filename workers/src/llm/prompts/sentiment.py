"""Review sentiment classification prompt.

Classifies a batch of review texts into positive/negative/neutral with
a confidence score. Designed to be called with Haiku for cheap batch
processing — one LLM call handles many reviews at once.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


Sentiment = Literal["positive", "negative", "neutral"]


class SentimentItem(BaseModel):
    """One classified review in the batch."""

    index: int = Field(..., description="Zero-based index into the input list.")
    sentiment: Sentiment
    confidence: float = Field(..., ge=0.0, le=1.0)


class SentimentBatchOutput(BaseModel):
    """Schema returned by chat_json for sentiment classification."""

    items: list[SentimentItem]


SENTIMENT_SYSTEM_PROMPT = """You are a precise sentiment classifier for mobile-game user reviews.
Your job: label each review as positive, negative, or neutral, with a
confidence between 0 and 1.

Rules:
- Return JSON only, no prose, no markdown, no code fences.
- The JSON must have this shape: {"items": [{"index": 0, "sentiment": "positive", "confidence": 0.92}, ...]}.
- Produce exactly one entry per input review, with matching indices.
- Use "neutral" for mixed, ambiguous, or off-topic reviews.
- confidence reflects how certain you are — lower it for short/ambiguous reviews.
- Do not rewrite, translate, or summarize the reviews. Classification only.
"""

SENTIMENT_USER_TEMPLATE = """Classify the sentiment of the following {count} review(s).
Respond with JSON only.

Reviews:
{numbered_reviews}
"""


SENTIMENT_PROMPT = PromptTemplate(
    name="sentiment_classification",
    version="v1",
    system=SENTIMENT_SYSTEM_PROMPT,
    user_template=SENTIMENT_USER_TEMPLATE,
)


def build_sentiment_messages(reviews: list[str]) -> list[dict[str, str]]:
    """Helper: render the sentiment prompt for a list of review texts."""
    numbered = "\n".join(f"[{i}] {text}" for i, text in enumerate(reviews))
    return SENTIMENT_PROMPT.render(count=len(reviews), numbered_reviews=numbered)


__all__ = [
    "Sentiment",
    "SentimentItem",
    "SentimentBatchOutput",
    "SENTIMENT_PROMPT",
    "build_sentiment_messages",
]
