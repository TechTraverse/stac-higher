"""Drop a probe's pgstac collection without stranding its queued statistics.

Since M3-A the pipeline's writer runs with `pgstac.use_queue` ON, so every
item write QUEUES `update_partition_stats('_items_<key>')` into
`pgstac.query_queue` instead of running it inline. `pgstac.delete_collection`
drops the partition but not those rows; the next drain tick
(`pipeline.pgstac_queue_drain`, every minute) then executes each one, gets
`relation "_items_<key>" does not exist`, records an error row in
`query_queue_history` and bumps
`pipeline_pgstac_query_queue_queries_total{outcome="error"}` — permanent noise
on a shared database (seen live after the 2026-09-08 `m3a` teardown:
`executed: 2, errors: 2`). Hygiene, not correctness: the queue self-heals.

pgstac names the queued statement after the PARTITION (`_items_<key>`, where
`<key>` is `pgstac.collections.key`, a serial), never after the collection
id, so the name must be resolved WHILE the collection row still exists.
Same shape as `tests/test_integration_pgstac_queue.py`. ADR 0020 has the
GUC pairings this cleanup exists because of.
"""

from __future__ import annotations

from typing import Any, Protocol


class _Cursor(Protocol):
    rowcount: int

    def execute(self, query: str, params: Any = ...) -> Any: ...
    def fetchone(self) -> Any: ...


def partition_queue_pattern(partition: str) -> str:
    """ILIKE pattern for the queued statements of one partition.

    Quoted, so `_items_3` cannot match `_items_34`: the queued text reads
    `SELECT update_partition_stats('_items_3', 't')`."""
    return f"%'{partition}'%"


def resolve_partition(cur: _Cursor, collection_id: str) -> str | None:
    """`_items_<key>` for the collection, or None once its row is gone."""
    cur.execute("SELECT key FROM pgstac.collections WHERE id = %s", (collection_id,))
    row = cur.fetchone()
    return None if row is None else f"_items_{row[0]}"


def clear_partition_queue(cur: _Cursor, partition: str) -> int:
    """Delete the partition's queued and historical statements; returns the
    number of QUEUED rows removed (the ones that would have errored)."""
    pattern = partition_queue_pattern(partition)
    cur.execute("DELETE FROM pgstac.query_queue WHERE query ILIKE %s", (pattern,))
    cleared = cur.rowcount
    cur.execute("DELETE FROM pgstac.query_queue_history WHERE query ILIKE %s", (pattern,))
    return cleared


def drop_probe_collection(cur: _Cursor, collection_id: str) -> int:
    """Resolve → clear → drop, in that order. Returns the cleared queue rows."""
    partition = resolve_partition(cur, collection_id)
    cleared = clear_partition_queue(cur, partition) if partition is not None else 0
    cur.execute("SELECT pgstac.delete_collection(%s)", (collection_id,))
    return cleared
