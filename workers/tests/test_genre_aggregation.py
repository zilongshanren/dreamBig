"""Unit tests for :mod:`src.processors.genre_aggregation`."""

from __future__ import annotations

import pytest

from src.processors.genre_aggregation import GenreAggregator, _genre_matches


# ---------------------------------------------------------------------------
# _genre_matches — pure helper
# ---------------------------------------------------------------------------
class TestGenreMatches:
    def test_direct_genre_match(self):
        assert _genre_matches("idle", None, "idle") is True

    def test_word_boundary_in_compound_genre(self):
        # "casual idle" contains idle at a word boundary
        assert _genre_matches("casual idle", None, "idle") is True

    def test_tag_match(self):
        assert _genre_matches("rpg", ["merge", "fantasy"], "merge") is True

    def test_no_match(self):
        assert _genre_matches("shooter", ["action"], "idle") is False


# ---------------------------------------------------------------------------
# GenreAggregator.refresh() via psycopg.connect monkeypatch
# ---------------------------------------------------------------------------
def test_refresh_with_no_scores_noops(monkeypatch, fake_conn):
    # Arrange: psycopg.connect returns fake_conn; first SELECT (today's scores)
    # yields nothing → refresh should bail early with 0.
    from tests.conftest import make_psycopg_connect_patch

    make_psycopg_connect_patch(monkeypatch, fake_conn)
    fake_conn.queue_result([])  # today's scores empty

    aggregator = GenreAggregator(db_url="fake")

    # Act
    written = aggregator.refresh()

    # Assert
    assert written == 0


def test_refresh_writes_rows_for_each_genre(monkeypatch, fake_conn):
    # Arrange
    from tests.conftest import make_psycopg_connect_patch

    make_psycopg_connect_patch(monkeypatch, fake_conn)

    # First SELECT: today's rows — one idle game above threshold
    fake_conn.queue_result(
        [
            (10, "idle", ["incremental"], 82),   # hot
            (11, "merge", None, 40),             # not hot
            (12, "rpg", None, 30),               # not hot
        ]
    )
    # Second SELECT: prior scores — yields a baseline for game 10
    fake_conn.queue_result([(10, 60)])

    aggregator = GenreAggregator(db_url="fake")
    num_genre_keys = len(aggregator.genres)

    # Act
    written = aggregator.refresh()

    # Assert
    assert written == num_genre_keys
    # idle UPSERT should have happened
    idle_writes = [
        params
        for sql, params in fake_conn.executed
        if "INSERT INTO genres" in sql and params and params[0] == "idle"
    ]
    assert len(idle_writes) == 1
    assert fake_conn.committed == 1
