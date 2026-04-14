"""Game deduplication (entity resolution) engine.

Merges the same game across different platforms into a single
canonical entity, even when names differ across platforms.
"""

from __future__ import annotations

import logging
import random
import re
import time
import unicodedata

import psycopg

logger = logging.getLogger(__name__)

# Batch commit frequency: commit every N entries instead of the whole batch.
# Shorter transactions = shorter lock hold time = fewer deadlocks when two
# Google Play regions (US + JP) are dequeued at the same minute.
RANKING_COMMIT_BATCH = 10

# Retry policy for transient concurrency errors (deadlock, unique violation).
# These can happen when two workers race on the same platform_listings row
# before either has committed — advisory lock + retry solves it.
MAX_RETRIES = 3
INITIAL_BACKOFF_SEC = 0.2


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

    def _process_single_entry(
        self,
        conn: psycopg.Connection,
        entry,
        platform: str,
        today,
    ) -> None:
        """Upsert one ranking entry (game + listing + snapshot).

        Assumes caller holds the transaction and will commit in batches.
        """
        game_id = self.find_or_create_game(
            conn,
            name=entry.name,
            developer=entry.developer,
            genre=entry.genre,
            platform=platform,
            platform_id=entry.platform_id,
        )

        if getattr(entry, "icon_url", None):
            conn.execute(
                """
                UPDATE games SET thumbnail_url = %s
                WHERE id = %s AND thumbnail_url IS NULL
                """,
                (entry.icon_url, game_id),
            )

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

    def process_ranking_entries(
        self,
        conn: psycopg.Connection,
        entries: list,
        platform: str,
    ) -> int:
        """Process a batch of ranking entries: dedup, link, and store snapshots.

        Concurrency-safe design:
        1. Entries are sorted by platform_id so concurrent workers acquire
           row locks in the same order, eliminating a class of deadlocks.
        2. A transaction-level advisory lock keyed on the platform serializes
           writes from multiple workers on the same platform — small cost,
           huge reliability win.
        3. Each entry is committed in a small batch (RANKING_COMMIT_BATCH)
           rather than holding one giant transaction for 100 rows. Shorter
           tx = shorter lock window.
        4. Each per-entry upsert is retried with backoff on DeadlockDetected
           or UniqueViolation — these are transient and the operations are
           idempotent.
        """
        from datetime import date

        today = date.today()
        # Sort for consistent lock-acquisition order across concurrent workers.
        sorted_entries = sorted(entries, key=lambda e: e.platform_id)
        count = 0
        pending = 0

        # Transaction-level advisory lock — auto-released on commit/rollback.
        # Keyed by platform hash so different platforms don't block each other.
        lock_key = hash(f"ranking:{platform}") & 0x7FFFFFFF
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))

        for entry in sorted_entries:
            for attempt in range(MAX_RETRIES):
                try:
                    self._process_single_entry(conn, entry, platform, today)
                    break
                except (
                    psycopg.errors.DeadlockDetected,
                    psycopg.errors.UniqueViolation,
                    psycopg.errors.SerializationFailure,
                ) as e:
                    conn.rollback()
                    # Re-acquire advisory lock after rollback.
                    conn.execute(
                        "SELECT pg_advisory_xact_lock(%s)", (lock_key,)
                    )
                    if attempt == MAX_RETRIES - 1:
                        logger.error(
                            f"[{platform}] giving up on entry "
                            f"{entry.platform_id} after {MAX_RETRIES} retries: {e}"
                        )
                        # Skip this one entry, keep processing the rest.
                        break
                    backoff = INITIAL_BACKOFF_SEC * (2 ** attempt) + random.random() * 0.1
                    logger.warning(
                        f"[{platform}] retry {attempt + 1}/{MAX_RETRIES} on "
                        f"{entry.platform_id} after {e.__class__.__name__}; "
                        f"sleeping {backoff:.2f}s"
                    )
                    time.sleep(backoff)
            else:
                continue

            count += 1
            pending += 1
            if pending >= RANKING_COMMIT_BATCH:
                conn.commit()
                pending = 0
                # Re-acquire advisory lock for the next batch's transaction.
                conn.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (lock_key,)
                )

        if pending > 0:
            conn.commit()

        return count
