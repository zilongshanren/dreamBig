"""Unit tests for :mod:`src.processors.embedding`.

The embedding client is stubbed to return deterministic vectors. Psycopg
is monkeypatched via ``FakeConnection``.
"""

from __future__ import annotations

import asyncio

import pytest

from src.llm.embedding_client import DIM
from src.processors.embedding import GameEmbeddingGenerator, run_embedding_refresh


class StubEmbeddingClient:
    """Returns deterministic fake vectors."""

    def __init__(self):
        self.embed_calls = 0
        self.batch_calls = 0

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        return [0.1] * DIM

    async def embed_batch(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        self.batch_calls += 1
        return [[0.2] * DIM for _ in texts]

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

    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: _Ctx())


# ---------------------------------------------------------------------------
# _build_text — pure helper
# ---------------------------------------------------------------------------
class TestBuildText:
    def test_includes_all_sections(self):
        # Arrange
        gen = GameEmbeddingGenerator(db_url="fake", client=StubEmbeddingClient())  # type: ignore[arg-type]

        # Act
        text = gen._build_text(
            name="Cool Game",
            genre="idle",
            tags=["incremental", "clicker"],
            positioning="a chill tap tap",
            core_loop="tap to earn",
            topics=["ads_intrusive: too many ads"],
        )

        # Assert
        assert "Cool Game" in text
        assert "idle" in text
        assert "incremental" in text
        assert "tap to earn" in text

    def test_handles_missing_sections(self):
        # Arrange
        gen = GameEmbeddingGenerator(db_url="fake", client=StubEmbeddingClient())  # type: ignore[arg-type]

        # Act
        text = gen._build_text(
            name="Minimal",
            genre=None,
            tags=None,
            positioning=None,
            core_loop=None,
            topics=None,
        )

        # Assert
        assert "Minimal" in text


# ---------------------------------------------------------------------------
# refresh_stale — end-to-end bulk path
# ---------------------------------------------------------------------------
class TestRefreshStale:
    def test_writes_embeddings_for_stale_games(self, monkeypatch, fake_conn):
        # Arrange: SELECT stale games returns 2 rows
        fake_conn.queue_result(
            [
                (1, "Game One", "idle", ["incremental"], None, None),
                (2, "Game Two", "merge", None, None, None),
            ]
        )
        # _load_top_topics for each game → empty topics (SELECT)
        fake_conn.queue_result([])
        fake_conn.queue_result([])
        # The two INSERTs into game_embeddings don't need any queued result;
        # FakeConnection returns an empty cursor for unqueued calls.
        _patch_psycopg_connect(monkeypatch, fake_conn)

        stub = StubEmbeddingClient()
        gen = GameEmbeddingGenerator(db_url="fake", client=stub)  # type: ignore[arg-type]

        # Act
        written = asyncio.run(gen.refresh_stale(limit=50))

        # Assert
        assert written == 2
        assert stub.batch_calls == 1
        # Pgvector inserts use ::vector cast
        inserts = [
            sql
            for sql, _ in fake_conn.executed
            if "game_embeddings" in sql and "INSERT" in sql
        ]
        assert len(inserts) == 2
        assert any("::vector" in sql for sql in inserts)

    def test_no_stale_returns_zero(self, monkeypatch, fake_conn):
        # Arrange: empty SELECT
        fake_conn.queue_result([])
        _patch_psycopg_connect(monkeypatch, fake_conn)

        gen = GameEmbeddingGenerator(
            db_url="fake", client=StubEmbeddingClient()  # type: ignore[arg-type]
        )

        # Act
        written = asyncio.run(gen.refresh_stale(limit=50))

        # Assert
        assert written == 0


# ---------------------------------------------------------------------------
# run_embedding_refresh — sync entry wrapper
# ---------------------------------------------------------------------------
def test_run_embedding_refresh_uses_client(monkeypatch, fake_conn):
    # Arrange: SELECT returns 1 stale game, then _load_top_topics empty
    fake_conn.queue_result([(1, "Solo", "idle", None, None, None)])
    fake_conn.queue_result([])
    _patch_psycopg_connect(monkeypatch, fake_conn)

    # Patch the class's embed client attribute upon instantiation by
    # monkeypatching EmbeddingClient constructor.
    from src.processors import embedding as emb

    stub = StubEmbeddingClient()
    monkeypatch.setattr(emb, "EmbeddingClient", lambda: stub)

    # Act
    written = run_embedding_refresh(db_url="fake", limit=10)

    # Assert
    assert written == 1
    assert stub.batch_calls == 1
