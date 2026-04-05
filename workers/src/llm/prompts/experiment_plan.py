"""Experiment plan suggestion prompt.

Given a game's IAA advice (from its GameReport payload) plus genre + templates,
produce 3-5 concrete, prioritized A/B experiment suggestions.

Suggestions are deliberately tied to concrete variants (placement, ad type,
reward shape) rather than vague hypotheses — they should be directly
instantiable as Experiment rows in the DB.
"""

from __future__ import annotations

import json as _json
from typing import Literal

from pydantic import BaseModel, Field

from src.llm.prompts import PromptTemplate


SuccessMetric = Literal[
    "day1_retention",
    "day3_retention",
    "day7_retention",
    "arpdau",
    "ad_arpdau",
    "iap_arpdau",
    "sessions_per_dau",
    "session_length",
]


class SuggestedExperiment(BaseModel):
    """A single concrete A/B experiment suggestion."""

    name: str = Field(..., description="Short Chinese name, 10-25 chars")
    hypothesis: str = Field(..., description="Chinese hypothesis, 1-2 sentences")
    variant_a: dict = Field(
        default_factory=dict,
        description="Control variant spec: {placement, ad_type, reward, ...}",
    )
    variant_b: dict = Field(
        default_factory=dict,
        description="Treatment variant spec: {placement, ad_type, reward, ...}",
    )
    success_metric: SuccessMetric = Field(
        ..., description="Primary metric to evaluate the experiment against"
    )
    sample_size: int = Field(
        ..., ge=500, le=10000, description="DAU target per variant"
    )
    priority: int = Field(..., ge=1, le=5, description="1 = most important")
    expected_lift_pct: float = Field(
        ..., description="Expected % lift in success_metric (treatment vs control)"
    )
    rationale: str = Field(
        ..., description="Chinese rationale citing game info / iaa_advice"
    )


class ExperimentSuggestionsOutput(BaseModel):
    """Top-level output: a list of suggestions for one game."""

    game_id: int
    suggestions: list[SuggestedExperiment] = Field(
        default_factory=list,
        max_length=5,
        description="3-5 prioritized experiment suggestions",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="0 if input data is insufficient"
    )


EXPERIMENT_PLAN_SYSTEM_PROMPT = """你是一位资深商业化实验设计师，擅长为休闲手游设计 IAA / 混合变现 A/B 测试方案。

你的任务：根据游戏的玩法、类型、已有 IAA 建议（suitable_placements / forbidden_placements /
ab_test_order）和现有实验列表，为团队产出 3-5 条可直接落地的实验建议。

设计原则：
1. 每条建议必须有具体的 variant_a（对照）和 variant_b（实验）结构，包含
   placement / ad_type / reward / frequency_cap 等字段，而不是模糊描述。
2. success_metric 必须从 8 个枚举值中选一个：day1_retention / day3_retention /
   day7_retention / arpdau / ad_arpdau / iaa_arpdau / sessions_per_dau / session_length。
   注意：必须使用 iap_arpdau 拼写（不是 iaa_arpdau）。
3. sample_size 必须在 500-10000 之间。轻量测试 500-1500，主要广告位 2000-5000，付费包 3000+。
4. priority 1-5，1 = 最重要。ab_test_order 中排在前面的应该 priority = 1-2。
5. expected_lift_pct 是预估百分比提升，如 5.0 表示预估 +5%。保守务实，不要吹牛。
6. 不要重复已有实验（existing_experiments），不要建议 forbidden_placements 中已禁止的广告位。
7. rationale 必须用中文，引用 iaa_advice 或 game info 中的具体信息。
8. 可以参考提供的 templates 作为灵感，但必须针对这款游戏定制变体。
9. 如果数据太少无法设计，返回空 suggestions 数组并把 confidence 设为 0。

返回 JSON，严格匹配 schema，不要 markdown / 代码块 / 解释文本。
"""

EXPERIMENT_PLAN_USER_TEMPLATE = """为以下游戏设计 3-5 条 A/B 测试方案。

game_id: {game_id}
游戏名称: {game_name}
品类: {genre}

=== IAA 建议 (来自 GameReport.iaa_advice) ===
{iaa_advice_json}

=== 现有实验 (existing_experiments，避免重复) ===
{existing_experiments}

=== 可参考模板 (仅作为灵感) ===
{templates}

请输出 JSON，包含 game_id、3-5 条 suggestions 和 confidence（0-1）。
每条 suggestion 的 variant_a / variant_b 必须是可执行的规格，name/hypothesis/rationale 用中文。
"""


EXPERIMENT_PLAN_PROMPT = PromptTemplate(
    name="experiment_plan_suggestion",
    version="v1",
    system=EXPERIMENT_PLAN_SYSTEM_PROMPT,
    user_template=EXPERIMENT_PLAN_USER_TEMPLATE,
)


def _format_existing_experiments(experiments: list[dict]) -> str:
    """Format existing experiments as a bullet list for de-duplication."""
    if not experiments:
        return "无现有实验"

    lines: list[str] = []
    for exp in experiments:
        name = exp.get("name") or "Unknown"
        status = exp.get("status") or "draft"
        metric = exp.get("successMetric") or exp.get("success_metric") or "-"
        lines.append(f"- {name} (status={status}, metric={metric})")
    return "\n".join(lines)


def _format_templates(templates: list[dict]) -> str:
    """Condense template list to short bullets (labels + hypothesis)."""
    if not templates:
        return "无可用模板"

    lines: list[str] = []
    for t in templates:
        label = t.get("label_zh") or t.get("id") or "Unknown"
        hypothesis = t.get("hypothesis") or ""
        metric = t.get("success_metric") or "-"
        lines.append(f"- [{label}] metric={metric}: {hypothesis}")
    return "\n".join(lines)


def build_experiment_plan_messages(
    game_id: int,
    game_name: str,
    genre: str,
    iaa_advice: dict | None,
    existing_experiments: list[dict],
    templates: list[dict],
) -> list[dict[str, str]]:
    """Render the experiment plan prompt to an OpenAI-format messages list."""
    iaa_advice_json = _json.dumps(
        iaa_advice or {}, ensure_ascii=False, indent=2, default=str
    )
    # Cap embedded JSON so we stay well under token limits.
    if len(iaa_advice_json) > 3000:
        iaa_advice_json = iaa_advice_json[:3000] + "\n... (truncated)"

    return EXPERIMENT_PLAN_PROMPT.render(
        game_id=game_id,
        game_name=game_name,
        genre=genre or "unknown",
        iaa_advice_json=iaa_advice_json,
        existing_experiments=_format_existing_experiments(existing_experiments),
        templates=_format_templates(templates),
    )


__all__ = [
    "SuccessMetric",
    "SuggestedExperiment",
    "ExperimentSuggestionsOutput",
    "EXPERIMENT_PLAN_PROMPT",
    "build_experiment_plan_messages",
]
