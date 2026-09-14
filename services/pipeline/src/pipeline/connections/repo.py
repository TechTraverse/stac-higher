"""Repository seam over ``stac_higher.connections`` + ``connection_checks``.

The drain/health-sweep jobs depend on the :class:`ConnectionsRepo` ABC, so their
claim/transition logic is unit-testable against an in-memory fake — no live DB.
:class:`PgConnectionsRepo` is the psycopg implementation used in production.

The pipeline only ever reads these tables and UPDATEs the health/pin columns
(status, last_checked_at, last_error, host_key, host_key_pinned_at) plus the
check bookkeeping (status, result, finished_at). It NEVER touches
``connections.updated_at`` (that means "user last edited") and NEVER creates the
tables (ADR 0001).
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass
class ConnectionRow:
    id: str
    name: str
    protocol: str
    config: dict[str, Any]
    #: raw credential envelope (bytea) — decrypted only inside a probe.
    credentials: bytes | None
    host_key: str | None
    enabled: bool = True

    def as_adapter_row(self) -> dict[str, Any]:
        """The minimal mapping ``adapter_for`` needs."""
        return {"protocol": self.protocol, "config": self.config}


@dataclass
class ClaimedCheck:
    check_id: str
    connection: ConnectionRow


class ConnectionsRepo(abc.ABC):
    """DB access the connection jobs depend on."""

    @abc.abstractmethod
    async def claim_pending(self, limit: int) -> list[ClaimedCheck]:
        """Atomically claim up to ``limit`` pending checks.

        Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers never collide,
        flips the claimed rows to ``running``, and returns each with its parent
        connection loaded. Returns ``[]`` when nothing is pending.
        """

    @abc.abstractmethod
    async def record_check(self, check_id: str, status: str, result: dict[str, Any]) -> None:
        """Write a terminal check row: ``status`` (done|failed), ``result``
        jsonb, ``finished_at = now()``.
        """

    @abc.abstractmethod
    async def update_connection_health(
        self,
        connection_id: str,
        *,
        status: str,
        last_error: str | None,
        host_key_to_pin: str | None,
    ) -> None:
        """Update a connection's health columns (never ``updated_at``).

        Sets status, ``last_checked_at = now()``, ``last_error``. When
        ``host_key_to_pin`` is given (TOFU first-pin), also sets ``host_key`` and
        ``host_key_pinned_at = now()``.
        """

    @abc.abstractmethod
    async def list_enabled_connections(self) -> list[ConnectionRow]:
        """All ``enabled`` connections, for the health sweep."""


# --------------------------------------------------------------------------- #
# psycopg implementation
# --------------------------------------------------------------------------- #

_CONNECTION_COLUMNS = "id, name, protocol, config, credentials, host_key, enabled"


def _to_connection_row(record: Sequence[Any]) -> ConnectionRow:
    cid, name, protocol, config, credentials, host_key, enabled = record
    return ConnectionRow(
        id=str(cid),
        name=name,
        protocol=protocol,
        config=dict(config) if config else {},
        credentials=bytes(credentials) if credentials is not None else None,
        host_key=host_key,
        enabled=bool(enabled),
    )


@dataclass
class PgConnectionsRepo(ConnectionsRepo):
    """psycopg-backed repo. Checks a connection out of the process-wide async
    pool per operation (M3-B, `pipeline/db/pool.py`)."""

    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        # M3-B: a checkout from the process-wide pool, not a fresh backend.
        # `pool.connection()` is an async context manager with the same
        # commit-on-success / rollback-on-error semantics, so every
        # `async with await self._connect() as conn:` call site is unchanged.
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def claim_pending(self, limit: int) -> list[ClaimedCheck]:  # pragma: no cover
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, connection_id FROM stac_higher.connection_checks"
                    " WHERE status = 'pending' ORDER BY requested_at"
                    " FOR UPDATE SKIP LOCKED LIMIT %s",
                    (limit,),
                )
                claimed = await cur.fetchall()
                if not claimed:
                    return []
                check_ids = [row[0] for row in claimed]
                await cur.execute(
                    "UPDATE stac_higher.connection_checks SET status = 'running'"
                    " WHERE id = ANY(%s)",
                    (check_ids,),
                )
                conn_ids = [row[1] for row in claimed]
                await cur.execute(
                    f"SELECT {_CONNECTION_COLUMNS} FROM stac_higher.connections"
                    " WHERE id = ANY(%s) AND deleted_at IS NULL",
                    (conn_ids,),
                )
                conn_rows = await cur.fetchall()
            await conn.commit()
        by_id = {str(r[0]): _to_connection_row(r) for r in conn_rows}
        results: list[ClaimedCheck] = []
        for check_id, connection_id in claimed:
            connection = by_id.get(str(connection_id))
            if connection is None:
                # parent vanished (cascade race) — nothing to test.
                continue
            results.append(ClaimedCheck(check_id=str(check_id), connection=connection))
        return results

    async def record_check(  # pragma: no cover
        self, check_id: str, status: str, result: dict[str, Any]
    ) -> None:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.connection_checks"
                " SET status = %s, result = %s, finished_at = now() WHERE id = %s",
                (status, Json(result), check_id),
            )
            await conn.commit()

    async def update_connection_health(  # pragma: no cover
        self,
        connection_id: str,
        *,
        status: str,
        last_error: str | None,
        host_key_to_pin: str | None,
    ) -> None:
        async with await self._connect() as conn:
            if host_key_to_pin is not None:
                await conn.execute(
                    "UPDATE stac_higher.connections SET status = %s,"
                    " last_checked_at = now(), last_error = %s,"
                    " host_key = %s, host_key_pinned_at = now() WHERE id = %s",
                    (status, last_error, host_key_to_pin, connection_id),
                )
            else:
                await conn.execute(
                    "UPDATE stac_higher.connections SET status = %s,"
                    " last_checked_at = now(), last_error = %s WHERE id = %s",
                    (status, last_error, connection_id),
                )
            await conn.commit()

    async def list_enabled_connections(self) -> list[ConnectionRow]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_CONNECTION_COLUMNS} FROM stac_higher.connections"
                " WHERE enabled = true AND deleted_at IS NULL ORDER BY created_at"
            )
            rows = await cur.fetchall()
        return [_to_connection_row(r) for r in rows]
