"""pgstac session settings the pipeline's writers carry (M3-A, spec §4.2).

`pgstac.get_setting` resolves a setting as conf → session GUC → the
`pgstac_settings` table, so a `SET` on the writer's own connection overrides
the table for that session and nothing else: the app's BFF writes, `psql`,
search and e2e all keep pgstac's inline statistics. This is the library's own
seam for bulk loaders — pypgstac issues the first of these SETs itself when
`PgstacDB(use_queue=True)` checks a connection out of a pool — and it ships
with the release rather than as a deployment step that can be forgotten.

Both hooks COMMIT: `SET` is transactional, and a pool's reset would roll back
an uncommitted one on the connection's first return.

The drainer carries the OPPOSITE pairing (`pipeline/stac/query_queue.py`);
ADR 0020 (`docs/decisions/0020-pgstac-session-guc-pairings.md`) records why
the two must never be unified.
"""

from __future__ import annotations

from typing import Protocol

#: `use_queue` routes `update_partition_stats` (the O(partition) scan +
#: ANALYZE on every item write) into `pgstac.query_queue` — read in the
#: WRITER's session, at the point the item trigger fires.
#: `update_collection_extent` gates a second, nested `run_or_queue` call
#: *inside* `update_partition_stats` that refreshes the collection's
#: advertised extent — but that call runs later, in whichever session
#: DRAINS the queue (`run_queued_queries()`), so setting it here on the
#: writer has no effect on that nested call: `pgstac_settings` defaults it
#: to `false`, and the drainer is the session that must carry it (Task 4).
#: It is still set here, on the writer, because spec §4.2 directs both
#: statements together and doing so is harmless — the writer's own copy of
#: the GUC is simply never read by anything. The two travel together in
#: this module only because the writer needs `use_queue`; the drainer needs
#: the opposite pairing (`update_collection_extent` on, `use_queue`
#: explicitly FALSE — not merely left unset, so it cannot silently inherit
#: whatever `pgstac_settings` or an `ALTER DATABASE`/`ALTER ROLE` says — or
#: it would re-queue the extent refresh instead of running it). ADR 0020.
PGSTAC_SESSION_SQL: tuple[str, ...] = (
    "SET pgstac.use_queue TO TRUE",
    "SET pgstac.update_collection_extent TO TRUE",
)


class _SyncConnection(Protocol):
    def execute(self, query: str, params: object = None) -> object: ...
    def commit(self) -> None: ...


class _AsyncConnection(Protocol):
    async def execute(self, query: str, params: object = None) -> object: ...
    async def commit(self) -> None: ...


def configure_pgstac_session(conn: _SyncConnection) -> None:
    """`psycopg_pool.ConnectionPool(configure=...)` hook — runs once per new connection."""
    for statement in PGSTAC_SESSION_SQL:
        conn.execute(statement)
    conn.commit()


async def configure_pgstac_session_async(conn: _AsyncConnection) -> None:
    """`psycopg_pool.AsyncConnectionPool(configure=...)` hook — the async twin."""
    for statement in PGSTAC_SESSION_SQL:
        await conn.execute(statement)
    await conn.commit()
