"""Generate GameEmbedding vectors for all games.

Embedding source text = name + genre + gameplay tags + positioning + core loop
+ top review topic snippets. Writes to the `game_embeddings` table (pgvector).

The generated vectors power the /api/games/[id]/similar endpoint via cosine
similarity (pgvector's <=> operator).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

import psycopg

from src.llm.embedding_client import DIM, EmbeddingClient

logger = logging.getLogger(__name__)


# How much each section contributes to the embedding text
MAX_TAGS = 12
MAX_TOPICS = 6


class GameEmbeddingGenerator:
    """Generate and persist pgvector embeddings for games."""

    def __init__(self, db_url: str, client: EmbeddingClient | None = None):
        self.db_url = db_url
        self.client = client or EmbeddingClient()

    # ------------------------------------------------------------------
    # Text assembly
    # ------------------------------------------------------------------
    def _build_text(
        self,
        name: str,
        genre: str | None,
        tags: list[str] | None,
        positioning: str | None,
        core_loop: str | None,
        topics: list[str] | None,
    ) -> str:
        """Assemble stable, compact description of a game for embedding."""
        parts: list[str] = [name.strip()]

        if genre:
            parts.append(f"genre: {genre.strip()}")

        if tags:
            tag_str = ", ".join(t.strip() for t in tags[:MAX_TAGS] if t and t.strip())
            if tag_str:
                parts.append(f"tags: {tag_str}")

        if positioning:
            parts.append(f"positioning: {positioning.strip()[:400]}")

        if core_loop:
            parts.append(f"core loop: {core_loop.strip()[:400]}")

        if topics:
            topic_str = " | ".join(t.strip() for t in topics[:MAX_TOPICS] if t and t.strip())
            if topic_str:
                parts.append(f"top topics: {topic_str[:500]}")

        return " \u2502 ".join(parts)

    def _load_game_row(
        self, conn: psycopg.Connection, game_id: int
    ) -> tuple[int, str, str | None, list[str] | None, str | None, str | None] | None:
        """Fetch the minimum fields needed to build embedding text."""
        row = conn.execute(
            """
            SELECT id,
                   COALESCE(name_zh, name_en) AS name,
                   genre,
                   gameplay_tags,
                   positioning,
                   core_loop
            FROM games
            WHERE id = %s
              AND (name_zh IS NOT NULL OR name_en IS NOT NULL)
            """,
            (game_id,),
        ).fetchone()
        return row  # type: ignore[return-value]

    def _load_top_topics(
        self, conn: psycopg.Connection, game_id: int
    ) -> list[str]:
        """Top review topic snippets for this game (positive + negative)."""
        rows = conn.execute(
            """
            SELECT topic, snippet
            FROM review_topic_summaries
            WHERE game_id = %s
            ORDER BY review_count DESC
            LIMIT %s
            """,
            (game_id, MAX_TOPICS),
        ).fetchall()
        out: list[str] = []
        for topic, snippet in rows:
            if topic and snippet:
                out.append(f"{topic}: {snippet[:120]}")
            elif topic:
                out.append(str(topic))
        return out

    def _save_embedding(
        self,
        conn: psycopg.Connection,
        game_id: int,
        embedding: list[float],
        source: str,
    ) -> None:
        """UPSERT the vector into game_embeddings."""
        # pgvector accepts text representation like '[0.1,0.2,...]'
        vec_str = "[" + ",".join(f"{v:.7f}" for v in embedding) + "]"
        conn.execute(
            """
            INSERT INTO game_embeddings (game_id, embedding, source, dim, computed_at)
            VALUES (%s, %s::vector, %s, %s, NOW())
            ON CONFLICT (game_id) DO UPDATE SET
                embedding = EXCLUDED.embedding,
                source = EXCLUDED.source,
                dim = EXCLUDED.dim,
                computed_at = EXCLUDED.computed_at
            """,
            (game_id, vec_str, source, DIM),
        )

    # ------------------------------------------------------------------
    # Per-game generation
    # ------------------------------------------------------------------
    async def generate_for_game(self, game_id: int) -> bool:
        """Generate and persist embedding for a single game. Returns True on success."""
        with psycopg.connect(self.db_url) as conn:
            row = self._load_game_row(conn, game_id)
            if not row:
                logger.info(f"Game {game_id} has no name, skipping embedding")
                return False

            _id, name, genre, tags, positioning, core_loop = row
            topics = self._load_top_topics(conn, game_id)
            text = self._build_text(name, genre, tags, positioning, core_loop, topics)

            try:
                vec = await self.client.embed(text)
            except Exception as exc:
                logger.error(f"Embedding failed for game {game_id}: {exc}")
                return False

            self._save_embedding(conn, game_id, vec, "name+genre+topics")
            conn.commit()
            return True

    # ------------------------------------------------------------------
    # Bulk refresh
    # ------------------------------------------------------------------
    async def refresh_stale(
        self, max_age_days: int = 7, limit: int = 200
    ) -> int:
        """Regenerate embeddings for games with no embedding or stale (> max_age_days).

        Returns number of embeddings written.
        """
        cutoff = date.today() - timedelta(days=max_age_days)

        with psycopg.connect(self.db_url) as conn:
            rows = conn.execute(
                """
                SELECT g.id,
                       COALESCE(g.name_zh, g.name_en) AS name,
                       g.genre,
                       g.gameplay_tags,
                       g.positioning,
                       g.core_loop
                FROM games g
                LEFT JOIN game_embeddings ge ON ge.game_id = g.id
                WHERE (g.name_zh IS NOT NULL OR g.name_en IS NOT NULL)
                  AND (
                      ge.game_id IS NULL
                      OR ge.computed_at < %s
                  )
                ORDER BY ge.computed_at NULLS FIRST, g.id
                LIMIT %s
                """,
                (cutoff, limit),
            ).fetchall()

            if not rows:
                logger.info("No stale embeddings to refresh")
                return 0

            logger.info(f"Refreshing {len(rows)} game embeddings")

            # Build text per game (pull topics per game one at a time)
            texts: list[str] = []
            game_ids: list[int] = []
            for game_id, name, genre, tags, positioning, core_loop in rows:
                topics = self._load_top_topics(conn, game_id)
                text = self._build_text(name, genre, tags, positioning, core_loop, topics)
                texts.append(text)
                game_ids.append(game_id)

            # Batch embed
            try:
                vectors = await self.client.embed_batch(texts, batch_size=64)
            except Exception as exc:
                logger.error(f"Batch embedding failed: {exc}")
                return 0

            # Persist
            written = 0
            for gid, vec in zip(game_ids, vectors):
                try:
                    self._save_embedding(conn, gid, vec, "name+genre+topics")
                    written += 1
                except Exception as exc:
                    logger.warning(f"Failed to save embedding for game {gid}: {exc}")
                    conn.rollback()
                    continue

            conn.commit()
            logger.info(f"Wrote {written} game embeddings")
            return written


# ---------------------------------------------------------------------
# Entry points for worker.py
# ---------------------------------------------------------------------
async def _refresh_async(db_url: str, limit: int) -> int:
    gen = GameEmbeddingGenerator(db_url)
    try:
        return await gen.refresh_stale(limit=limit)
    finally:
        await gen.client.close()


def run_embedding_refresh(db_url: str, limit: int = 200) -> int:
    """Entry function for worker.py — refreshes stale embeddings."""
    return asyncio.run(_refresh_async(db_url, limit))
