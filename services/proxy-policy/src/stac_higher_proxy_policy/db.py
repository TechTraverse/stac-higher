"""Read-only loader for the externally-writable collection set (ADR 0015).

One short-lived async connection per cache refresh (every ~15 s at most) —
no pool needed. The session is forced read-only as defense in depth; cloud
deployments should additionally provision a read-only DB role for the proxy
(ADR 0015 consequences / Phase 8 IaC note).

Archived collections are excluded: ``archived`` means no item writes
(ADR 0011), so an archived-but-flagged collection must not accept direct
pushes any more than it accepts app-side writes.
"""

from __future__ import annotations

import psycopg

QUERY = """
    SELECT collection_id
    FROM stac_higher.collection_settings
    WHERE externally_writable AND NOT archived
"""


async def load_externally_writable_set(database_url: str) -> frozenset[str]:
    """Return the ids of collections currently open to direct external writes."""
    async with (
        await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=5,
            options="-c default_transaction_read_only=on",
            autocommit=True,
        ) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute(QUERY)
        rows = await cur.fetchall()
    return frozenset(row[0] for row in rows)
