"""Model registry + task-to-model routing for Poe API.

Poe bot names are the exact strings that the Poe API expects when you pass
them as the `model` parameter. Mapping tasks to tiers ensures we spend
Opus budget only where deep reasoning actually moves the needle.
"""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class PoeModel(str, Enum):
    """Exact bot name strings that the Poe API expects in the `model` field."""

    HAIKU = "Claude-Haiku-4.5"
    SONNET = "Claude-Sonnet-4.6"
    OPUS = "Claude-Opus-4.6"
    GPT5 = "GPT-5"  # fallback when Anthropic models are unavailable
    DEEPSEEK = "DeepSeek-R1"


class ModelTier(str, Enum):
    """Logical cost/capability tiers — mapped to concrete Poe models."""

    FAST = "fast"  # batch classification, cheap — Haiku
    BALANCED = "balanced"  # topic extraction, summaries — Sonnet
    DEEP = "deep"  # game report, IAA analysis — Opus


# Each tier resolves to a concrete Poe bot name.
TIER_MODEL_MAP: dict[ModelTier, PoeModel] = {
    ModelTier.FAST: PoeModel.HAIKU,
    ModelTier.BALANCED: PoeModel.SONNET,
    ModelTier.DEEP: PoeModel.OPUS,
}


# Task routing — edit this table when adding new downstream consumers.
TASK_MODEL_MAP: dict[str, PoeModel] = {
    "sentiment_classification": PoeModel.HAIKU,
    "topic_extraction": PoeModel.HAIKU,
    "topic_clustering": PoeModel.SONNET,
    "game_report_generation": PoeModel.OPUS,
    "iaa_advice": PoeModel.OPUS,
    "genre_trend_summary": PoeModel.SONNET,
    "review_summary": PoeModel.SONNET,
    "game_name_translate": PoeModel.HAIKU,
    "wechat_intelligence": PoeModel.OPUS,
}


# Fallback chain when a model is rate-limited or unavailable.
# Try the same tier first, then drop to a nearby tier.
FALLBACK_CHAIN: dict[PoeModel, list[PoeModel]] = {
    PoeModel.OPUS: [PoeModel.SONNET, PoeModel.GPT5],
    PoeModel.SONNET: [PoeModel.HAIKU, PoeModel.GPT5],
    PoeModel.HAIKU: [PoeModel.GPT5, PoeModel.DEEPSEEK],
    PoeModel.GPT5: [PoeModel.SONNET, PoeModel.HAIKU],
    PoeModel.DEEPSEEK: [PoeModel.HAIKU, PoeModel.GPT5],
}


def get_model_for_task(task_name: str) -> str:
    """Return the Poe bot name (string) for a given task.

    Unknown tasks default to the BALANCED tier (Sonnet) and log a warning
    so we can catch typos in task names early.
    """
    model = TASK_MODEL_MAP.get(task_name)
    if model is None:
        logger.warning(
            f"Unknown LLM task '{task_name}', defaulting to BALANCED tier. "
            f"Register it in TASK_MODEL_MAP to silence this warning."
        )
        model = TIER_MODEL_MAP[ModelTier.BALANCED]
    return model.value


def get_model_for_tier(tier: ModelTier) -> str:
    """Return the Poe bot name (string) for a given tier."""
    return TIER_MODEL_MAP[tier].value


def get_fallback_chain(model: str) -> list[str]:
    """Return a list of fallback bot names for a given model string."""
    try:
        poe_model = PoeModel(model)
    except ValueError:
        return [PoeModel.SONNET.value, PoeModel.HAIKU.value]
    return [m.value for m in FALLBACK_CHAIN.get(poe_model, [])]
