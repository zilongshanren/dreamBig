"""Unit tests for :mod:`src.processors.dedup`.

Covers the three-tier matching flow in ``find_or_create_game`` plus the
``process_ranking_entries`` loop that stitches platform listings and
ranking snapshots into the DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from src.processors.dedup import DeduplicationEngine, normalize_name


@pytest.fixture
def engine():
    return DeduplicationEngine(db_url="fake")


# ---------------------------------------------------------------------------
# normalize_name — pure function, no DB
# ---------------------------------------------------------------------------
def test_normalize_name_strips_suffix_and_case():
    # Arrange / Act
    result = normalize_name("Royal Match: Premium")

    # Assert
    assert "premium" not in result
    assert "royalmatch" in result


# ---------------------------------------------------------------------------
# find_or_create_game — three-tier matching
# ---------------------------------------------------------------------------
class TestFindOrCreateGame:
    def test_exact_match_returns_existing_id(self, engine, fake_conn):
        # Arrange: tier 1 hit (developer + normalized name)
        fake_conn.queue_result([(7,)])  # found in the first SELECT

        # Act
        game_id = engine.find_or_create_game(
            fake_conn,
            name="Royal Match",
            developer="Dream Games",
        )

        # Assert
        assert game_id == 7
        assert len(fake_conn.executed) == 1  # only one query needed

    def test_tier_1b_name_only_match(self, engine, fake_conn):
        # Arrange: no developer match (queued empty), but tier 1b hits
        fake_conn.queue_result([])       # tier 1 (developer+name) miss
        fake_conn.queue_result([(11,)])  # tier 1b (name-only) hit

        # Act
        game_id = engine.find_or_create_game(
            fake_conn,
            name="Block Blast",
            developer="Hungry Studio",
        )

        # Assert
        assert game_id == 11

    def test_fuzzy_match_returns_id(self, engine, fake_conn):
        # Arrange: miss tier 1 + tier 1b, hit tier 2 (pg_trgm)
        fake_conn.queue_result([])        # tier 1
        fake_conn.queue_result([])        # tier 1b
        fake_conn.queue_result([(21, 0.9)])  # tier 2 similarity row

        # Act
        game_id = engine.find_or_create_game(
            fake_conn,
            name="Merge Mansion",
            developer="Metacore",
        )

        # Assert
        assert game_id == 21

    def test_new_game_insertion_path(self, engine, fake_conn):
        # Arrange: all tiers miss, INSERT ... RETURNING id produces 99
        fake_conn.queue_result([])        # tier 1
        fake_conn.queue_result([])        # tier 1b
        fake_conn.queue_result([])        # tier 2 fuzzy
        fake_conn.queue_result([(99,)])   # INSERT RETURNING

        # Act
        game_id = engine.find_or_create_game(
            fake_conn,
            name="Brand New Title",
            developer="Indie Dev",
            genre="puzzle",
        )

        # Assert
        assert game_id == 99
        # Verify an INSERT was executed
        assert any("INSERT INTO games" in sql for sql, _ in fake_conn.executed)

    def test_no_developer_skips_tier_1(self, engine, fake_conn):
        # Arrange: tier 1b matches first query
        fake_conn.queue_result([(5,)])

        # Act
        game_id = engine.find_or_create_game(fake_conn, name="Some Game")

        # Assert
        assert game_id == 5


# ---------------------------------------------------------------------------
# process_ranking_entries
# ---------------------------------------------------------------------------
@dataclass
class FakeEntry:
    name: str
    developer: str | None
    genre: str | None
    platform_id: str
    rank_position: int
    chart_type: str = "top_free"
    region: str = "US"
    rating: float | None = None
    rating_count: int | None = None
    download_est: int | None = None
    url: str | None = None
    metadata: dict = field(default_factory=dict)
    icon_url: str | None = None


class TestProcessRankingEntries:
    def test_mixed_new_and_existing_entries(self, engine, fake_conn):
        # Arrange: two entries — the first is an existing game (tier 1 hit),
        # the second is brand new (misses all tiers → INSERT).
        # NOTE: entries are sorted by platform_id before processing, so
        # "com.ex.game" still comes before "com.fresh.indie" alphabetically.
        entries = [
            FakeEntry(
                name="Existing Game",
                developer="Big Studio",
                genre="idle",
                platform_id="com.ex.game",
                rank_position=3,
            ),
            FakeEntry(
                name="Fresh Indie",
                developer="Lone Dev",
                genre="puzzle",
                platform_id="com.fresh.indie",
                rank_position=42,
            ),
        ]

        # New in v2: process_ranking_entries acquires a transaction-level
        # advisory lock before touching rows. That's a `SELECT pg_advisory_
        # xact_lock(...)` which drains one slot from the fake queue.
        fake_conn.queue_result([])          # advisory lock — no meaningful rows

        # ---------------- entry 1 (existing) ----------------
        fake_conn.queue_result([(100,)])    # find_or_create tier 1 hit → id=100
        fake_conn.queue_result([(500,)])    # link_platform_listing RETURNING id
        fake_conn.queue_result([(5,)])      # prev rank lookup → previous_rank=5
        # INSERT INTO ranking_snapshots has no RETURNING — no queue needed.

        # ---------------- entry 2 (new) ----------------
        fake_conn.queue_result([])          # tier 1 miss
        fake_conn.queue_result([])          # tier 1b miss
        fake_conn.queue_result([])          # tier 2 fuzzy miss
        fake_conn.queue_result([(200,)])    # INSERT INTO games RETURNING id=200
        fake_conn.queue_result([(600,)])    # link_platform_listing RETURNING id
        fake_conn.queue_result([])          # prev rank lookup → no previous

        # Act
        count = engine.process_ranking_entries(fake_conn, entries, platform="google_play")

        # Assert
        assert count == 2
        # With only 2 entries (< RANKING_COMMIT_BATCH=10) there is exactly
        # one final commit at the end of the loop.
        assert fake_conn.committed == 1
        # Sanity check: a ranking snapshot INSERT was issued for each entry
        snapshot_inserts = [
            sql for sql, _ in fake_conn.executed if "ranking_snapshots" in sql and "INSERT" in sql
        ]
        assert len(snapshot_inserts) == 2
        # The advisory lock was acquired at the top of the transaction.
        assert any(
            "pg_advisory_xact_lock" in sql for sql, _ in fake_conn.executed
        )
