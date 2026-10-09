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
- ``pipeline.cube_append`` runs ``cubes.append.run_cube_append`` (Z-4): it
  claims up to 50 pending rows, parses their headers 4 at a time off the
  event loop, appends them virtually in one Icechunk commit, trims the
  window, records the commit and the ledger, and re-enqueues itself while
  rows remain. Retry: ``CUBE_APPEND_RETRY`` (shares Procrastinate's attempt
  budget with ``retry_stalled``'s cap of 3). A source read that fails in
  transit ends the job without raising; this kick paces that retry (I-145).
  After the commit is recorded and before the ledger is written, it
  publishes the cube on its collection (``cubes.collection``, Z-5).
- ``pipeline.cube_maintain`` (hourly, ``:23``) enqueues one
  ``pipeline.cube_maintain_sink`` per enabled sink with the same ``lock``
  (no ``queueing_lock``: that one is the append's), so maintenance never runs
  alongside the sink's appends (spec §10, §14.4). ``cube_kick`` recovers a
  stalled maintenance job too, since it would hold the same lock.
  ``cube_maintain_sink`` runs ``cubes.maintain.run_cube_maintain``: expiry and
  GC only on a sink with a window (ADR 0022). No retry: the next hour retries.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable

from pipeline.config import Settings
from pipeline.cubes.append import AppendDeps, run_cube_append
from pipeline.cubes.collection import production_after_batch
from pipeline.cubes.icerepo import cube_storage
from pipeline.cubes.maintain import MaintainDeps, platform_lister, run_cube_maintain
from pipeline.cubes.repo import CubeRepo, PgCubeRepo
from pipeline.cubes.resolve import PgSourceResolver
from pipeline.jobs._common import load_key_or_skip
from pipeline.queue.interface import Enqueued, QueueBackend, RetrySpec

logger = logging.getLogger(__name__)

JOB_CUBE_APPEND = "pipeline.cube_append"
JOB_CUBE_KICK = "pipeline.cube_kick"
KICK_CRON = "*/5 * * * *"
JOB_CUBE_MAINTAIN = "pipeline.cube_maintain"
JOB_CUBE_MAINTAIN_SINK = "pipeline.cube_maintain_sink"
MAINTAIN_CRON = "23 * * * *"
#: jobs holding a sink's lock: a dead worker's copy of either wedges the sink
LOCKED_JOBS = (JOB_CUBE_APPEND, JOB_CUBE_MAINTAIN_SINK)
#: §5.3: a pending row this old with no job in sight was probably stranded.
KICK_STALE_SECONDS = 120
#: §6: three attempts. RetrySpec waits a fixed time (no exponential form), and
#: Procrastinate counts retry_stalled requeues in the same attempts budget.
CUBE_APPEND_RETRY = RetrySpec(max_attempts=3, wait_seconds=30)


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


async def enqueue_cube_maintain(queue: QueueBackend, cube_sink_id: str) -> Enqueued:
    """One sink's maintenance, under the append's ``lock`` (spec §10)."""
    return await queue.enqueue(
        JOB_CUBE_MAINTAIN_SINK, {"cube_sink_id": cube_sink_id}, lock=cube_lock(cube_sink_id)
    )


async def schedule_maintenance(repo: CubeRepo, queue: QueueBackend) -> int:
    """The hourly fan-out: one ``cube_maintain_sink`` per enabled sink."""
    cube_sink_ids = await repo.maintainable_sinks()
    for cube_sink_id in cube_sink_ids:
        await enqueue_cube_maintain(queue, cube_sink_id)
    return len(cube_sink_ids)


async def kick_stale_sinks(repo: CubeRepo, queue: QueueBackend) -> int:
    """§5.3 backstop: re-enqueue every enabled sink with a stale pending row.
    Returns how many sinks were kicked (coalesced or not).

    First, any ``cube_append`` or ``cube_maintain_sink`` a dead worker left
    running goes back to the queue (plan decision 14). Until then it holds
    its sink's lock, and the enqueue below would only coalesce into a job
    that can never start. A failed recovery is logged and the stale-sink kick
    still runs, so one bad query cannot disable the backstop."""
    for job_name in LOCKED_JOBS:
        try:
            await queue.retry_stalled(job_name)
        except Exception:
            logger.exception(
                "cube_kick: stalled-job recovery failed",
                extra={"job_name": job_name},
            )
    cube_sink_ids = await repo.sinks_with_stale_pending(KICK_STALE_SECONDS)
    await cube_append_enqueuer(queue)(cube_sink_ids)
    return len(cube_sink_ids)


def production_append_deps(
    settings: Settings, queue: QueueBackend, repo: CubeRepo
) -> AppendDeps | None:
    """The real seams, or ``None`` without a credential master key (logged by
    ``load_key_or_skip``; the rows wait for the next kick)."""
    master_key = load_key_or_skip(settings, JOB_CUBE_APPEND)
    if master_key is None:
        return None

    async def enqueue_next(cube_sink_id: str) -> Enqueued:
        return await enqueue_cube_append(queue, cube_sink_id)

    return AppendDeps(
        repo=repo,
        resolver=PgSourceResolver.from_settings(settings, master_key),
        storage_for=lambda sink: cube_storage(settings, sink.cube_collection_id),
        enqueue_next=enqueue_next,
        after_batch=production_after_batch(settings),
    )


def production_maintain_deps(settings: Settings, repo: CubeRepo) -> MaintainDeps:
    """The real seams. No master key: maintenance never reads a source."""
    return MaintainDeps(
        repo=repo,
        storage_for=lambda sink: cube_storage(settings, sink.cube_collection_id),
        list_objects=platform_lister(settings),
        retention_seconds=settings.cube_snapshot_retention_seconds,
        warn_bytes=settings.cube_repo_warn_bytes,
        after_batch=production_after_batch(settings),
    )


def register(
    queue: QueueBackend,
    settings: Settings,
    *,
    repo: CubeRepo | None = None,
    deps_factory: Callable[[], AppendDeps | None] | None = None,
    maintain_deps_factory: Callable[[], MaintainDeps] | None = None,
) -> None:
    def _repo() -> CubeRepo:
        return repo if repo is not None else PgCubeRepo(settings.database_url)

    async def cube_append(cube_sink_id: str) -> None:
        deps = (
            deps_factory()
            if deps_factory is not None
            else production_append_deps(settings, queue, _repo())
        )
        if deps is None:
            return
        report = await run_cube_append(cube_sink_id, deps)
        logger.info(
            "cube_append finished",
            extra={"cube_sink_id": cube_sink_id, **dataclasses.asdict(report)},
        )

    async def cube_kick(timestamp: int) -> None:
        kicked = await kick_stale_sinks(_repo(), queue)
        if kicked:
            logger.info(
                "cube_kick re-enqueued sinks with stale pending rows",
                extra={"sinks": kicked, "scheduled_timestamp": timestamp},
            )

    async def cube_maintain(timestamp: int) -> None:
        scheduled = await schedule_maintenance(_repo(), queue)
        if scheduled:
            logger.info(
                "cube_maintain enqueued sink maintenance",
                extra={"sinks": scheduled, "scheduled_timestamp": timestamp},
            )

    async def cube_maintain_sink(cube_sink_id: str) -> None:
        deps = (
            maintain_deps_factory()
            if maintain_deps_factory is not None
            else production_maintain_deps(settings, _repo())
        )
        await run_cube_maintain(cube_sink_id, deps)

    # Default queue: the append reads headers, not bytes (spec §6).
    queue.register_task(cube_append, name=JOB_CUBE_APPEND, retry=CUBE_APPEND_RETRY)
    queue.register_periodic(cube_kick, name=JOB_CUBE_KICK, cron=KICK_CRON)
    # Maintenance lists and deletes small Icechunk objects: default queue too.
    queue.register_task(cube_maintain_sink, name=JOB_CUBE_MAINTAIN_SINK)
    queue.register_periodic(cube_maintain, name=JOB_CUBE_MAINTAIN, cron=MAINTAIN_CRON)
