"""Virtual cube sink jobs (virtual cube spec §5; ADR 0022).

One writer per cube repository: every ``pipeline.cube_append`` is deferred
with ``lock`` and ``queueing_lock`` = ``cube:{sink_id}``. The lock keeps two
appends to one repository from ever running together; the queueing lock
coalesces a burst of wake-ups into the one job that is already waiting, which
drains every pending ledger row when it runs (§5.1).

- The dispatcher (``dispatcher/loop.py``) writes the ledger rows and wakes the
  sink through :func:`cube_append_enqueuer` (§5.2).
- ``pipeline.cube_kick`` (every 5 minutes) first hands any ``cube_append`` a
  dead worker left running back to the queue (``QueueBackend.retry_stalled``),
  since it would hold its sink's lock forever. It then re-enqueues sinks
  holding ``pending`` rows older than 2 minutes, recovering a job lost between
  the ledger insert and the enqueue (§5.3).
- ``pipeline.cube_append`` is a **Z-3 stub**: it marks the sink's pending rows
  ``failed`` with reason ``not_implemented``. Z-4 (#90) replaces it with the
  real append. It writes nothing to ``cube_sinks``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from pipeline.config import Settings
from pipeline.cubes.repo import CubeRepo, PgCubeRepo
from pipeline.queue.interface import Enqueued, QueueBackend

logger = logging.getLogger(__name__)

JOB_CUBE_APPEND = "pipeline.cube_append"
JOB_CUBE_KICK = "pipeline.cube_kick"
KICK_CRON = "*/5 * * * *"
#: §5.3: a pending row this old with no job in sight was probably stranded.
KICK_STALE_SECONDS = 120
#: The stub's failure reason (free text; failed reasons are not a closed set).
REASON_NOT_IMPLEMENTED = "not_implemented"


def cube_lock(cube_sink_id: str) -> str:
    """The sink's ``lock`` and ``queueing_lock`` (spec §2)."""
    return f"cube:{cube_sink_id}"


async def enqueue_cube_append(queue: QueueBackend, cube_sink_id: str) -> Enqueued:
    """Wake the sink's single writer. A coalesced result means a job is
    already waiting and will read the new ledger rows: success, not retry."""
    lock = cube_lock(cube_sink_id)
    return await queue.enqueue(
        JOB_CUBE_APPEND,
        {"cube_sink_id": cube_sink_id},
        lock=lock,
        queueing_lock=lock,
    )


def cube_append_enqueuer(queue: QueueBackend) -> Callable[[list[str]], Awaitable[None]]:
    """The dispatcher's callback: one ``cube_append`` per distinct sink."""

    async def _enqueue(cube_sink_ids: list[str]) -> None:
        for cube_sink_id in dict.fromkeys(cube_sink_ids):
            result = await enqueue_cube_append(queue, cube_sink_id)
            if result.coalesced:
                logger.debug(
                    "cube_append already waiting; coalesced",
                    extra={"cube_sink_id": cube_sink_id},
                )

    return _enqueue


async def kick_stale_sinks(repo: CubeRepo, queue: QueueBackend) -> int:
    """§5.3 backstop: re-enqueue every enabled sink with a stale pending row.
    Returns how many sinks were kicked (coalesced or not).

    First, any ``cube_append`` a dead worker left running goes back to the
    queue (plan decision 14). Until then it holds its sink's lock, and the
    enqueue below would only coalesce into a job that can never start."""
    await queue.retry_stalled(JOB_CUBE_APPEND)
    cube_sink_ids = await repo.sinks_with_stale_pending(KICK_STALE_SECONDS)
    await cube_append_enqueuer(queue)(cube_sink_ids)
    return len(cube_sink_ids)


def register(
    queue: QueueBackend, settings: Settings, *, repo: CubeRepo | None = None
) -> None:
    def _repo() -> CubeRepo:
        return repo if repo is not None else PgCubeRepo(settings.database_url)

    async def cube_append(cube_sink_id: str) -> None:
        # Z-3 stub (spec §15): Z-4 (#90) replaces this with the real append.
        failed = await _repo().fail_pending(cube_sink_id, REASON_NOT_IMPLEMENTED)
        logger.warning(
            "cube_append is not implemented yet; pending rows marked failed",
            extra={
                "cube_sink_id": cube_sink_id,
                "rows": failed,
                "reason": REASON_NOT_IMPLEMENTED,
            },
        )

    async def cube_kick(timestamp: int) -> None:
        kicked = await kick_stale_sinks(_repo(), queue)
        if kicked:
            logger.info(
                "cube_kick re-enqueued sinks with stale pending rows",
                extra={"sinks": kicked, "scheduled_timestamp": timestamp},
            )

    # Default queue: the append reads headers, not bytes (spec §6). Retry is
    # Z-4's (§6: 3 attempts); the stub cannot fail usefully.
    queue.register_task(cube_append, name=JOB_CUBE_APPEND)
    queue.register_periodic(cube_kick, name=JOB_CUBE_KICK, cron=KICK_CRON)
