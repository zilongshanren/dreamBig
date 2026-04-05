"""Simple cost tracker for LLM calls.

Pricing below is rough Anthropic list pricing (USD per 1M tokens) as of
early 2026 — Poe may bill differently (per-message points rather than
per-token). Treat these numbers as directional only; reconcile with
actual Poe invoices for accounting.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# USD per 1M tokens — (input_price, output_price).
# Keys are Poe bot name strings (matches PoeModel enum values).
PRICING_PER_M_TOKENS: dict[str, tuple[float, float]] = {
    "Claude-Haiku-4.5": (1.0, 5.0),
    "Claude-Sonnet-4.6": (3.0, 15.0),
    "Claude-Opus-4.6": (15.0, 75.0),
    "GPT-5": (5.0, 20.0),  # rough estimate
    "DeepSeek-R1": (0.5, 2.0),  # rough estimate
}

# Fallback pricing for unknown models — assume Sonnet-tier cost.
_DEFAULT_PRICING = (3.0, 15.0)


@dataclass
class ModelUsage:
    """Accumulated usage for a single model."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0


@dataclass
class CostTracker:
    """Tracks token usage and estimated cost across LLM calls.

    Create one instance per worker process or per batch job. Call
    `.record()` after each successful completion and `.get_total_usd()`
    at the end for logging/alerting.
    """

    by_model: dict[str, ModelUsage] = field(default_factory=lambda: defaultdict(ModelUsage))

    def record(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Record a single call's token usage. Returns this call's cost in USD."""
        in_price, out_price = PRICING_PER_M_TOKENS.get(model, _DEFAULT_PRICING)
        call_cost = (input_tokens * in_price + output_tokens * out_price) / 1_000_000

        usage = self.by_model[model]
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.calls += 1
        usage.cost_usd += call_cost

        return call_cost

    def get_total_usd(self) -> float:
        """Sum cost across all models."""
        return sum(u.cost_usd for u in self.by_model.values())

    def get_by_model(self) -> dict[str, ModelUsage]:
        """Return a copy of per-model usage."""
        return dict(self.by_model)

    def get_total_tokens(self) -> tuple[int, int]:
        """Return (total_input_tokens, total_output_tokens) across all models."""
        in_total = sum(u.input_tokens for u in self.by_model.values())
        out_total = sum(u.output_tokens for u in self.by_model.values())
        return in_total, out_total

    def reset(self) -> None:
        """Clear all accumulated usage."""
        self.by_model.clear()

    def summary(self) -> str:
        """Human-readable summary line for logs."""
        in_tok, out_tok = self.get_total_tokens()
        total = self.get_total_usd()
        return (
            f"LLM cost ~${total:.4f} "
            f"({in_tok:,} in / {out_tok:,} out tokens across "
            f"{sum(u.calls for u in self.by_model.values())} calls)"
        )
