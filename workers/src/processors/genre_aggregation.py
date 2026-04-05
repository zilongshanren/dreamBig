"""Daily Genre rollup aggregator.

For each genre key in shared/genres.json, computes:
  - hotGamesCount: games in this genre with today's overall_score >= 60
  - momentum: avg(today_score - 7d_ago_score) across genre's active games
  - topGameIds: top 10 game IDs by today's overall_score
  - iaaBaseline: from shared/genres.json (static)
  - labelZh / labelEn: from shared/genres.json

Writes (UPSERT) into the `genres` table.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

SHARED_DIR = Path(__file__).parent.parent.parent.parent / "shared"
HOT_THRESHOLD = 60
TOP_N = 10
MOMENTUM_WINDOW_DAYS = 7


def load_genres() -> dict:
    """Load genre labels + IAA baseline scores from shared config."""
    with open(SHARED_DIR / "genres.json") as f:
        return json.load(f)["genres"]


def _genre_matches(game_genre: str | None, tags: list[str] | None, key: str) -> bool:
    """Whether a game belongs to the given genre key.

    Matches if the key appears as an exact/word-boundary substring in either
    the genre field or one of the gameplay tags.
    """
    pattern = re.compile(
        r"(?:^|[\s\-_])" + re.escape(key) + r"(?:$|[\s\-_])", re.IGNORECASE
    )

    if game_genre:
        g = game_genre.strip().lower()
        if g == key:
            return True
        if pattern.search(g):
            return True

    for t in tags or []:
        if not t:
            continue
        t_low = t.strip().lower()
        if t_low == key:
            return True
        if pattern.search(t_low):
            return True

    return False


class GenreAggregator:
    """Updates the `genres` table with daily rollup stats."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.genres = load_genres()

    def _load_game_scores(
        self,
        conn: psycopg.Connection,
        today: date,
    ) -> list[tuple[int, str | None, list[str] | None, int]]:
        """Return (game_id, genre, tags, overall_score) for today's scores."""
        rows = conn.execute(
            """
            SELECT g.id, g.genre, g.gameplay_tags, ps.overall_score
            FROM games g
            JOIN potential_scores ps ON ps.game_id = g.id
            WHERE ps.scored_at = %s
            """,
            (today,),
        ).fetchall()
        return rows  # type: ignore[return-value]

    def _load_prior_scores(
        self,
        conn: psycopg.Connection,
        prior: date,
    ) -> dict[int, int]:
        """Return {game_id: overall_score} for the given prior date (or the
        closest available snapshot within 14 days before).
        """
        rows = conn.execute(
            """
            SELECT DISTINCT ON (game_id) game_id, overall_score
            FROM potential_scores
            WHERE scored_at <= %s
              AND scored_at >= %s
            ORDER BY game_id, scored_at DESC
            """,
            (prior, prior - timedelta(days=14)),
        ).fetchall()
        return {gid: score for gid, score in rows}

    def refresh(self) -> int:
        """Recompute and UPSERT all genre rows. Returns number of rows written."""
        today = date.today()
        prior = today - timedelta(days=MOMENTUM_WINDOW_DAYS)

        written = 0
        with psycopg.connect(self.db_url) as conn:
            today_rows = self._load_game_scores(conn, today)
            if not today_rows:
                logger.warning(
                    f"No potential_scores for {today}, genre aggregation skipped"
                )
                return 0

            prior_scores = self._load_prior_scores(conn, prior)

            for key, info in self.genres.items():
                # Filter games that belong to this genre
                in_genre = [
                    (gid, score)
                    for gid, genre, tags, score in today_rows
                    if _genre_matches(genre, tags, key)
                ]

                hot_count = sum(1 for _, s in in_genre if s >= HOT_THRESHOLD)

                # Momentum: average delta over last 7 days
                deltas: list[int] = []
                for gid, score in in_genre:
                    before = prior_scores.get(gid)
                    if before is not None:
                        deltas.append(score - before)

                momentum = (
                    Decimal(sum(deltas)) / Decimal(len(deltas))
                    if deltas
                    else Decimal(0)
                )
                momentum = momentum.quantize(Decimal("0.001"))

                # Top game IDs by today's score
                top_ids = [
                    gid
                    for gid, _ in sorted(in_genre, key=lambda x: x[1], reverse=True)[
                        :TOP_N
                    ]
                ]

                conn.execute(
                    """
                    INSERT INTO genres
                        (key, label_zh, label_en, iaa_baseline,
                         hot_games_count, momentum, top_game_ids, last_computed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE SET
                        label_zh = EXCLUDED.label_zh,
                        label_en = EXCLUDED.label_en,
                        iaa_baseline = EXCLUDED.iaa_baseline,
                        hot_games_count = EXCLUDED.hot_games_count,
                        momentum = EXCLUDED.momentum,
                        top_game_ids = EXCLUDED.top_game_ids,
                        last_computed_at = EXCLUDED.last_computed_at
                    """,
                    (
                        key,
                        info.get("label_zh", key),
                        info.get("label_en", key),
                        int(info.get("iaa_score", 0)),
                        hot_count,
                        momentum,
                        top_ids,
                    ),
                )
                written += 1
                logger.info(
                    f"Genre '{key}': hot={hot_count}, momentum={momentum}, "
                    f"top_ids={top_ids[:3]}..."
                )

            conn.commit()

        logger.info(f"Genre aggregation complete: {written} genres written")
        return written


def run_genre_aggregation(db_url: str) -> int:
    """Entry function for worker.py."""
    aggregator = GenreAggregator(db_url)
    return aggregator.refresh()
