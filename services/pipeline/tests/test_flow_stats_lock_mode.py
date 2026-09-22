"""The flow_stats rollups lock the association row FOR NO KEY UPDATE (M3-L, #41).

`delivery_log.association_id` / `ingest_files.association_id` reference
`collection_connections(id)`, so a transaction that inserted such rows holds a
FOR KEY SHARE lock on the association row. FOR UPDATE conflicts with another
transaction's share lock (two concurrent deliveries deadlocked on M3-D's first
load run); FOR NO KEY UPDATE does not. These tests pin the statement text —
the live proof is in #41.
"""

from __future__ import annotations

from pipeline.delivery.repo import PgDeliveryRepo
from pipeline.flow.stats import zero_counts
from pipeline.ingest.repo import PgIngestRepo

ASSOC = "11111111-1111-1111-1111-111111111111"


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return []


class _Conn:
    """Records every statement; the SELECT returns a seeded stats row."""

    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, sql, params=None):
        self.statements.append(sql)
        return _Cursor(({"counts": zero_counts()},))

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _lock_statements(conn: _Conn) -> list[str]:
    return [
        s
        for s in conn.statements
        if "collection_connections" in s and s.lstrip().startswith("SELECT")
    ]


async def test_delivery_rollup_locks_the_association_row_without_the_key_lock():
    conn = _Conn()
    await PgDeliveryRepo(ASSOC).__class__._apply_flow_stats(
        PgDeliveryRepo(ASSOC), conn, ASSOC, [(None, "pending")], activity=True
    )
    (lock,) = _lock_statements(conn)
    assert lock.rstrip().endswith("FOR NO KEY UPDATE")
    assert "FOR UPDATE" not in lock


async def test_ingest_rollup_locks_the_association_row_without_the_key_lock(monkeypatch):
    conn = _Conn()
    repo = PgIngestRepo(database_url="postgresql://unused")

    async def _connect():
        return conn

    monkeypatch.setattr(repo, "_connect", _connect)
    await repo.bump_flow_stats(ASSOC, items=1)
    (lock,) = _lock_statements(conn)
    assert lock.rstrip().endswith("FOR NO KEY UPDATE")
    assert "FOR UPDATE" not in lock
