"""Unit tests for :mod:`src.processors.alerting`.

Focus: each detector's row-shape → AlertCandidate contract, plus the
cooldown gate. ``FakeConnection`` feeds canned rows matching what each
detector's SELECT would yield in production.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.processors.alerting import (
    AlertCandidate,
    AlertEngine,
    AlertType,
    Severity,
)


@pytest.fixture
def engine():
    return AlertEngine(db_url="fake")


# ---------------------------------------------------------------------------
# detect_ranking_jump
# ---------------------------------------------------------------------------
class TestRankingJump:
    def test_single_qualifying_row_produces_candidate(self, engine, fake_conn):
        # Arrange: one game jumped 30 places on app_store/top_free/US
        fake_conn.queue_result(
            [
                (
                    1,               # game_id
                    "My Game",       # name
                    "app_store",     # platform
                    "top_free",      # chart_type
                    "US",            # region
                    5,               # rank_position
                    35,              # previous_rank
                    30,              # rank_change
                )
            ]
        )
        # _lookup_score is called once per candidate → queue a score row
        fake_conn.queue_result([(72,)])

        # Act
        candidates = engine.detect_ranking_jump(fake_conn)

        # Assert
        assert len(candidates) == 1
        c = candidates[0]
        assert c.game_id == 1
        assert c.alert_type == AlertType.RANKING_JUMP
        # 30 rank_change → P2 by severity matrix
        assert c.severity == Severity.P2
        assert c.score == 72

    def test_no_rows_returns_empty(self, engine, fake_conn):
        # Arrange
        fake_conn.queue_result([])

        # Act
        candidates = engine.detect_ranking_jump(fake_conn)

        # Assert
        assert candidates == []


# ---------------------------------------------------------------------------
# detect_social_burst
# ---------------------------------------------------------------------------
class TestSocialBurst:
    def test_views_10x_baseline_triggers(self, engine, fake_conn):
        # Arrange: 2M views today vs 100K baseline → ratio 20x, P1
        fake_conn.queue_result(
            [(5, "Hot Game", "douyin", 2_000_000, 100_000)]
        )
        fake_conn.queue_result([(88,)])  # _lookup_score

        # Act
        candidates = engine.detect_social_burst(fake_conn)

        # Assert
        assert len(candidates) == 1
        c = candidates[0]
        assert c.alert_type == AlertType.SOCIAL_BURST
        # ratio >= 10 OR views >= 1M → P1
        assert c.severity == Severity.P1


# ---------------------------------------------------------------------------
# detect_steam_momentum
# ---------------------------------------------------------------------------
class TestSteamMomentum:
    def test_high_velocity_triggers_p1(self, engine, fake_conn):
        # Arrange: velocity 85, overall 78, rank_delta 12, best_rank 20
        fake_conn.queue_result(
            [(3, "Steam Hit", 85, 78, 12, 20)]
        )

        # Act
        candidates = engine.detect_steam_momentum(fake_conn)

        # Assert
        assert len(candidates) == 1
        c = candidates[0]
        assert c.alert_type == AlertType.STEAM_MOMENTUM
        assert c.severity == Severity.P1
        assert c.score == 78

    def test_p2_for_velocity_in_60s(self, engine, fake_conn):
        # Arrange
        fake_conn.queue_result([(4, "Mid Game", 65, 55, 0, 40)])

        # Act
        candidates = engine.detect_steam_momentum(fake_conn)

        # Assert
        assert candidates[0].severity == Severity.P2


# ---------------------------------------------------------------------------
# detect_taptap_heat
# ---------------------------------------------------------------------------
class TestTaptapHeat:
    def test_chart_rise_produces_candidate(self, engine, fake_conn):
        # Arrange: chart rows returned with rank_change=30 → P2
        fake_conn.queue_result(
            [
                (9, "Hot Taptap", "hot", 12, 30),
            ]
        )
        fake_conn.queue_result([(65,)])  # _lookup_score for chart candidate
        # rating-count surge query returns nothing
        fake_conn.queue_result([])

        # Act
        candidates = engine.detect_taptap_heat(fake_conn)

        # Assert
        assert len(candidates) == 1
        c = candidates[0]
        assert c.alert_type == AlertType.TAPTAP_HEAT
        # rank_change 30 >= 25 → P2
        assert c.severity == Severity.P2


# ---------------------------------------------------------------------------
# detect_review_burst
# ---------------------------------------------------------------------------
class TestReviewBurst:
    def test_high_volume_triggers_p1(self, engine, fake_conn):
        # Arrange: 250 reviews today vs 5/day baseline → 50x → P1
        fake_conn.queue_result(
            [(8, "Burst Game", 250, 5.0)]
        )
        fake_conn.queue_result([(90,)])  # _lookup_score

        # Act
        candidates = engine.detect_review_burst(fake_conn)

        # Assert
        assert len(candidates) == 1
        c = candidates[0]
        assert c.alert_type == AlertType.REVIEW_BURST
        assert c.severity == Severity.P1


# ---------------------------------------------------------------------------
# _is_in_cooldown
# ---------------------------------------------------------------------------
class TestCooldown:
    def test_recent_event_is_in_cooldown(self, engine, fake_conn):
        # Arrange: a row means "recent event exists"
        fake_conn.queue_result([(1,)])

        # Act
        in_cooldown = engine._is_in_cooldown(
            fake_conn, alert_id=1, game_id=42, cooldown_hours=12
        )

        # Assert
        assert in_cooldown is True

    def test_no_recent_event_not_in_cooldown(self, engine, fake_conn):
        # Arrange
        fake_conn.queue_result([])

        # Act
        in_cooldown = engine._is_in_cooldown(
            fake_conn, alert_id=1, game_id=42, cooldown_hours=12
        )

        # Assert
        assert in_cooldown is False


# ---------------------------------------------------------------------------
# _dedupe — keeps highest severity per game
# ---------------------------------------------------------------------------
def test_dedupe_keeps_highest_severity():
    # Arrange
    cands = [
        AlertCandidate(
            game_id=1, game_name="A",
            alert_type=AlertType.RANKING_JUMP, severity=Severity.P3,
            reason="low",
        ),
        AlertCandidate(
            game_id=1, game_name="A",
            alert_type=AlertType.SOCIAL_BURST, severity=Severity.P1,
            reason="high",
        ),
        AlertCandidate(
            game_id=2, game_name="B",
            alert_type=AlertType.RANKING_JUMP, severity=Severity.P2,
            reason="mid",
        ),
    ]

    # Act
    deduped = AlertEngine._dedupe(cands)

    # Assert
    deduped_by_game = {c.game_id: c for c in deduped}
    assert len(deduped) == 2
    assert deduped_by_game[1].severity == Severity.P1
    assert deduped_by_game[2].severity == Severity.P2
