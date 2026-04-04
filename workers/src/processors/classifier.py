"""Auto-classifier for game genres and gameplay mechanics.

Maps platform-specific genre labels to our standardized genre taxonomy,
and assigns IAA suitability scores.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

SHARED_DIR = Path(__file__).parent.parent.parent.parent / "shared"

# Mapping from platform-specific genre labels to our taxonomy
GENRE_MAPPING = {
    # Google Play categories
    "action": "casual_action",
    "adventure": "adventure",
    "arcade": "arcade",
    "board": "board",
    "card": "card",
    "casino": "card",
    "casual": "casual_action",
    "educational": "puzzle",
    "music": "arcade",
    "puzzle": "puzzle",
    "racing": "racing",
    "role playing": "rpg",
    "simulation": "simulation",
    "sports": "sports",
    "strategy": "strategy",
    "trivia": "trivia",
    "word": "word",
    # Common tags
    "idle": "idle",
    "merge": "merge",
    "match-3": "match3",
    "match 3": "match3",
    "三消": "match3",
    "放置": "idle",
    "合成": "merge",
    "益智": "puzzle",
    "休闲": "casual_action",
    "动作": "casual_action",
    "跑酷": "runner",
    "塔防": "tower_defense",
    "模拟": "simulation",
    "策略": "strategy",
    "角色扮演": "rpg",
    "射击": "shooter",
    "竞速": "racing",
    "体育": "sports",
    "棋牌": "board",
    "卡牌": "card",
    "文字": "word",
    "冒险": "adventure",
    "runner": "runner",
    "tower defense": "tower_defense",
    "clicker": "idle",
    "tapper": "idle",
    "hyper casual": "casual_action",
    "hypercasual": "casual_action",
}


def classify_genre(genre_str: str | None, tags: list[str] | None = None) -> str | None:
    """Map a platform genre string to our standard taxonomy."""
    if not genre_str and not tags:
        return None

    # Check tags first (more specific)
    if tags:
        for tag in tags:
            normalized = tag.lower().strip()
            if normalized in GENRE_MAPPING:
                return GENRE_MAPPING[normalized]

    # Then check genre string
    if genre_str:
        normalized = genre_str.lower().strip()
        if normalized in GENRE_MAPPING:
            return GENRE_MAPPING[normalized]

        # Partial match
        for key, value in GENRE_MAPPING.items():
            if key in normalized or normalized in key:
                return value

    return None


def get_iaa_score(genre: str | None) -> int:
    """Get the IAA suitability score for a genre."""
    if not genre:
        return 50

    with open(SHARED_DIR / "genres.json") as f:
        genres = json.load(f)["genres"]

    return genres.get(genre, {}).get("iaa_score", 50)


def classify_and_update(db_url: str) -> int:
    """Classify all games that don't have a genre yet."""
    updated = 0

    with psycopg.connect(db_url) as conn:
        # Find games without classification
        rows = conn.execute(
            """
            SELECT g.id, g.genre, g.gameplay_tags,
                   array_agg(DISTINCT pl.metadata->>'genre') as platform_genres
            FROM games g
            LEFT JOIN platform_listings pl ON g.id = pl.game_id
            WHERE g.iaa_suitability = 0 OR g.genre IS NULL
            GROUP BY g.id
            """
        ).fetchall()

        for game_id, current_genre, tags, platform_genres in rows:
            # Flatten all available genre info
            all_tags = list(tags or [])
            for pg in (platform_genres or []):
                if pg:
                    all_tags.append(pg)

            genre = classify_genre(current_genre, all_tags) or current_genre
            iaa_score = get_iaa_score(genre)

            if genre or iaa_score != 0:
                conn.execute(
                    """
                    UPDATE games
                    SET genre = COALESCE(%s, genre),
                        iaa_suitability = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (genre, iaa_score, game_id),
                )
                updated += 1

        conn.commit()

    logger.info(f"Classified {updated} games")
    return updated
