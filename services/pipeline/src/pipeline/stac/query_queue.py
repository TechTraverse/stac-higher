"""pgstac's query queue: the drain the pipeline owns locally (M3-A, spec §4.3).

With `pgstac.use_queue` on (a session GUC on the writer's connections —
pipeline/db/pgstac_session.py), every item write enqueues its partition's
`update_partition_stats` into `pgstac.query_queue` instead of running it
inline. The queue dedupes by query text, so its depth is bounded by the number
of distinct partitions written, not by write volume — and the drain cadence is
what bounds how stale a partition's statistics may get (spec §4.6).

pgstac's intended drainer is pg_cron calling `run_queued_queries()`. pg_cron is
not in the pgstac image, so locally (and on any Postgres without it) this
module's tick is the drainer; a deployment where the database drains sets
`PGSTAC_QUEUE_DRAINER=database` and the tick only samples, so the two cannot
fight. Either way the sample is the one outside-the-process proof the setting
is in effect, and a rising age is the signal that whichever drainer is
configured has stopped.

`run_queued_queries()` is a PROCEDURE that COMMITs per statement: it needs
`CALL` on an AUTOCOMMIT connection (`SELECT` errors; a transaction block errors
with "invalid transaction termination"). pgstac appends every executed
statement to `query_queue_history` and never prunes it; the tick does.

The drain connection's GUCs are deliberately the OPPOSITE pairing from the
writer's (pipeline/db/pgstac_session.py): `update_collection_extent` ON,
`use_queue` OFF. `update_partition_stats` (queued by the item trigger) itself
calls `run_or_queue` again, in whichever session runs it, to refresh the
collection's extent — gated on `update_collection_extent`, which
`pgstac_settings` defaults to false. That nested call must see the setting ON
here, or the extent refresh never runs (spec §4.4's entire point). And
`use_queue` must stay OFF here, or that same nested call re-queues the extent
UPDATE instead of executing it — deferring it one hop further on every drain,
forever.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass

from pipeline import metrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueueSample:
    depth: int
    #: Seconds since the oldest queued statement was added; None when empty.
    oldest_age_seconds: float | None


@dataclass(frozen=True)
class DrainOutcome:
    #: Statements pgstac executed during this CALL (from query_queue_history).
    executed: int
    #: Of those, how many pgstac recorded an error for. The row is still
    #: removed from the queue — the next write to that partition re-queues it.
    errors: int


@dataclass(frozen=True)
class TickResult:
    mode: str
    before: QueueSample
    after: QueueSample | None
    drained: DrainOutcome | None
    pruned: int
    stale: bool


class PgstacQueueRepo(abc.ABC):
    """The three statements the tick needs; unit tests use a fake."""

    @abc.abstractmethod
    async def sample(self) -> QueueSample: ...

    @abc.abstractmethod
    async def drain(self) -> DrainOutcome: ...

    @abc.abstractmethod
    async def prune_history(self, older_than_days: int) -> int: ...


@dataclass
class PgPgstacQueueRepo(PgstacQueueRepo):
    """psycopg-backed repo. One short-lived AUTOCOMMIT connection per call —
    the CALL commits inside itself and cannot run in a transaction block.
    (M3-B: keep this off the transactional repo pool for that reason.)"""

    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        conn = await psycopg.AsyncConnection.connect(self.database_url, autocommit=True)
        # Deliberately NOT pipeline.db.pgstac_session.configure_pgstac_session_async:
        # that hook sets BOTH GUCs and is the WRITER's pairing (use_queue ON,
        # update_collection_extent ON). The drainer needs the opposite pairing
        # — see the module docstring. use_queue is left at its default (off)
        # by simply never setting it on this connection.
        await conn.execute("SET pgstac.update_collection_extent TO TRUE")
        return conn

    async def sample(self) -> QueueSample:  # pragma: no cover - DB integration suite
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT count(*), EXTRACT(EPOCH FROM (now() - min(added))) FROM pgstac.query_queue"
            )
            row = await cur.fetchone()
        depth = int(row[0]) if row else 0
        age = float(row[1]) if row and row[1] is not None else None
        return QueueSample(depth=depth, oldest_age_seconds=age)

    async def drain(self) -> DrainOutcome:  # pragma: no cover - DB integration suite
        async with await self._connect() as conn:
            cur = await conn.execute("SELECT clock_timestamp()")
            started = (await cur.fetchone())[0]
            # PROCEDURE with COMMIT inside: CALL, autocommit connection.
            await conn.execute("CALL pgstac.run_queued_queries()")
            cur = await conn.execute(
                "SELECT count(*), count(error) FROM pgstac.query_queue_history"
                " WHERE finished >= %s",
                (started,),
            )
            row = await cur.fetchone()
        return DrainOutcome(executed=int(row[0]), errors=int(row[1]))

    async def prune_history(self, older_than_days: int) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "DELETE FROM pgstac.query_queue_history"
                " WHERE finished < now() - make_interval(days => %s)",
                (older_than_days,),
            )
            return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0


async def drain_tick(
    repo: PgstacQueueRepo,
    *,
    mode: str,
    stale_after_seconds: int,
    history_days: int,
) -> TickResult:
    """One tick: sample, drain if this deployment owns it, sample again, prune.

    Gauges carry the LAST sample — what is left for the next tick. The stale
    flag reads the last sample too: in `pipeline` mode it means a CALL hit
    `queue_timeout` with work left (or is failing outright); in `database`
    mode it means pg_cron has stopped. Both are WARNINGs with the numbers.
    """
    before = await repo.sample()
    after: QueueSample | None = None
    drained: DrainOutcome | None = None

    if mode == "pipeline":
        drained = await repo.drain()
        metrics.PGSTAC_QUEUE_QUERIES.labels(outcome="ok").inc(drained.executed - drained.errors)
        metrics.PGSTAC_QUEUE_QUERIES.labels(outcome="error").inc(drained.errors)
        after = await repo.sample()

    pruned = await repo.prune_history(history_days)

    last = after if after is not None else before
    metrics.PGSTAC_QUEUE_DEPTH.set(last.depth)
    metrics.PGSTAC_QUEUE_OLDEST_SECONDS.set(last.oldest_age_seconds or 0.0)

    stale = last.oldest_age_seconds is not None and last.oldest_age_seconds > stale_after_seconds
    if stale:
        logger.warning(
            "pgstac query queue is stale — the configured drainer is behind or stopped",
            extra={
                "mode": mode,
                "depth": last.depth,
                "oldest_age_seconds": last.oldest_age_seconds,
                "stale_after_seconds": stale_after_seconds,
            },
        )

    return TickResult(
        mode=mode, before=before, after=after, drained=drained, pruned=pruned, stale=stale
    )
