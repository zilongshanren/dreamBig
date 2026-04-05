"""Review NLP processor.

Pipeline over the `reviews` table:
  1. classify_sentiments — fill in `sentiment` + `sentiment_confidence`
     for reviews that don't have a label yet (Haiku, batch of 50).
  2. extract_topics — fill in `topics[]` for reviews that already have
     a sentiment but no topic tags (Haiku, batch of 30).
  3. cluster_game_topics — aggregate all labeled reviews for one game,
     merge synonymous tags, write a one-sentence Chinese summary per
     (topic, sentiment) into `review_topic_summaries` (Sonnet, one call
     per game).
  4. cluster_all_games — run (3) for every game that has enough labeled
     reviews and no summary from today yet.

Only the LLM call is async; DB I/O uses sync psycopg, same as scoring.py.
Errors within a batch are logged and break the loop — we don't burn API
budget retrying against a failing endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date
from typing import Any

import psycopg

from src.llm.models import get_model_for_task
from src.llm.poe_client import PoeClient
from src.llm.prompts.sentiment import (
    SentimentBatchOutput,
    build_sentiment_messages,
)
from src.llm.prompts.topic_clustering import (
    TopicClusteringOutput,
    build_topic_clustering_messages,
)
from src.llm.prompts.topic_extraction import (
    ReviewTopicsBatchOutput,
    build_topic_extraction_messages,
)

logger = logging.getLogger(__name__)


# Batch sizes — tunable constants.
SENTIMENT_BATCH_SIZE = 50
TOPIC_BATCH_SIZE = 30
DEFAULT_RUN_CAP = 500  # max reviews processed per single run
MIN_REVIEWS_PER_GROUP = 3  # skip (topic, sentiment) groups with fewer reviews
MIN_REVIEWS_PER_GAME = 20  # game must have >= N labeled reviews to cluster
MAX_SAMPLES_PER_GROUP = 5  # evidence snippets kept per (topic, sentiment) group
MAX_SNIPPET_LEN = 240  # truncate per-snippet chars sent to the LLM


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


class ReviewNLPProcessor:
    """Runs sentiment + topic NLP over the reviews table."""

    def __init__(self, db_url: str, poe_client: PoeClient | None = None):
        self.db_url = db_url
        self.client = poe_client or PoeClient()

    # ------------------------------------------------------------------
    # 1. Sentiment classification
    # ------------------------------------------------------------------
    async def classify_sentiments(self, limit: int = DEFAULT_RUN_CAP) -> int:
        """Classify unlabeled reviews (sentiment IS NULL). Batch 50 per LLM call.

        Returns number of reviews processed.
        """
        with psycopg.connect(self.db_url) as conn:
            if not _table_exists(conn, "reviews"):
                logger.warning(
                    "reviews table does not exist yet — skipping sentiment classification."
                )
                return 0

            rows = conn.execute(
                """
                SELECT id, content
                FROM reviews
                WHERE sentiment IS NULL
                  AND content IS NOT NULL
                  AND length(content) > 0
                ORDER BY posted_at DESC NULLS LAST
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

            if not rows:
                logger.info("No unclassified reviews to process.")
                return 0

            logger.info(f"Classifying sentiment for {len(rows)} reviews...")
            model = get_model_for_task("sentiment_classification")
            processed_total = 0

            for batch in _chunks(rows, SENTIMENT_BATCH_SIZE):
                ids = [r[0] for r in batch]
                contents = [r[1] for r in batch]
                messages = build_sentiment_messages(contents)

                try:
                    result: SentimentBatchOutput = await self.client.chat_json(
                        messages=messages,
                        model=model,
                        schema=SentimentBatchOutput,
                    )
                except Exception as exc:
                    logger.error(
                        f"Sentiment LLM call failed on batch of {len(batch)}: {exc} — "
                        f"committing {processed_total} processed so far and stopping."
                    )
                    conn.commit()
                    break

                # Write back per-item — index into this batch.
                batch_count = 0
                for item in result.items:
                    if item.index < 0 or item.index >= len(ids):
                        continue
                    review_id = ids[item.index]
                    conn.execute(
                        """
                        UPDATE reviews
                        SET sentiment = %s,
                            sentiment_confidence = %s
                        WHERE id = %s
                        """,
                        (item.sentiment, item.confidence, review_id),
                    )
                    batch_count += 1

                conn.commit()
                processed_total += batch_count
                logger.info(f"Classified {batch_count} reviews in batch")

            return processed_total

    # ------------------------------------------------------------------
    # 2. Topic extraction
    # ------------------------------------------------------------------
    async def extract_topics(self, limit: int = DEFAULT_RUN_CAP) -> int:
        """Extract topic tags for reviews with sentiment set but topics empty.

        Returns number of reviews processed.
        """
        with psycopg.connect(self.db_url) as conn:
            if not _table_exists(conn, "reviews"):
                logger.warning(
                    "reviews table does not exist yet — skipping topic extraction."
                )
                return 0

            rows = conn.execute(
                """
                SELECT id, content, sentiment
                FROM reviews
                WHERE sentiment IS NOT NULL
                  AND content IS NOT NULL
                  AND length(content) > 0
                  AND (topics IS NULL OR cardinality(topics) = 0)
                ORDER BY posted_at DESC NULLS LAST
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

            if not rows:
                logger.info("No reviews pending topic extraction.")
                return 0

            logger.info(f"Extracting topics for {len(rows)} reviews...")
            model = get_model_for_task("topic_extraction")
            processed_total = 0

            for batch in _chunks(rows, TOPIC_BATCH_SIZE):
                ids = [r[0] for r in batch]
                pairs = [(r[1], r[2]) for r in batch]  # (content, sentiment)
                messages = build_topic_extraction_messages(pairs)

                try:
                    result: ReviewTopicsBatchOutput = await self.client.chat_json(
                        messages=messages,
                        model=model,
                        schema=ReviewTopicsBatchOutput,
                    )
                except Exception as exc:
                    logger.error(
                        f"Topic extraction LLM call failed on batch of {len(batch)}: "
                        f"{exc} — committing {processed_total} processed so far and stopping."
                    )
                    conn.commit()
                    break

                batch_count = 0
                for item in result.items:
                    if item.index < 0 or item.index >= len(ids):
                        continue
                    review_id = ids[item.index]
                    # Normalize: snake_case, lowercase, dedupe, keep at most 3.
                    clean_topics = _sanitize_topics(item.topics)
                    if not clean_topics:
                        continue
                    conn.execute(
                        """
                        UPDATE reviews
                        SET topics = %s
                        WHERE id = %s
                        """,
                        (clean_topics, review_id),
                    )
                    batch_count += 1

                conn.commit()
                processed_total += batch_count
                logger.info(f"Tagged topics on {batch_count} reviews in batch")

            return processed_total

    # ------------------------------------------------------------------
    # 3. Topic clustering (per game)
    # ------------------------------------------------------------------
    async def cluster_game_topics(self, game_id: int) -> int:
        """Aggregate labeled reviews for one game, cluster into summaries.

        Returns number of topic summaries written for this game.
        """
        with psycopg.connect(self.db_url) as conn:
            if not _table_exists(conn, "reviews") or not _table_exists(
                conn, "review_topic_summaries"
            ):
                logger.warning(
                    "reviews / review_topic_summaries table missing — skipping clustering."
                )
                return 0

            # Fetch labeled reviews for the game via platform_listings.
            rows = conn.execute(
                """
                SELECT r.id, r.content, r.sentiment, r.topics
                FROM reviews r
                JOIN platform_listings pl ON r.platform_listing_id = pl.id
                WHERE pl.game_id = %s
                  AND r.sentiment IS NOT NULL
                  AND r.topics IS NOT NULL
                  AND cardinality(r.topics) > 0
                """,
                (game_id,),
            ).fetchall()

            if not rows:
                logger.info(f"Game {game_id}: no labeled reviews to cluster.")
                return 0

            # Fetch game name for the prompt.
            name_row = conn.execute(
                "SELECT COALESCE(name_en, name_zh, '') FROM games WHERE id = %s",
                (game_id,),
            ).fetchone()
            game_name = (name_row[0] if name_row else "") or f"game#{game_id}"

            # Group by (topic, sentiment), keep up to MAX_SAMPLES snippets + ids.
            grouped: dict[
                tuple[str, str], dict[str, Any]
            ] = defaultdict(
                lambda: {"count": 0, "review_ids": [], "snippets": []}
            )
            for review_id, content, sentiment, topics in rows:
                if sentiment not in ("positive", "negative"):
                    continue  # skip neutral during clustering
                for topic in topics or []:
                    key = (str(topic), sentiment)
                    g = grouped[key]
                    g["count"] += 1
                    if len(g["snippets"]) < MAX_SAMPLES_PER_GROUP:
                        snippet = (content or "")[:MAX_SNIPPET_LEN]
                        g["snippets"].append(snippet)
                        g["review_ids"].append(review_id)

            # Drop noisy groups with < MIN_REVIEWS_PER_GROUP evidence.
            filtered_groups = {
                k: v
                for k, v in grouped.items()
                if v["count"] >= MIN_REVIEWS_PER_GROUP
            }
            if not filtered_groups:
                logger.info(
                    f"Game {game_id}: no (topic, sentiment) group passed min-{MIN_REVIEWS_PER_GROUP} threshold."
                )
                return 0

            # Build LLM input.
            llm_topics_input = [
                {
                    "topic_label": topic,
                    "sentiment": sentiment,
                    "review_count": data["count"],
                    "sample_review_snippets": data["snippets"],
                }
                for (topic, sentiment), data in filtered_groups.items()
            ]

            messages = build_topic_clustering_messages(game_name, llm_topics_input)
            model = get_model_for_task("topic_clustering")

            try:
                result: TopicClusteringOutput = await self.client.chat_json(
                    messages=messages,
                    model=model,
                    schema=TopicClusteringOutput,
                )
            except Exception as exc:
                logger.error(
                    f"Topic clustering LLM call failed for game {game_id}: {exc}"
                )
                return 0

            # Map each clustered topic back to evidence review_ids.
            # Since the LLM may merge synonyms, we union the sample_review_ids
            # from all source groups that either (a) have the exact canonical
            # label or (b) share the sentiment and we lose provenance — so we
            # fall back to the canonical label's own evidence if present, else
            # to the first matching-sentiment group.
            today = date.today()
            summaries_written = 0

            for cluster in result.clusters:
                canon_topic = (cluster.topic or "").strip().lower()
                if not canon_topic:
                    continue
                canon_sentiment = cluster.sentiment
                if canon_sentiment not in ("positive", "negative"):
                    continue

                # Collect evidence: prefer exact-match group, otherwise
                # aggregate all groups with the same sentiment whose topic
                # label appears in the snippet OR that fed into this merge.
                exact_key = (canon_topic, canon_sentiment)
                review_ids: list[int] = []
                total_count = 0
                if exact_key in filtered_groups:
                    g = filtered_groups[exact_key]
                    review_ids = list(g["review_ids"])[:MAX_SAMPLES_PER_GROUP]
                    total_count = int(g["count"])
                else:
                    # Fallback: union all same-sentiment groups; the LLM
                    # merged them, so we can't cleanly attribute review IDs
                    # except by taking the largest contributor.
                    same_sent = [
                        (k, v)
                        for k, v in filtered_groups.items()
                        if k[1] == canon_sentiment
                    ]
                    if not same_sent:
                        continue
                    same_sent.sort(key=lambda kv: kv[1]["count"], reverse=True)
                    top = same_sent[0][1]
                    review_ids = list(top["review_ids"])[:MAX_SAMPLES_PER_GROUP]
                    total_count = int(top["count"])

                conn.execute(
                    """
                    INSERT INTO review_topic_summaries
                        (game_id, topic, sentiment, sample_review_ids,
                         snippet, review_count, computed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (game_id, topic, sentiment, computed_at)
                    DO UPDATE SET
                        sample_review_ids = EXCLUDED.sample_review_ids,
                        snippet = EXCLUDED.snippet,
                        review_count = EXCLUDED.review_count
                    """,
                    (
                        game_id,
                        canon_topic,
                        canon_sentiment,
                        review_ids,
                        cluster.snippet,
                        total_count,
                        today,
                    ),
                )
                summaries_written += 1

            conn.commit()
            logger.info(
                f"Game {game_id} ({game_name}): wrote {summaries_written} topic summaries."
            )
            return summaries_written

    # ------------------------------------------------------------------
    # 4. Cluster all qualifying games
    # ------------------------------------------------------------------
    async def cluster_all_games(self) -> int:
        """Run cluster_game_topics for every game with enough labeled reviews
        AND no topic summary from today.

        Returns total summaries written across all games.
        """
        with psycopg.connect(self.db_url) as conn:
            if not _table_exists(conn, "reviews") or not _table_exists(
                conn, "review_topic_summaries"
            ):
                logger.warning(
                    "reviews / review_topic_summaries table missing — skipping cluster_all_games."
                )
                return 0

            today = date.today()
            rows = conn.execute(
                """
                SELECT pl.game_id, COUNT(r.id) AS labeled_count
                FROM reviews r
                JOIN platform_listings pl ON r.platform_listing_id = pl.id
                WHERE r.sentiment IS NOT NULL
                  AND r.topics IS NOT NULL
                  AND cardinality(r.topics) > 0
                GROUP BY pl.game_id
                HAVING COUNT(r.id) >= %s
                   AND NOT EXISTS (
                        SELECT 1 FROM review_topic_summaries rts
                        WHERE rts.game_id = pl.game_id
                          AND rts.computed_at = %s
                   )
                ORDER BY labeled_count DESC
                """,
                (MIN_REVIEWS_PER_GAME, today),
            ).fetchall()

        game_ids = [r[0] for r in rows]
        if not game_ids:
            logger.info("No games need clustering today.")
            return 0

        logger.info(f"Clustering topics for {len(game_ids)} games...")
        total = 0
        for game_id in game_ids:
            try:
                total += await self.cluster_game_topics(game_id)
            except Exception as exc:
                logger.error(
                    f"cluster_game_topics failed for game {game_id}: {exc} — continuing."
                )
        return total


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _sanitize_topics(topics: list[str]) -> list[str]:
    """Lowercase, strip, validate snake_case, dedupe, cap at 3."""
    out: list[str] = []
    seen: set[str] = set()
    for t in topics or []:
        if not isinstance(t, str):
            continue
        cleaned = t.strip().lower().replace(" ", "_").replace("-", "_")
        if not cleaned:
            continue
        # Keep only alphanumerics + underscore.
        cleaned = "".join(
            ch for ch in cleaned if ch.isalnum() or ch == "_"
        )
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) >= 3:
            break
    return out


# --------------------------------------------------------------------------
# Sync entry points (callable from worker.py / scheduler.py)
# --------------------------------------------------------------------------
async def _sentiment_async(db_url: str) -> int:
    proc = ReviewNLPProcessor(db_url)
    try:
        return await proc.classify_sentiments()
    finally:
        await proc.client.close()


async def _topic_async(db_url: str) -> int:
    proc = ReviewNLPProcessor(db_url)
    try:
        return await proc.extract_topics()
    finally:
        await proc.client.close()


async def _clustering_async(db_url: str) -> int:
    proc = ReviewNLPProcessor(db_url)
    try:
        return await proc.cluster_all_games()
    finally:
        await proc.client.close()


def run_sentiment_classification(db_url: str) -> int:
    """Non-async wrapper — classify pending reviews' sentiment.

    Returns number of reviews classified.
    """
    return asyncio.run(_sentiment_async(db_url))


def run_topic_extraction(db_url: str) -> int:
    """Non-async wrapper — tag sentiment-labeled reviews with topics.

    Returns number of reviews tagged.
    """
    return asyncio.run(_topic_async(db_url))


def run_topic_clustering(db_url: str) -> int:
    """Non-async wrapper — cluster per-game topics into summaries.

    Returns total summaries written across all games.
    """
    return asyncio.run(_clustering_async(db_url))


__all__ = [
    "ReviewNLPProcessor",
    "run_sentiment_classification",
    "run_topic_extraction",
    "run_topic_clustering",
]
