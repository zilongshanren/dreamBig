"""LLM infrastructure for DreamBig — Poe API client + prompt templates.

This package provides async Poe API access for downstream agents that need
LLM capabilities: review sentiment classification, topic extraction,
game report generation, IAA advice, and genre trend analysis.
"""

from __future__ import annotations

from src.llm.cost import CostTracker
from src.llm.models import (
    TASK_MODEL_MAP,
    ModelTier,
    PoeModel,
    get_model_for_task,
)
from src.llm.poe_client import ChatResponse, LLMError, PoeClient
from src.llm.prompts import PromptTemplate

__all__ = [
    "PoeClient",
    "ChatResponse",
    "LLMError",
    "ModelTier",
    "PoeModel",
    "TASK_MODEL_MAP",
    "get_model_for_task",
    "PromptTemplate",
    "CostTracker",
]
