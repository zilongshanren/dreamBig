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
        score, active = engine._calc_ranking_velocity(1, fake_conn)

        # Assert: rising game should score > 0, active=True.
        assert score > 0
        assert score <= 100
        assert active is True

    def test_insufficient_data_returns_inactive(self, engine, fake_conn):
        # Arrange: only 1 data point < min_data_points (3) → inactive
        fake_conn.queue_result([(50, date.today())])

        # Act
        score, active = engine._calc_ranking_velocity(1, fake_conn)

        # Assert: inactive means score is dropped from normalization.
        assert score == 0
        assert active is False

    def test_flat_trend_returns_zero_inactive(self, engine, fake_conn):
        # Arrange: flat rank over many days; slope zero → denom-of-slope zero
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
        score, active = engine._calc_ranking_velocity(1, fake_conn)

        # Assert: zero slope → velocity 0, but active=True (data exists).
        # Actually: regression math produces slope=0 which gives score=0
        # and velocity_score stays 0. The early-return from denom==0 is the
        # "inactive" signal; a computed-zero slope is still "active".
        assert score == 0
        assert active is True


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
        rows = [
            ("douyin", 20, 500_000, 5_000),
            ("bilibili", 10, 200_000, 2_000),
        ]
        fake_conn.queue_result(rows)

        # Act
        score, active = engine._calc_social_buzz(1, fake_conn)

        # Assert: both platforms contribute, score in [0,100], active.
        assert 0 < score <= 100
        assert active is True

    def test_empty_returns_inactive(self, engine, fake_conn):
        # Arrange: no rows → dimension inactive (excluded from denominator)
        fake_conn.queue_result([])

        # Act
        score, active = engine._calc_social_buzz(1, fake_conn)

        # Assert
        assert score == 0
        assert active is False


# ---------------------------------------------------------------------------
# _calc_cross_platform
# ---------------------------------------------------------------------------
class TestCrossPlatform:
    def test_three_platforms_beats_one(self, engine, fake_conn):
        fake_conn.queue_result([("google_play",), ("app_store",), ("steam",)])
        three_score, three_active = engine._calc_cross_platform(1, fake_conn)

        fake_conn.queue_result([("google_play",)])
        one_score, one_active = engine._calc_cross_platform(1, fake_conn)

        assert three_score > one_score
        assert three_score == 70  # 25 + 25 + 20
        assert one_score == 25
        assert three_active is True
        assert one_active is True

    def test_no_listings_returns_inactive(self, engine, fake_conn):
        fake_conn.queue_result([])

        score, active = engine._calc_cross_platform(1, fake_conn)

        # No listings → dimension excluded from normalization.
        assert score == 0
        assert active is False


# ---------------------------------------------------------------------------
# _calc_rating_quality
# ---------------------------------------------------------------------------
class TestRatingQuality:
    def test_high_rating_many_reviews_scores_high(self, engine, fake_conn):
        fake_conn.queue_result([(4.7, 150_000)])

        score, active = engine._calc_rating_quality(1, fake_conn)

        assert score >= 90
        assert active is True

    def test_low_rating_few_reviews_scores_low(self, engine, fake_conn):
        fake_conn.queue_result([(2.5, 50)])

        score, active = engine._calc_rating_quality(1, fake_conn)

        assert score < 40
        assert active is True

    def test_no_rating_returns_inactive(self, engine, fake_conn):
        fake_conn.queue_result([])

        score, active = engine._calc_rating_quality(1, fake_conn)

        # No rating data (e.g. WeChat Mini game) → excluded from normalization.
        assert score == 0
        assert active is False


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
        fake_conn.queue_result([(creatives,)])

        score, active = engine._calc_ad_activity(1, fake_conn)

        assert score == expected
        assert active is True

    def test_no_ads_returns_inactive(self, engine, fake_conn):
        # Row exists but sum is None → no measurements yet, dimension skipped.
        fake_conn.queue_result([(None,)])

        score, active = engine._calc_ad_activity(1, fake_conn)

        assert score == 0
        assert active is False


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
        assert result.algorithm_version == "v2"

    def test_wechat_only_game_can_exceed_high_potential(self, engine, fake_conn):
        """Regression: v1 scoring gave WeChat-only games a hard ceiling
        around 57 because rating_quality=0 / social_buzz=0 / ad_activity=0
        were averaged into the denominator. v2 dynamic normalization drops
        absent dimensions, so a strong WeChat game can cross the 60-point
        high-potential threshold on its own merits."""
        today = date.today()

        # 1. ranking_velocity: strong rising series (big improvement bonus)
        fake_conn.queue_result(
            [
                (200, today - timedelta(days=7)),
                (120, today - timedelta(days=5)),
                (60, today - timedelta(days=3)),
                (20, today - timedelta(days=1)),
                (10, today),
            ]
        )
        # 2. genre_fit: idle (iaa_score=95)
        fake_conn.queue_result([("idle", [])])
        # 3. social_buzz: no rows (empty B站/Douyin data)
        fake_conn.queue_result([])
        # 4. cross_platform: wechat_mini only
        fake_conn.queue_result([("wechat_mini",)])
        # 5. rating_quality: no rows (WeChat has no star ratings)
        fake_conn.queue_result([])
        # 6. competition_gap: (genre, tags) then COUNT
        fake_conn.queue_result([("idle", [])])
        fake_conn.queue_result([(0,)])
        # 7. ad_activity: no ad data
        fake_conn.queue_result([(None,)])

        # Act
        result = engine.score_game(game_id=99, conn=fake_conn)

        # Assert: v1 would have capped this around 57. With dynamic
        # normalization a top-tier idle WeChat game clears 60.
        assert result.social_buzz == 0
        assert result.rating_quality == 0
        assert result.ad_activity == 0
        assert result.genre_fit == 95
        assert result.overall_score >= 60, (
            f"WeChat-only idle game with strong velocity should score "
            f">= 60, got {result.overall_score}"
        )
