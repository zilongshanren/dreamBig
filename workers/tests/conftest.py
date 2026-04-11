"""Shared test fixtures: a fake psycopg.Connection for processors.

Processors under test take a ``psycopg.Connection`` and call ``conn.execute()``
chained with ``.fetchone()`` / ``.fetchall()``. These fakes:

- Record every SQL call (text + params) for assertion.
- Return canned results for each SELECT in FIFO order via ``queue_result()``.
- Behave as a context manager and a cursor — mirroring the subset of psycopg's
  API these processors actually use.
- Avoid any real network / database access.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import pytest

# Ensure `src.*` imports work from tests without requiring an installed package.
WORKERS_ROOT = Path(__file__).resolve().parent.parent
if str(WORKERS_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKERS_ROOT))


class FakeCursor:
    """Mimics a psycopg cursor for fetchone/fetchall/iteration."""

    def __init__(self, rows: list[tuple] | None):
        # rows == None signals "not a query that returns rows" (e.g. INSERT).
        self._rows = list(rows) if rows is not None else []
        self._consumed = False

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __getitem__(self, idx):
        # Some code does `conn.execute(...).fetchone()[0]`; make sure we still
        # support that shape via the tuple returned from fetchone() naturally.
        return self._rows[idx]


class FakeConnection:
    """Drop-in fake for ``psycopg.Connection``.

    Usage::

        conn = FakeConnection()
        conn.queue_result([(1, "alpha")])          # pop for next SELECT
        conn.queue_result([(42,)])                 # pop for the one after that
        engine.score_game(1, conn)

        assert any("SELECT" in sql for sql, _ in conn.executed)
    """

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self._results: deque[list[tuple]] = deque()
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    # -- Queueing API ------------------------------------------------------
    def queue_result(self, rows: list[tuple]) -> None:
        """Push one canned result onto the queue.

        The next ``execute(...)`` call whose SQL looks like a SELECT (or any
        call at all if more results are queued) will drain one entry here.
        """
        self._results.append(list(rows))

    # -- psycopg.Connection surface ---------------------------------------
    def execute(self, sql, params=None):
        self.executed.append((str(sql), tuple(params) if params is not None else ()))
        # Heuristic: return queued rows for anything that LOOKS like it needs
        # rows (SELECT / WITH / RETURNING). Pure INSERT/UPDATE/DELETE with no
        # RETURNING clause get an empty cursor and don't drain the queue.
        sql_upper = str(sql).lstrip().upper()
        needs_rows = (
            sql_upper.startswith("SELECT")
            or sql_upper.startswith("WITH")
            or "RETURNING" in sql_upper
        )
        if needs_rows and self._results:
            return FakeCursor(self._results.popleft())
        return FakeCursor([])

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


@pytest.fixture
def fake_conn():
    """Fresh FakeConnection for each test."""
    return FakeConnection()


def make_psycopg_connect_patch(monkeypatch, fake_conn: FakeConnection):
    """Patch ``psycopg.connect`` so that it returns our ``fake_conn``.

    Usage::

        make_psycopg_connect_patch(monkeypatch, fake_conn)
        engine = ScoringEngine(db_url="fake")
        engine.score_all_games()

    The returned connection is a context manager, matching how processors
    normally use psycopg.
    """
    import psycopg

    class _ConnectCtx:
        def __enter__(self_inner):
            return fake_conn

        def __exit__(self_inner, exc_type, exc, tb):
            fake_conn.close()
            return False

        # Some callers may not use `with`; make direct .close() work too.
        def close(self_inner):
            fake_conn.close()

        # Forward attribute access to the fake conn for anyone using the
        # return value as a connection rather than via context manager.
        def __getattr__(self_inner, item):
            return getattr(fake_conn, item)

    def fake_connect(*args, **kwargs):
        return _ConnectCtx()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    return fake_connect
