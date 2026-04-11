"""Unit tests for :mod:`src.processors.review_analysis`.

All LLM calls are stubbed out at module level via monkeypatch so no real
Poe API calls happen. ``FakeConnection`` feeds the review rows the
processor expects.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.llm.prompts.sentiment import SentimentBatchOutput, SentimentItem
from src.llm.prompts.topic_clustering import ClusteredTopic, TopicClusteringOutput
from src.llm.prompts.topic_extraction import (
    ReviewTopicsBatchOutput,
    ReviewTopicsItem,
)
from src.processors import review_analysis as ra
from src.processors.review_analysis import ReviewNLPProcessor, _sanitize_topics


# ---------------------------------------------------------------------------
# _sanitize_topics — pure helper
# ---------------------------------------------------------------------------
class TestSanitizeTopics:
    def test_snake_cases_and_dedupes(self):
        # Arrange / Act
        out = _sanitize_topics(["Ads Intrusive", "ads_intrusive", "Progression"])

        # Assert
        assert out == ["ads_intrusive", "progression"]

    def test_caps_at_three(self):
        out = _sanitize_topics(["a", "b", "c", "d", "e"])
        assert len(out) == 3

    def test_drops_invalid_entries(self):
        out = _sanitize_topics([None, "", "  ", "valid_tag"])  # type: ignore[list-item]
        assert out == ["valid_tag"]


# ---------------------------------------------------------------------------
# Stub PoeClient
# ---------------------------------------------------------------------------
class StubPoeClient:
    """Minimal async stub that returns canned pydantic objects."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def chat_json(self, messages, model, schema, **kwargs):
        self.calls.append({"model": model, "schema": schema})
        if not self.responses:
            raise RuntimeError("StubPoeClient exhausted")
        return self.responses.pop(0)

    async def close(self):
        pass


def _patch_psycopg_connect(monkeypatch, fake_conn):
    import psycopg

    class _Ctx:
        def __enter__(self_inner):
            return fake_conn

        def __exit__(self_inner, exc_type, exc, tb):
            fake_conn.close()
            return False

    def fake_connect(*args, **kwargs):
        return _Ctx()

    monkeypatch.setattr(psycopg, "connect", fake_connect)


# ---------------------------------------------------------------------------
# classify_sentiments
# ---------------------------------------------------------------------------
class TestClassifySentiments:
    def test_parses_llm_output_and_updates_reviews(self, monkeypatch, fake_conn):
        # Arrange: _table_exists → True, then a SELECT returning 2 reviews
        fake_conn.queue_result([(True,)])  # _table_exists
        fake_conn.queue_result(
            [
                (101, "This game is amazing"),
                (102, "Terrible ads everywhere"),
            ]
        )
        _patch_psycopg_connect(monkeypatch, fake_conn)

        stub = StubPoeClient(
            [
                SentimentBatchOutput(
                    items=[
                        SentimentItem(index=0, sentiment="positive", confidence=0.95),
                        SentimentItem(index=1, sentiment="negative", confidence=0.88),
                    ]
                )
            ]
        )

        proc = ReviewNLPProcessor(db_url="fake", poe_client=stub)  # type: ignore[arg-type]

        # Act
        count = asyncio.run(proc.classify_sentiments(limit=50))

        # Assert
        assert count == 2
        assert len(stub.calls) == 1
        # Two UPDATE reviews were issued
        updates = [sql for sql, _ in fake_conn.executed if "UPDATE reviews" in sql]
        assert len(updates) == 2


# ---------------------------------------------------------------------------
# extract_topics
# ---------------------------------------------------------------------------
class TestExtractTopics:
    def test_parses_topics_and_updates(self, monkeypatch, fake_conn):
        # Arrange
        fake_conn.queue_result([(True,)])  # _table_exists
        fake_conn.queue_result(
            [
                (201, "The ads are constant", "negative"),
                (202, "Great graphics and music", "positive"),
            ]
        )
        _patch_psycopg_connect(monkeypatch, fake_conn)

        stub = StubPoeClient(
            [
                ReviewTopicsBatchOutput(
                    items=[
                        ReviewTopicsItem(index=0, topics=["ads_intrusive"]),
                        ReviewTopicsItem(index=1, topics=["art_style", "music"]),
                    ]
                )
            ]
        )

        proc = ReviewNLPProcessor(db_url="fake", poe_client=stub)  # type: ignore[arg-type]

        # Act
        count = asyncio.run(proc.extract_topics(limit=50))

        # Assert
        assert count == 2
        updates = [sql for sql, _ in fake_conn.executed if "UPDATE reviews" in sql]
        assert len(updates) == 2


# ---------------------------------------------------------------------------
# cluster_game_topics — end-to-end aggregation
# ---------------------------------------------------------------------------
class TestClusterGameTopics:
    def test_aggregates_and_writes_summaries(self, monkeypatch, fake_conn):
        # Arrange: two _table_exists calls (reviews, review_topic_summaries)
        fake_conn.queue_result([(True,)])
        fake_conn.queue_result([(True,)])

        # Labeled reviews SELECT — 3 negative ads_intrusive reviews clear the
        # MIN_REVIEWS_PER_GROUP=3 threshold.
        fake_conn.queue_result(
            [
                (301, "Too many ads", "negative", ["ads_intrusive"]),
                (302, "Ads are constant", "negative", ["ads_intrusive"]),
                (303, "Ads ruin it", "negative", ["ads_intrusive"]),
            ]
        )
        # Game name lookup
        fake_conn.queue_result([("Test Game",)])
        _patch_psycopg_connect(monkeypatch, fake_conn)

        stub = StubPoeClient(
            [
                TopicClusteringOutput(
                    clusters=[
                        ClusteredTopic(
                            topic="ads_intrusive",
                            sentiment="negative",
                            snippet="玩家普遍抱怨广告过多。",
                            confidence=0.9,
                        )
                    ]
                )
            ]
        )

        proc = ReviewNLPProcessor(db_url="fake", poe_client=stub)  # type: ignore[arg-type]

        # Act
        written = asyncio.run(proc.cluster_game_topics(game_id=7))

        # Assert
        assert written == 1
        # Verify INSERT into review_topic_summaries was issued
        inserts = [
            sql
            for sql, _ in fake_conn.executed
            if "review_topic_summaries" in sql and "INSERT" in sql
        ]
        assert len(inserts) == 1
