"""Hook-phrase extraction processor.

Pipeline over `social_content_samples`:
  find rows with `hook_phrase IS NULL` and a non-empty title, batch them
  through Haiku with the HOOK_PHRASE prompt, write back the extracted
  hook per sample.

Only the LLM call is async; DB I/O uses sync psycopg, same as scoring.py
and review_analysis.py. Errors in a batch log + commit the rows processed
so far, then stop — we don't burn API budget retrying against a failing
endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import psycopg

from src.llm.models import get_model_for_task
from src.llm.poe_client import PoeClient
from src.llm.prompts.hook_phrase import (
    HookPhraseBatch,
    build_hook_phrase_messages,
)

logger = logging.getLogger(__name__)


# Batch sizes — tunable constants.
HOOK_BATCH_SIZE = 30
DEFAULT_RUN_CAP = 200  # max samples processed per single run
MAX_HOOK_LEN = 50      # hard cap written to DB (schema is `text`)


def _chunks(seq: list[Any], size: int) -> list[list[Any]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def _table_exists(conn: psycopg.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        )
        """,
        (table_name,),
    ).fetchone()
    return bool(row and row[0])


class HookExtractor:
    """Runs hook-phrase extraction over the social_content_samples table."""

    def __init__(self, db_url: str, client: PoeClient | None = None):
        self.db_url = db_url
        self.client = client or PoeClient()

    async def extract_pending(self, limit: int = DEFAULT_RUN_CAP) -> int:
        """Find samples with hook_phrase NULL, extract in batches, update.

        Returns number of samples that got a hook_phrase written.
        """
        with psycopg.connect(self.db_url) as conn:
            if not _table_exists(conn, "social_content_samples"):
                logger.warning(
                    "social_content_samples table does not exist yet — "
                    "skipping hook extraction."
                )
                return 0

            rows = conn.execute(
                """
                SELECT id, title
                FROM social_content_samples
                WHERE hook_phrase IS NULL
                  AND title IS NOT NULL
                  AND length(title) > 0
                ORDER BY view_count DESC NULLS LAST, posted_at DESC NULLS LAST
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

            if not rows:
                logger.info("No pending social content samples for hook extraction.")
                return 0

            logger.info(f"Extracting hook phrases for {len(rows)} samples...")
            model = get_model_for_task("topic_extraction")  # Haiku
            processed_total = 0

            for batch in _chunks(rows, HOOK_BATCH_SIZE):
                ids = [r[0] for r in batch]
                titles = [r[1] for r in batch]
                messages = build_hook_phrase_messages(titles)

                try:
                    result: HookPhraseBatch = await self.client.chat_json(
                        messages=messages,
                        model=model,
                        schema=HookPhraseBatch,
                    )
                except Exception as exc:
                    logger.error(
                        f"Hook-phrase LLM call failed on batch of {len(batch)}: "
                        f"{exc} — committing {processed_total} processed so far and stopping."
                    )
                    conn.commit()
                    break

                batch_count = 0
                for item in result.items:
                    if item.index < 0 or item.index >= len(ids):
                        continue
                    sample_id = ids[item.index]
                    hook = (item.hook_phrase or "").strip()[:MAX_HOOK_LEN]
                    if not hook:
                        continue
                    conn.execute(
                        """
                        UPDATE social_content_samples
                        SET hook_phrase = %s
                        WHERE id = %s
                        """,
                        (hook, sample_id),
                    )
                    batch_count += 1

                conn.commit()
                processed_total += batch_count
                logger.info(
                    f"Wrote hook_phrase on {batch_count} samples in batch"
                )

            return processed_total


# --------------------------------------------------------------------------
# Sync entry points (callable from worker.py / scheduler.py)
# --------------------------------------------------------------------------
async def _extract_async(db_url: str, limit: int) -> int:
    proc = HookExtractor(db_url)
    try:
        return await proc.extract_pending(limit=limit)
    finally:
        await proc.client.close()


def run_hook_extraction(db_url: str, limit: int = DEFAULT_RUN_CAP) -> int:
    """Non-async wrapper — extract hook phrases for pending social samples.

    Returns number of samples updated.
    """
    return asyncio.run(_extract_async(db_url, limit))


__all__ = [
    "HookExtractor",
    "run_hook_extraction",
]
