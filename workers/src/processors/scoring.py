"""IAA Potential Scoring Engine.

Computes a 0-100 score across 7 dimensions to identify games
with the highest potential for IAA (In-App Advertising) adaptation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

# Load scoring weights from shared config
SHARED_DIR = Path(__file__).parent.parent.parent.parent / "shared"


def load_weights() -> dict:
    with open(SHARED_DIR / "scoring_weights.json") as f:
        return json.load(f)


def load_genres() -> dict:
    with open(SHARED_DIR / "genres.json") as f:
        return json.load(f)["genres"]


@dataclass
class ScoreBreakdown:
    """Breakdown of a game's IAA potential score."""

    game_id: int
    overall_score: int
    ranking_velocity: int
    genre_fit: int
    social_buzz: int
    cross_platform: int
    rating_quality: int
    competition_gap: int
    ad_activity: int
    algorithm_version: str = "v1"


class ScoringEngine:
    """Computes IAA potential scores for games."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.config = load_weights()
        self.genres = load_genres()
        self.weights = self.config["weights"]

    def score_game(self, game_id: int, conn: psycopg.Connection) -> ScoreBreakdown:
        """Compute the full score for a single game."""
        rv = self._calc_ranking_velocity(game_id, conn)
        gf = self._calc_genre_fit(game_id, conn)
        sb = self._calc_social_buzz(game_id, conn)
        cp = self._calc_cross_platform(game_id, conn)
        rq = self._calc_rating_quality(game_id, conn)
        cg = self._calc_competition_gap(game_id, conn)
        aa = self._calc_ad_activity(game_id, conn)

        overall = int(
            rv * self.weights["ranking_velocity"]
            + gf * self.weights["genre_fit"]
            + sb * self.weights["social_buzz"]
            + cp * self.weights["cross_platform"]
            + rq * self.weights["rating_quality"]
            + cg * self.weights["competition_gap"]
            + aa * self.weights["ad_activity"]
        )

        return ScoreBreakdown(
            game_id=game_id,
            overall_score=min(100, max(0, overall)),
            ranking_velocity=rv,
            genre_fit=gf,
            social_buzz=sb,
            cross_platform=cp,
            rating_quality=rq,
            competition_gap=cg,
            ad_activity=aa,
            algorithm_version=self.config["version"],
        )

    def _calc_ranking_velocity(self, game_id: int, conn: psycopg.Connection) -> int:
        """Calculate ranking velocity score (0-100).

        Measures how fast a game is climbing charts over a 14-day window.
        A game rising from #200 to #50 in 7 days gets a very high score.
        """
        window_days = self.config["ranking_velocity_params"]["window_days"]
        min_points = self.config["ranking_velocity_params"]["min_data_points"]
        cutoff = date.today() - timedelta(days=window_days)

        rows = conn.execute(
            """
            SELECT rs.rank_position, rs.snapshot_date
            FROM ranking_snapshots rs
            JOIN platform_listings pl ON rs.platform_listing_id = pl.id
            WHERE pl.game_id = %s AND rs.snapshot_date >= %s
            ORDER BY rs.snapshot_date ASC
            """,
            (game_id, cutoff),
        ).fetchall()

        if len(rows) < min_points:
            return 0

        # Linear regression on rank positions over time
        # Lower rank = better, so negative slope = game is rising
        positions = [r[0] for r in rows]
        days = [(r[1] - cutoff).days for r in rows]

        if len(set(days)) < 2:
            return 0

        # Simple linear regression
        n = len(positions)
        sum_x = sum(days)
        sum_y = sum(positions)
        sum_xy = sum(x * y for x, y in zip(days, positions))
        sum_x2 = sum(x * x for x in days)

        denom = n * sum_x2 - sum_x * sum_x
        if denom == 0:
            return 0

        slope = (n * sum_xy - sum_x * sum_y) / denom

        # Negative slope = rising (rank number decreasing)
        # Normalize: slope of -10/day (rising ~10 positions daily) = 100
        velocity_score = int(min(100, max(0, -slope * 10)))

        # Bonus for large rank improvements
        first_rank = positions[0]
        last_rank = positions[-1]
        improvement = first_rank - last_rank
        if improvement > 50:
            velocity_score = min(100, velocity_score + 20)

        return velocity_score

    def _calc_genre_fit(self, game_id: int, conn: psycopg.Connection) -> int:
        """Calculate genre IAA fit score (0-100).

        Lookup table mapping genres to IAA suitability.
        Idle/merge/match3 score highest.
        """
        row = conn.execute(
            "SELECT genre, gameplay_tags FROM games WHERE id = %s", (game_id,)
        ).fetchone()

        if not row:
            return 50  # Unknown genre, middle score

        genre = (row[0] or "").lower().strip()
        tags = row[1] or []

        # Check genre directly (exact match first, then word-boundary)
        best_score = 50  # default for unknown
        if genre in self.genres:
            best_score = max(best_score, self.genres[genre]["iaa_score"])
        else:
            for key, info in self.genres.items():
                if re.search(r'(?:^|[\s\-_])' + re.escape(key) + r'(?:$|[\s\-_])', genre):
                    best_score = max(best_score, info["iaa_score"])

        # Also check gameplay tags (exact match first, then word-boundary)
        for tag in tags:
            tag_lower = tag.lower().strip()
            if tag_lower in self.genres:
                best_score = max(best_score, self.genres[tag_lower]["iaa_score"])
            else:
                for key, info in self.genres.items():
                    if re.search(r'(?:^|[\s\-_])' + re.escape(key) + r'(?:$|[\s\-_])', tag_lower):
                        best_score = max(best_score, info["iaa_score"])

        return best_score

    def _calc_social_buzz(self, game_id: int, conn: psycopg.Connection) -> int:
        """Calculate social media buzz score (0-100).

        Based on video counts and view velocity across platforms.
        """
        recent = date.today() - timedelta(days=7)

        rows = conn.execute(
            """
            SELECT platform, SUM(video_count), SUM(view_count), SUM(like_count)
            FROM social_signals
            WHERE game_id = %s AND signal_date >= %s
            GROUP BY platform
            """,
            (game_id, recent),
        ).fetchall()

        if not rows:
            return 0

        platform_weights = self.config["social_buzz_params"]["platform_weights"]
        total_score = 0.0
        max_possible = 0.0

        for platform, video_count, view_count, like_count in rows:
            weight = float(platform_weights.get(platform, 0.5))
            max_possible += weight * 100

            # SUM() on BIGINT returns numeric → Decimal in psycopg3.
            # Cast to int up front so all downstream arithmetic is native.
            vc = int(view_count or 0)
            vid = int(video_count or 0)

            # Normalize views: 100K views = 50 score, 1M+ = 100
            view_score = min(100.0, vc / 10000.0)
            # Video count bonus: more creators = more organic
            video_bonus = min(30, vid * 2)

            platform_score = min(100.0, view_score + video_bonus)
            total_score += platform_score * weight

        if max_possible == 0:
            return 0

        return int(total_score / max_possible * 100)

    def _calc_cross_platform(self, game_id: int, conn: psycopg.Connection) -> int:
        """Calculate cross-platform presence score (0-100).

        Games on more platforms demonstrate universal appeal.
        """
        rows = conn.execute(
            "SELECT platform FROM platform_listings WHERE game_id = %s",
            (game_id,),
        ).fetchall()

        platforms = {r[0] for r in rows}

        # Weighted platform points
        platform_points = {
            "google_play": 25,
            "app_store": 25,
            "steam": 20,
            "taptap": 15,
            "poki": 10,
            "crazygames": 10,
            "wechat_mini": 15,
        }

        score = sum(platform_points.get(p, 5) for p in platforms)
        return min(100, score)

    def _calc_rating_quality(self, game_id: int, conn: psycopg.Connection) -> int:
        """Calculate rating quality score (0-100).

        High rating + many reviews = quality worth porting.
        """
        rows = conn.execute(
            """
            SELECT rating, rating_count
            FROM platform_listings
            WHERE game_id = %s AND rating IS NOT NULL
            """,
            (game_id,),
        ).fetchall()

        if not rows:
            return 0

        # Use the best rating with sufficient reviews
        best_score = 0
        for rating, count in rows:
            if rating is None:
                continue

            # rating column is DECIMAL — convert to float up front so every
            # downstream operation is native python and never mixes Decimal
            # with float weights / divisors.
            rating_f = float(rating)
            count = int(count or 0)

            # Normalize rating to 0-60 (rating out of 5)
            rating_norm = min(60.0, (rating_f / 5.0) * 60.0)

            # Review count bonus: 1K = +10, 10K = +25, 100K+ = +40
            if count >= 100000:
                count_bonus = 40.0
            elif count >= 10000:
                count_bonus = 25.0
            elif count >= 1000:
                count_bonus = 10.0
            else:
                count_bonus = max(0.0, count / 100.0)

            total = int(rating_norm + count_bonus)
            best_score = max(best_score, total)

        return min(100, best_score)

    def _calc_competition_gap(self, game_id: int, conn: psycopg.Connection) -> int:
        """Calculate competition gap score (0-100).

        Fewer existing IAA adaptations = bigger opportunity.
        """
        row = conn.execute(
            "SELECT genre, gameplay_tags FROM games WHERE id = %s", (game_id,)
        ).fetchone()

        if not row or not row[0]:
            return 50  # Unknown, assume moderate competition

        genre = row[0]

        # Count games in same genre that are already tagged as IAA adaptations
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM games g
            JOIN game_tags gt ON g.id = gt.game_id
            WHERE g.genre = %s AND gt.tag = 'iaa_adapted'
            """,
            (genre,),
        ).fetchone()[0]

        # Fewer competitors = higher score
        if count == 0:
            return 100
        elif count <= 3:
            return 80
        elif count <= 10:
            return 60
        elif count <= 20:
            return 40
        else:
            return 20

    def _calc_ad_activity(self, game_id: int, conn: psycopg.Connection) -> int:
        """Calculate ad activity score (0-100).

        Others advertising similar games = validated market demand.
        """
        recent = date.today() - timedelta(days=14)

        rows = conn.execute(
            """
            SELECT SUM(active_creatives)
            FROM ad_intelligence
            WHERE game_id = %s AND signal_date >= %s
            """,
            (game_id, recent),
        ).fetchall()

        if not rows or not rows[0][0]:
            return 0

        creatives = rows[0][0] or 0
        # More creatives = more validated
        if creatives >= 100:
            return 100
        elif creatives >= 50:
            return 80
        elif creatives >= 20:
            return 60
        elif creatives >= 5:
            return 40
        else:
            return 20

    def score_all_games(self) -> list[ScoreBreakdown]:
        """Score all games in the database."""
        scores = []
        with psycopg.connect(self.db_url) as conn:
            game_ids = conn.execute("SELECT id FROM games").fetchall()

            for (game_id,) in game_ids:
                try:
                    score = self.score_game(game_id, conn)
                    scores.append(score)
                except Exception as e:
                    logger.error(f"Scoring failed for game {game_id}: {e}")
                    conn.rollback()

            # Write scores to database
            for s in scores:
                conn.execute(
                    """
                    INSERT INTO potential_scores
                    (game_id, overall_score, ranking_velocity, genre_fit,
                     social_buzz, cross_platform, rating_quality,
                     competition_gap, ad_activity, algorithm_version, scored_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (game_id, scored_at) DO UPDATE SET
                        overall_score = EXCLUDED.overall_score,
                        ranking_velocity = EXCLUDED.ranking_velocity,
                        genre_fit = EXCLUDED.genre_fit,
                        social_buzz = EXCLUDED.social_buzz,
                        cross_platform = EXCLUDED.cross_platform,
                        rating_quality = EXCLUDED.rating_quality,
                        competition_gap = EXCLUDED.competition_gap,
                        ad_activity = EXCLUDED.ad_activity,
                        algorithm_version = EXCLUDED.algorithm_version
                    """,
                    (
                        s.game_id, s.overall_score, s.ranking_velocity,
                        s.genre_fit, s.social_buzz, s.cross_platform,
                        s.rating_quality, s.competition_gap, s.ad_activity,
                        s.algorithm_version, date.today(),
                    ),
                )
            conn.commit()

        logger.info(f"Scored {len(scores)} games")
        return scores
