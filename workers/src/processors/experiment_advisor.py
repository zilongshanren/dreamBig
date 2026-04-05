"""LLM-backed experiment plan suggester.

Given a game with an existing IAA advice (stored inside GameReport.payload),
produce 3-5 concrete A/B test suggestions that the product/monetization team
can instantiate as Experiment rows.

This processor does NOT persist suggestions — it returns them so that the API
caller (or a dry-run script) decides whether to create Experiment rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import psycopg

from src.llm import PoeClient, get_model_for_task
from src.llm.poe_client import LLMError
from src.llm.prompts.experiment_plan import (
    EXPERIMENT_PLAN_PROMPT,
    ExperimentSuggestionsOutput,
    build_experiment_plan_messages,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = EXPERIMENT_PLAN_PROMPT.version  # "v1"

# Templates live in the shared/ folder at repo root.
_TEMPLATES_PATH = (
    Path(__file__).resolve().parents[3] / "shared" / "experiment_templates.json"
)

# Limit existing experiments we embed in the prompt (keeps tokens bounded).
MAX_EXISTING_EXPERIMENTS = 20
MIN_CONFIDENCE_TO_RETURN = 0.3


class ExperimentAdvisor:
    """Orchestrates context assembly + LLM call for experiment suggestions."""

    def __init__(self, db_url: str, client: PoeClient | None = None):
        self.db_url = db_url
        self.client = client or PoeClient()
        self.model = get_model_for_task("iaa_advice")  # Opus (same strategic tier)
        self._templates = _load_templates()

    async def suggest_for_game(self, game_id: int) -> list[dict] | None:
        """Produce a list of suggested experiments for one game.

        Returns:
            List of dicts matching SuggestedExperiment shape, or None when
            skipped (missing game, missing iaa_advice, low confidence, or
            LLM failure). Suggestions are NOT persisted — the caller decides.
        """
        # 1. Gather context (sync DB reads)
        try:
            with psycopg.connect(self.db_url) as conn:
                context = _gather_context(conn, game_id)
        except psycopg.errors.UndefinedTable as exc:
            logger.warning(
                f"Required table missing, skipping game {game_id}: {exc}"
            )
            return None
        except psycopg.Error as exc:
            logger.error(
                f"DB error gathering context for experiment suggest "
                f"(game {game_id}): {exc}"
            )
            return None

        if context is None:
            logger.info(
                f"Insufficient context for experiment suggest game {game_id}, "
                "skipping"
            )
            return None

        # 2. Filter templates to those applicable to this genre
        genre = (context.get("genre") or "").lower()
        applicable_templates = _filter_templates_by_genre(self._templates, genre)

        # 3. Build messages
        messages = build_experiment_plan_messages(
            game_id=game_id,
            game_name=context["game_name"],
            genre=genre or "unknown",
            iaa_advice=context["iaa_advice"],
            existing_experiments=context["existing_experiments"],
            templates=applicable_templates,
        )

        # 4. Call chat_json
        try:
            output = await self.client.chat_json(
                messages=messages,
                model=self.model,
                schema=ExperimentSuggestionsOutput,
            )
        except LLMError as exc:
            logger.error(
                f"LLM call failed for experiment suggest (game {game_id}, "
                f"{context['game_name']}): {exc}"
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"Unexpected error in experiment suggest (game {game_id}, "
                f"{context['game_name']}): {exc}"
            )
            return None

        # 5. Discard low-confidence suggestions
        if output.confidence < MIN_CONFIDENCE_TO_RETURN:
            logger.info(
                f"Experiment suggest confidence {output.confidence:.2f} below "
                f"threshold {MIN_CONFIDENCE_TO_RETURN} for game {game_id} "
                f"({context['game_name']}), discarding"
            )
            return None

        suggestions = [s.model_dump() for s in output.suggestions]
        logger.info(
            f"Experiment suggestions ready for game {game_id} "
            f"({context['game_name']}): {len(suggestions)} items, "
            f"confidence={output.confidence:.2f}"
        )
        return suggestions


# ============================================================
# Template loading / filtering
# ============================================================
def _load_templates() -> list[dict]:
    """Load shared/experiment_templates.json. Returns [] on any error."""
    try:
        raw = _TEMPLATES_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        templates = data.get("templates")
        if isinstance(templates, list):
            return templates
    except FileNotFoundError:
        logger.warning(
            f"Experiment templates file not found at {_TEMPLATES_PATH}"
        )
    except json.JSONDecodeError as exc:
        logger.error(f"Failed to parse experiment templates: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Unexpected error loading templates: {exc}")
    return []


def _filter_templates_by_genre(templates: list[dict], genre: str) -> list[dict]:
    """Keep templates whose applicable_genres contains this genre or '*'."""
    if not templates:
        return []

    norm_genre = genre.lower().strip()
    out: list[dict] = []
    for t in templates:
        applicable = t.get("applicable_genres") or []
        if not applicable:
            out.append(t)
            continue
        if "*" in applicable or norm_genre in [
            str(g).lower() for g in applicable
        ]:
            out.append(t)
    return out


# ============================================================
# Context gathering
# ============================================================
def _gather_context(
    conn: psycopg.Connection, game_id: int
) -> dict[str, Any] | None:
    """Fetch game info + iaa_advice + existing experiments.

    Returns None when the game doesn't exist or has no game_report /
    iaa_advice to start from — we'd rather skip than ask the LLM to
    fabricate a plan.
    """
    # 1. Game base info
    game_row = conn.execute(
        """
        SELECT COALESCE(name_en, name_zh, 'Unknown') AS name,
               genre
        FROM games
        WHERE id = %s
        """,
        (game_id,),
    ).fetchone()

    if game_row is None:
        logger.warning(f"Game {game_id} not found")
        return None

    game_name, genre = game_row

    # 2. IAA advice from game_reports.payload
    report_row = conn.execute(
        """
        SELECT payload
        FROM game_reports
        WHERE game_id = %s
        """,
        (game_id,),
    ).fetchone()

    iaa_advice: dict[str, Any] | None = None
    if report_row is not None:
        payload = report_row[0]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        if isinstance(payload, dict):
            iaa_advice = payload.get("iaa_advice")

    if not iaa_advice:
        logger.info(
            f"Game {game_id} has no iaa_advice in game_reports, skipping "
            "experiment suggest"
        )
        return None

    # 3. Existing experiments (so the LLM doesn't duplicate them)
    exp_rows = conn.execute(
        """
        SELECT name, status, success_metric, priority
        FROM experiments
        WHERE game_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (game_id, MAX_EXISTING_EXPERIMENTS),
    ).fetchall()

    existing_experiments = [
        {
            "name": r[0],
            "status": r[1],
            "successMetric": r[2],
            "priority": r[3],
        }
        for r in exp_rows
    ]

    return {
        "game_name": game_name,
        "genre": genre or "",
        "iaa_advice": iaa_advice,
        "existing_experiments": existing_experiments,
    }


# ============================================================
# Sync entry points
# ============================================================
def run_experiment_suggest(db_url: str, game_id: int) -> list[dict] | None:
    """Entry for API endpoint / worker job.

    Synchronous wrapper around the async advisor. Returns list of
    suggested experiment dicts, or None on skip/failure.
    """
    return asyncio.run(_suggest_async(db_url, game_id))


async def _suggest_async(db_url: str, game_id: int) -> list[dict] | None:
    advisor = ExperimentAdvisor(db_url)
    try:
        suggestions = await advisor.suggest_for_game(game_id)
        logger.info(
            f"Experiment advisor: {advisor.client.cost_tracker.summary()}"
        )
        return suggestions
    finally:
        await advisor.client.close()


__all__ = [
    "ExperimentAdvisor",
    "run_experiment_suggest",
    "PROMPT_VERSION",
    "MIN_CONFIDENCE_TO_RETURN",
]
