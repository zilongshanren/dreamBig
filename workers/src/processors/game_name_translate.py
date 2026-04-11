"""Game name translation processor.

Finds games with a non-null name_en but null name_zh, batches them through
Haiku with the GAME_NAME_TRANSLATE prompt, writes back the Chinese
translation.

Rationale: games scraped from Google Play US / App Store US / Steam
Global only populate name_en. Users want Chinese labels on the dashboard
and game detail pages, so we translate in bulk as a background job.

Scheduled daily at 07:15 HKT — right after social signals and before
scoring so the dashboard renders Chinese names for the freshly scraped
batch the same morning.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import psycopg

from src.llm.models import get_model_for_task
from src.llm.poe_client import PoeClient
from src.llm.prompts.game_name_translate import (
    GameNameTranslationBatch,
    build_game_name_translate_messages,
)

logger = logging.getLogger(__name__)


# Batch sizes — tunable constants.
TRANSLATE_BATCH_SIZE = 30
DEFAULT_RUN_CAP = 300  # max games processed per single run
MAX_NAME_LEN = 50      # hard cap written to DB


def _chunks(seq: list[Any], size: int) -> list[list[Any]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


class GameNameTranslator:
    """Runs name translation over the games table."""

    def __init__(self, db_url: str, client: PoeClient | None = None):
        self.db_url = db_url
        self.client = client or PoeClient()

    async def translate_pending(self, limit: int = DEFAULT_RUN_CAP) -> int:
        """Find games missing name_zh, translate in batches, update.

        Returns number of games that got a name_zh written.
        """
        with psycopg.connect(self.db_url) as conn:
            rows = conn.execute(
                """
                SELECT id, name_en, genre, developer
                FROM games
                WHERE name_zh IS NULL
                  AND name_en IS NOT NULL
                  AND length(name_en) > 0
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

            if not rows:
                logger.info("No games pending translation.")
                return 0

            logger.info(f"Translating {len(rows)} game names to Chinese...")
            model = get_model_for_task("game_name_translate")  # Haiku
            processed_total = 0

            for batch in _chunks(rows, TRANSLATE_BATCH_SIZE):
                ids = [r[0] for r in batch]
                names = [r[1] for r in batch]
                genres = [r[2] for r in batch]
                developers = [r[3] for r in batch]

                tuples = list(zip(names, genres, developers))
                messages = build_game_name_translate_messages(tuples)

                try:
                    result: GameNameTranslationBatch = await self.client.chat_json(
                        messages=messages,
                        model=model,
                        schema=GameNameTranslationBatch,
                    )
                except Exception as exc:
                    logger.error(
                        f"Translation LLM call failed on batch of {len(batch)}: "
                        f"{exc} — committing {processed_total} processed so far and stopping."
                    )
                    conn.commit()
                    break

                batch_count = 0
                for item in result.items:
                    if item.index < 0 or item.index >= len(ids):
                        continue
                    game_id = ids[item.index]
                    zh = (item.zh or "").strip()[:MAX_NAME_LEN]
                    # "keep" source with empty zh means LLM decided to keep
                    # the English name — we still mark it as processed by
                    # setting name_zh to the original name_en so we don't
                    # re-query it next run.
                    if not zh:
                        if item.source == "keep":
                            zh = names[item.index][:MAX_NAME_LEN]
                        else:
                            continue
                    conn.execute(
                        "UPDATE games SET name_zh = %s WHERE id = %s AND name_zh IS NULL",
                        (zh, game_id),
                    )
                    batch_count += 1

                conn.commit()
                processed_total += batch_count
                logger.info(f"Translated {batch_count} game names in batch")

            return processed_total


# --------------------------------------------------------------------------
# Sync entry points (callable from worker.py / scheduler.py)
# --------------------------------------------------------------------------
async def _translate_async(db_url: str, limit: int) -> int:
    proc = GameNameTranslator(db_url)
    try:
        return await proc.translate_pending(limit=limit)
    finally:
        await proc.client.close()


def run_game_name_translate(db_url: str, limit: int = DEFAULT_RUN_CAP) -> int:
    """Non-async wrapper — translate pending game names.

    Returns number of games updated.
    """
    return asyncio.run(_translate_async(db_url, limit))


__all__ = [
    "GameNameTranslator",
    "run_game_name_translate",
]
