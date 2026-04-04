"""Game deduplication (entity resolution) engine.

Merges the same game across different platforms into a single
canonical entity, even when names differ across platforms.
"""

from __future__ import annotations

import logging
import re
import unicodedata

import psycopg

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Normalize a game name for comparison.

    Handles:
    - Case folding
    - Unicode normalization
    - Stripping punctuation/special chars
    - Common suffix removal (: Edition, - Remastered, etc.)
    """
    # Unicode normalize
    name = unicodedata.normalize("NFKC", name)
    # Lowercase
    name = name.lower()
    # Remove common suffixes
    suffixes = [
        r"\s*[-:]\s*(free|lite|premium|pro|hd|deluxe|remastered|edition|mobile)$",
        r"\s*\(.*\)$",
        r"\s*【.*】$",
    ]
    for pattern in suffixes:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)
    # Remove special characters, keep CJK and alphanumeric
    name = re.sub(r"[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]", "", name)
    return name.strip()


class DeduplicationEngine:
    """Resolves game entities across platforms."""

    def __init__(self, db_url: str):
        self.db_url = db_url

    def find_or_create_game(
        self,
        conn: psycopg.Connection,
        name: str,
        developer: str | None = None,
        genre: str | None = None,
        platform: str | None = None,
        platform_id: str | None = None,
    ) -> int:
        """Find an existing canonical game or create a new one.

        Three-tier matching:
        1. Exact match: same developer + normalized name
        2. Fuzzy match: pg_trgm similarity > 0.85 on name, same genre
        3. Create new if no match found
        """
        norm_name = normalize_name(name)

        # Tier 1: Exact match on normalized name + developer
        if developer:
            row = conn.execute(
                """
                SELECT g.id FROM games g
                JOIN platform_listings pl ON g.id = pl.game_id
                WHERE g.developer = %s
                AND (
                    lower(g.name_zh) = %s
                    OR lower(g.name_en) = %s
                )
                LIMIT 1
                """,
                (developer, norm_name, norm_name),
            ).fetchone()

            if row:
                return row[0]

        # Tier 1b: Exact normalized name match (any developer)
        row = conn.execute(
            """
            SELECT id FROM games
            WHERE lower(COALESCE(name_zh, '')) = %s
               OR lower(COALESCE(name_en, '')) = %s
            LIMIT 1
            """,
            (norm_name, norm_name),
        ).fetchone()

        if row:
            return row[0]

        # Tier 2: Fuzzy match using pg_trgm (if extension is available)
        try:
            row = conn.execute(
                """
                SELECT id, similarity(lower(COALESCE(name_en, name_zh, '')), %s) AS sim
                FROM games
                WHERE similarity(lower(COALESCE(name_en, name_zh, '')), %s) > 0.85
                ORDER BY sim DESC
                LIMIT 1
                """,
                (norm_name, norm_name),
            ).fetchone()

            if row:
                logger.info(
                    f"Fuzzy matched '{name}' to game #{row[0]} "
                    f"(similarity: {row[1]:.2f})"
                )
                return row[0]
        except psycopg.errors.UndefinedFunction:
            # pg_trgm extension not installed, skip fuzzy matching
            pass

        # Tier 3: Create new game entity
        # Detect language for name_zh vs name_en
        has_cjk = bool(re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", name))
        name_zh = name if has_cjk else None
        name_en = name if not has_cjk else None

        row = conn.execute(
            """
            INSERT INTO games (name_zh, name_en, developer, genre)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (name_zh, name_en, developer, genre),
        ).fetchone()

        game_id = row[0]
        logger.info(f"Created new game entity #{game_id}: {name}")
        return game_id

    def link_platform_listing(
        self,
        conn: psycopg.Connection,
        game_id: int,
        platform: str,
        platform_id: str,
        name: str,
        rating: float | None = None,
        rating_count: int | None = None,
        download_est: int | None = None,
        url: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        """Link or update a platform listing to a canonical game."""
        import json

        row = conn.execute(
            """
            INSERT INTO platform_listings
            (game_id, platform, platform_id, name, rating, rating_count,
             download_est, platform_url, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (platform, platform_id) DO UPDATE SET
                game_id = EXCLUDED.game_id,
                name = EXCLUDED.name,
                rating = COALESCE(EXCLUDED.rating, platform_listings.rating),
                rating_count = COALESCE(EXCLUDED.rating_count, platform_listings.rating_count),
                download_est = COALESCE(EXCLUDED.download_est, platform_listings.download_est),
                platform_url = COALESCE(EXCLUDED.platform_url, platform_listings.platform_url),
                metadata = COALESCE(EXCLUDED.metadata, platform_listings.metadata),
                last_updated = CURRENT_DATE
            RETURNING id
            """,
            (
                game_id, platform, platform_id, name,
                rating, rating_count, download_est, url,
                json.dumps(metadata or {}),
            ),
        ).fetchone()

        return row[0]

    def process_ranking_entries(
        self,
        conn: psycopg.Connection,
        entries: list,
        platform: str,
    ) -> int:
        """Process a batch of ranking entries: dedup, link, and store snapshots."""
        from datetime import date

        count = 0
        today = date.today()

        for entry in entries:
            # Find or create canonical game
            game_id = self.find_or_create_game(
                conn,
                name=entry.name,
                developer=entry.developer,
                genre=entry.genre,
                platform=platform,
                platform_id=entry.platform_id,
            )

            # Link platform listing
            listing_id = self.link_platform_listing(
                conn,
                game_id=game_id,
                platform=platform,
                platform_id=entry.platform_id,
                name=entry.name,
                rating=entry.rating,
                rating_count=entry.rating_count,
                download_est=entry.download_est,
                url=entry.url,
                metadata=entry.metadata,
            )

            # Get previous rank for change calculation
            prev = conn.execute(
                """
                SELECT rank_position FROM ranking_snapshots
                WHERE platform_listing_id = %s
                  AND chart_type = %s
                  AND region = %s
                  AND snapshot_date < %s
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                (listing_id, entry.chart_type, entry.region, today),
            ).fetchone()

            previous_rank = prev[0] if prev else None
            rank_change = (
                (previous_rank - entry.rank_position) if previous_rank else None
            )

            # Insert ranking snapshot
            conn.execute(
                """
                INSERT INTO ranking_snapshots
                (platform_listing_id, chart_type, region,
                 rank_position, previous_rank, rank_change, snapshot_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (platform_listing_id, chart_type, region, snapshot_date)
                DO UPDATE SET
                    rank_position = EXCLUDED.rank_position,
                    previous_rank = EXCLUDED.previous_rank,
                    rank_change = EXCLUDED.rank_change
                """,
                (
                    listing_id, entry.chart_type, entry.region,
                    entry.rank_position, previous_rank, rank_change, today,
                ),
            )

            count += 1

        conn.commit()
        return count
