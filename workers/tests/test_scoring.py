"""Unit tests for :mod:`src.processors.scoring` — the IAA potential engine.

All tests use ``FakeConnection`` so there's no real DB round-trip. Each test
queues rows in the exact order ScoringEngine's private ``_calc_*`` methods
issue their SELECTs, then inspects the returned score.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.processors.scoring import ScoringEngine


@pytest.fixture
def engine():
    return ScoringEngine(db_url="fake")


# ---------------------------------------------------------------------------
# _calc_ranking_velocity
# ---------------------------------------------------------------------------
class TestRankingVelocity:
    def test_rising_trend_returns_positive_score(self, engine, fake_conn):
        # Arrange: game rising from rank 100 → 50 over 7 days (big improvement)
        today = date.today()
        rows = [
            (100, today - timedelta(days=7)),
            (90, today - timedelta(days=6)),
            (80, today - timedelta(days=5)),
            (70, today - timedelta(days=4)),
            (60, today - timedelta(days=2)),
            (55, today - timedelta(days=1)),
            (50, today),
        ]
        fake_conn.queue_result(rows)

        # Act
        score = engine._calc_ranking_velocity(1, fake_conn)

        # Assert: rising game should score > 0, and with >50 improvement gets
        # the +20 bonus baked in.
        assert score > 0
        assert score <= 100

    def test_insufficient_data_returns_zero(self, engine, fake_conn):
        # Arrange: only 1 data point < min_data_points (3)
        fake_conn.queue_result([(50, date.today())])

        # Act
        score = engine._calc_ranking_velocity(1, fake_conn)

        # Assert
        assert score == 0

    def test_flat_trend_returns_low_score(self, engine, fake_conn):
        # Arrange: flat rank over many days
        today = date.today()
        rows = [
            (50, today - timedelta(days=7)),
            (50, today - timedelta(days=5)),
            (50, today - timedelta(days=3)),
            (50, today - timedelta(days=1)),
            (50, today),
        ]
        fake_conn.queue_result(rows)

        # Act
        score = engine._calc_ranking_velocity(1, fake_conn)

        # Assert: zero slope → zero velocity
        assert score == 0


# ---------------------------------------------------------------------------
# _calc_genre_fit
# ---------------------------------------------------------------------------
class TestGenreFit:
    def test_known_high_iaa_genre_idle(self, engine, fake_conn):
        # Arrange: shared/genres.json has "idle" at iaa_score 95
        fake_conn.queue_result([("idle", [])])

        # Act
        score = engine._calc_genre_fit(1, fake_conn)

        # Assert
        assert score == 95

    def test_unknown_genre_defaults_to_50(self, engine, fake_conn):
        # Arrange
        fake_conn.queue_result([("totally_unknown_genre_xyz", [])])

        # Act
        score = engine._calc_genre_fit(1, fake_conn)

        # Assert
        assert score == 50

    def test_gameplay_tag_match(self, engine, fake_conn):
        # Arrange: genre is unknown but a tag matches "merge" (iaa_score=92)
        fake_conn.queue_result([("mystery", ["merge", "cute"])])

        # Act
        score = engine._calc_genre_fit(1, fake_conn)

        # Assert
        assert score == 92

    def test_missing_row_returns_default(self, engine, fake_conn):
        # Arrange: no row
        fake_conn.queue_result([])

        # Act
        score = engine._calc_genre_fit(1, fake_conn)

        # Assert
        assert score == 50


# ---------------------------------------------------------------------------
# _calc_social_buzz
# ---------------------------------------------------------------------------
class TestSocialBuzz:
    def test_mixed_platforms_returns_score(self, engine, fake_conn):
        # Arrange: douyin (weight 1.0) + bilibili (weight 0.8)
        # douyin 500k views + 20 videos → view_score=50, video_bonus=30 → 80
        # bilibili 200k views + 10 videos → view_score=20, video_bonus=20 → 40
        rows = [
            ("douyin", 20, 500_000, 5_000),
            ("bilibili", 10, 200_000, 2_000),
        ]
        fake_conn.queue_result(rows)

        # Act
        score = engine._calc_social_buzz(1, fake_conn)

        # Assert: both platforms contribute, score is in [0,100]
        assert 0 < score <= 100

    def test_empty_returns_zero(self, engine, fake_conn):
        # Arrange: no rows
        fake_conn.queue_result([])

        # Act
        score = engine._calc_social_buzz(1, fake_conn)

        # Assert
        assert score == 0


# ---------------------------------------------------------------------------
# _calc_cross_platform
# ---------------------------------------------------------------------------
class TestCrossPlatform:
    def test_three_platforms_beats_one(self, engine, fake_conn):
        # Arrange/Act: three platform listings
        fake_conn.queue_result([("google_play",), ("app_store",), ("steam",)])
        three_score = engine._calc_cross_platform(1, fake_conn)

        fake_conn.queue_result([("google_play",)])
        one_score = engine._calc_cross_platform(1, fake_conn)

        # Assert
        assert three_score > one_score
        assert three_score == 70  # 25 + 25 + 20
        assert one_score == 25

    def test_no_listings_returns_zero(self, engine, fake_conn):
        # Arrange
        fake_conn.queue_result([])

        # Act
        score = engine._calc_cross_platform(1, fake_conn)

        # Assert
        assert score == 0


# ---------------------------------------------------------------------------
# _calc_rating_quality
# ---------------------------------------------------------------------------
class TestRatingQuality:
    def test_high_rating_many_reviews_scores_high(self, engine, fake_conn):
        # Arrange: 4.7/5 with 150k reviews
        fake_conn.queue_result([(4.7, 150_000)])

        # Act
        score = engine._calc_rating_quality(1, fake_conn)

        # Assert: rating_norm = 4.7/5*60 = 56.4, count_bonus = 40 → 96
        assert score >= 90

    def test_low_rating_few_reviews_scores_low(self, engine, fake_conn):
        # Arrange
        fake_conn.queue_result([(2.5, 50)])

        # Act
        score = engine._calc_rating_quality(1, fake_conn)

        # Assert
        assert score < 40

    def test_no_rating_returns_zero(self, engine, fake_conn):
        # Arrange
        fake_conn.queue_result([])

        # Act
        score = engine._calc_rating_quality(1, fake_conn)

        # Assert
        assert score == 0


# ---------------------------------------------------------------------------
# _calc_competition_gap
# ---------------------------------------------------------------------------
class TestCompetitionGap:
    def test_zero_competitors_scores_max(self, engine, fake_conn):
        # Arrange: one SELECT for (genre, tags), one SELECT for COUNT
        fake_conn.queue_result([("idle", [])])
        fake_conn.queue_result([(0,)])

        # Act
        score = engine._calc_competition_gap(1, fake_conn)

        # Assert
        assert score == 100

    def test_five_competitors_scores_medium(self, engine, fake_conn):
        # Arrange: 5 falls into the <=10 bucket → 60
        fake_conn.queue_result([("idle", [])])
        fake_conn.queue_result([(5,)])

        # Act
        score = engine._calc_competition_gap(1, fake_conn)

        # Assert
        assert score == 60

    def test_twenty_five_competitors_scores_low(self, engine, fake_conn):
        # Arrange: 25 > 20 bucket → 20
        fake_conn.queue_result([("idle", [])])
        fake_conn.queue_result([(25,)])

        # Act
        score = engine._calc_competition_gap(1, fake_conn)

        # Assert
        assert score == 20

    def test_no_genre_returns_default(self, engine, fake_conn):
        # Arrange
        fake_conn.queue_result([(None, None)])

        # Act
        score = engine._calc_competition_gap(1, fake_conn)

        # Assert
        assert score == 50


# ---------------------------------------------------------------------------
# _calc_ad_activity
# ---------------------------------------------------------------------------
class TestAdActivity:
    @pytest.mark.parametrize(
        "creatives,expected",
        [
            (150, 100),
            (60, 80),
            (25, 60),
            (10, 40),
            (2, 20),
        ],
    )
    def test_thresholds(self, engine, fake_conn, creatives, expected):
        # Arrange
        fake_conn.queue_result([(creatives,)])

        # Act
        score = engine._calc_ad_activity(1, fake_conn)

        # Assert
        assert score == expected

    def test_no_ads_returns_zero(self, engine, fake_conn):
        # Arrange: row exists but sum is None
        fake_conn.queue_result([(None,)])

        # Act
        score = engine._calc_ad_activity(1, fake_conn)

        # Assert
        assert score == 0


# ---------------------------------------------------------------------------
# score_game end-to-end
# ---------------------------------------------------------------------------
class TestScoreGameEndToEnd:
    def test_weighted_overall_in_range(self, engine, fake_conn):
        """End-to-end: queue rows for all 7 sub-metrics in the exact order
        score_game() calls its helpers and verify the final overall is
        clamped into [0, 100]."""
        today = date.today()

        # 1. ranking_velocity: queue a rising series
        fake_conn.queue_result(
            [
                (200, today - timedelta(days=7)),
                (150, today - timedelta(days=5)),
                (100, today - timedelta(days=3)),
                (60, today - timedelta(days=1)),
                (40, today),
            ]
        )
        # 2. genre_fit: (genre, tags)
        fake_conn.queue_result([("idle", [])])
        # 3. social_buzz: rows grouped by platform
        fake_conn.queue_result(
            [
                ("douyin", 50, 1_000_000, 20_000),
                ("bilibili", 30, 400_000, 5_000),
            ]
        )
        # 4. cross_platform: platform listings
        fake_conn.queue_result([("google_play",), ("app_store",), ("steam",)])
        # 5. rating_quality: (rating, count)
        fake_conn.queue_result([(4.6, 50_000)])
        # 6. competition_gap: (genre, tags) then COUNT
        fake_conn.queue_result([("idle", [])])
        fake_conn.queue_result([(2,)])
        # 7. ad_activity: SUM row
        fake_conn.queue_result([(80,)])

        # Act
        result = engine.score_game(game_id=42, conn=fake_conn)

        # Assert
        assert result.game_id == 42
        assert 0 <= result.overall_score <= 100
        # Idle + rising + multi-platform should produce a healthy score
        assert result.overall_score > 50
        assert result.genre_fit == 95
        assert result.algorithm_version == "v1"
