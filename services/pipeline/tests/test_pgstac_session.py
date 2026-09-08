"""The two pgstac session GUCs the writer's connections carry (M3-A, spec §4.2).

Unit-level: the hook issues exactly the two SETs and COMMITs — a SET inside a
transaction the pool later rolls back would silently disappear. The DB-gated
proof that the GUCs survive a pool checkout is in
tests/test_integration_pgstac_queue.py.
"""

from __future__ import annotations

from pipeline.db.pgstac_session import (
    PGSTAC_SESSION_SQL,
    configure_pgstac_session,
    configure_pgstac_session_async,
)


class _SyncConn:
    def __init__(self):
        self.executed: list[str] = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def commit(self):
        self.commits += 1


class _AsyncConn:
    def __init__(self):
        self.executed: list[str] = []
        self.commits = 0

    async def execute(self, sql, params=None):
        self.executed.append(sql)

    async def commit(self):
        self.commits += 1


def test_session_sql_names_both_settings():
    assert PGSTAC_SESSION_SQL == (
        "SET pgstac.use_queue TO TRUE",
        "SET pgstac.update_collection_extent TO TRUE",
    )


def test_sync_hook_sets_both_and_commits():
    conn = _SyncConn()
    configure_pgstac_session(conn)
    assert conn.executed == list(PGSTAC_SESSION_SQL)
    # SET is transactional: without the commit the pool's reset would undo it.
    assert conn.commits == 1


async def test_async_hook_sets_both_and_commits():
    conn = _AsyncConn()
    await configure_pgstac_session_async(conn)
    assert conn.executed == list(PGSTAC_SESSION_SQL)
    assert conn.commits == 1
