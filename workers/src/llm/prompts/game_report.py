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


GAME_REPORT_SYSTEM_PROMPT = """You are a senior mobile-game analyst specializing in IAA (In-App Advertising)
monetization. Given structured data about a single game — its platform
listings, top review topics, and social hot words — produce a concise,
evidence-backed report.

Hard rules (follow exactly):
1. Return JSON only. No prose, no markdown, no code fences.
2. Every description (core_loop, meta_loop) MUST cite evidence_refs. Use
   the review IDs, snapshot IDs, or source labels present in the input.
   If you cannot cite at least one source, set that field's description
   to the empty string.
3. If the input data is too thin to make a confident call, set
   overall_confidence=0 and iaa_advice.confidence=0, and keep other
   fields minimal. DO NOT invent details.
4. Do not speculate about the developer's roadmap, business goals, or
   unreleased features. Stick to what the provided data supports.
5. iaa_advice.overall_grade must be exactly one of: S, A, B, C, D.
6. Every confidence value must be between 0.0 and 1.0.
7. Keep lists short (3-7 items). Prefer precise, actionable items over
   generic platitudes.
"""

GAME_REPORT_USER_TEMPLATE = """Produce an IAA-oriented game report for the following game.

Game name: {game_name}
Genre: {genre}

=== Platform listings summary ===
{platform_summary}

=== Top review topics ===
{review_topics}

=== Social hot words ===
{social_hot_words}

Respond with JSON only, matching the requested schema exactly. Cite
evidence_refs for every description field. If the data is insufficient,
set overall_confidence=0.
"""


GAME_REPORT_PROMPT = PromptTemplate(
    name="game_report_generation",
    version="v1",
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
