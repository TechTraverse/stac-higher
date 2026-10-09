"""``pipeline.cube_maintain_sink``: trim, expire, GC and measure one cube (spec §10; ADR 0022).

The job runs under the sink's ``lock`` (``cube:{id}``), so it never runs
alongside that sink's appends (spec §14.4). One run does six things:
1. Prune the sink's terminal ledger rows older than 7 days.
2. Open the repository if it exists. Maintenance never creates one.
3. Only for a sink with a ``window`` (ADR 0022: GC runs only there):
   a. Trim by age with no new data. If nothing is pending and the tip is the
      recorded snapshot, drop the steps the window no longer holds (§6.2
      step 6) in one commit, then record that commit.
   b. ``expire_snapshots`` then ``garbage_collect`` at a cutoff that spares
      every snapshot that was the tip within the retention (``expiry_cutoff``).
4. Republish the recorded tip on the cube collection (``after_batch``, Z-5's
   idempotent writer). That covers a trim, and also a publish that an
   append's crash missed.
5. List the repository prefix and record object counts and bytes per kind.
   Icechunk never deletes ``transactions/`` or ``overwritten/`` (I-143). At or
   above ``CUBE_REPO_WARN_BYTES`` the run's status is ``attention``.
6. Write ``last_maintenance`` on every run, and ``last_maintained_at`` only
   on success.

A sink without a window gets steps 1, 2, 4, 5 and 6, never 3. Its ledger
still needs pruning, and its repository, which nothing trims, is the one
most likely to cross the size warning.

The Icechunk and boto3 parts block, so they run through ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import icechunk as ic

from pipeline.config import (
    DEFAULT_CUBE_REPO_WARN_BYTES,
    DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS,
    Settings,
)
from pipeline.cubes.append import AfterBatch
from pipeline.cubes.config import CubeSinkConfig, parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, CubeState, cube_prefix, open_existing, read_state
from pipeline.cubes.repo import CubeRepo, CubeSink
from pipeline.cubes.steps import trim_count
from pipeline.cubes.write import MAX_ERROR_CHARS, BatchResult, error_text, trim_steps
from pipeline.storage.platform import build_platform_client, list_sizes

logger = logging.getLogger(__name__)

#: spec §4.2: terminal ledger rows older than this are pruned
LEDGER_RETENTION_DAYS = 7
#: the per-kind readout (spec §10); anything else (``repo``, ``config.yaml``) is ``other``
KINDS = ("transactions", "overwritten", "manifests", "snapshots", "chunks")
OTHER_KIND = "other"
#: the GCSummary counters recorded in ``last_maintenance.gc``
GC_COUNTS = (
    "snapshots_deleted",
    "manifests_deleted",
    "chunks_deleted",
    "transaction_logs_deleted",
    "attributes_deleted",
    "bytes_deleted",
    "objects_failed_to_delete",
)
MAX_DELETE_ERRORS = 5
STATUS_OK = "ok"
STATUS_ATTENTION = "attention"
STATUS_FAILED = "failed"
ATTENTION_REPO_SIZE = "repo_size"
ATTENTION_GC_DELETE_FAILURES = "gc_delete_failures"
ATTENTION_UNRECORDED_TIP = "unrecorded_tip"
#: ends every maintenance trim's commit message, so a later run can tell a
#: trim it made (and failed to record) from an append's commit
MAINTENANCE_MESSAGE_SUFFIX = "(maintenance)"

#: every object in a cube repository: (key relative to its prefix, bytes)
ListObjects = Callable[[CubeSink], list[tuple[str, int]]]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


@dataclass
class MaintainDeps:
    repo: CubeRepo
    #: the Icechunk Storage of a sink's repository (``icerepo.cube_storage``)
    storage_for: Callable[[CubeSink], Any]
    list_objects: ListObjects
    retention_seconds: int = DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS
    warn_bytes: int = DEFAULT_CUBE_REPO_WARN_BYTES
    #: Z-5's collection asset writer (the same hook ``cube_append`` calls)
    after_batch: AfterBatch | None = None
    now: Callable[[], dt.datetime] = _utcnow


@dataclass(frozen=True)
class RepoPass:
    """What the blocking repository pass did."""

    exists: bool
    #: the branch tip after the pass (the trim commit, if there was one)
    tip: str | None = None
    #: the tip's cube: its time values AND its grid (``statics``); ``None``
    #: until the cube has its first step. The grid goes to Z-5's writer:
    #: for a stopped source this republish is the only one, and a form save
    #: drops ``cube:dimensions``, so it must never publish time alone.
    state: CubeState | None = None
    trimmed: int = 0
    #: the pass ended on a tip maintenance owns (it trimmed, or healed one)
    record_tip: bool = False
    #: the pass found an unrecorded maintenance trim and took it over
    healed: bool = False
    expired: int | None = None
    gc: dict[str, Any] | None = None
    durations_ms: dict[str, int] = field(default_factory=dict)


def size_by_kind(objects: Iterable[tuple[str, int]]) -> dict[str, dict[str, int]]:
    """Object counts and bytes per top-level directory of the repository."""
    sizes = {kind: {"objects": 0, "bytes": 0} for kind in (*KINDS, OTHER_KIND)}
    for key, size in objects:
        head, sep, _ = key.partition("/")
        kind = head if sep and head in KINDS else OTHER_KIND
        sizes[kind]["objects"] += 1
        sizes[kind]["bytes"] += size
    return sizes


def gc_counts(summary: ic.GCSummary) -> dict[str, Any]:
    counts: dict[str, Any] = {name: int(getattr(summary, name)) for name in GC_COUNTS}
    counts["delete_errors"] = [
        str(err)[:MAX_ERROR_CHARS] for err in list(summary.delete_errors)[:MAX_DELETE_ERRORS]
    ]
    return counts


def expiry_cutoff(
    repo: ic.Repository,
    now: dt.datetime,
    retention_seconds: int,
    *,
    recorded_snapshot_id: str | None,
) -> dt.datetime:
    """The expiry and GC cutoff. Icechunk ages a snapshot by when it was
    WRITTEN, but a reader holds the snapshot that was the tip when it last
    looked, and a snapshot stops being the tip only when its child is
    written. So the newest ancestor written at or before ``now - retention``
    was the tip until after the cutoff: the cutoff drops to its write time
    (the bound is exclusive, so it survives). Without this, a trim commit, or
    an append after a quiet spell, lets the same pass expire the snapshot
    readers were on. The recorded snapshot is the one the collection's asset
    ``version`` names, so it survives any crash ordering. A provisional
    repository (``recorded_snapshot_id=None``) keeps the plain cutoff: nothing
    published it, and its reset moved ``main`` backwards."""
    cutoff = now - dt.timedelta(seconds=retention_seconds)
    if recorded_snapshot_id is None:
        return cutoff
    lowest = cutoff
    aged = recorded = False
    for snapshot in repo.ancestry(branch=BRANCH):
        if not aged and snapshot.written_at <= cutoff:
            aged = True
            lowest = min(lowest, snapshot.written_at)
        if not recorded and snapshot.id == recorded_snapshot_id:
            recorded = True
            lowest = min(lowest, snapshot.written_at)
        if aged and recorded:
            break
    return lowest


def maintain_repository(
    storage: ic.Storage,
    config: CubeSinkConfig,
    now: dt.datetime,
    *,
    retention_seconds: int,
    recorded_snapshot_id: str | None,
    may_trim: bool,
) -> RepoPass:
    """Blocking: trim (windowed, recorded tip, nothing pending), expire and GC
    (windowed only), then read the tip's time values."""
    repo = open_existing(storage)
    if repo is None:
        return RepoPass(exists=False)
    durations: dict[str, int] = {}
    tip = repo.lookup_branch(BRANCH)
    trimmed = 0
    healed = False
    owned = recorded_snapshot_id
    if recorded_snapshot_id is not None and tip != recorded_snapshot_id:
        # A trim committed, then the job died before recording it: that tip
        # is maintenance's own, and a stopped source has no append to record it.
        head = next(iter(repo.ancestry(branch=BRANCH)), None)
        if (
            head is not None
            and head.parent_id == recorded_snapshot_id
            and head.message.endswith(MAINTENANCE_MESSAGE_SUFFIX)
        ):
            healed = True
            owned = tip
    if config.window is not None and may_trim and tip == owned:
        started = time.monotonic()
        session = repo.writable_session(BRANCH)
        state = read_state(session, config.append_dim)
        trimmed = trim_count(state.values, config.window, now) if state.initialised else 0
        if trimmed:
            trim_steps(session, state, trimmed)
            tip = session.commit(f"trim {trimmed} steps {MAINTENANCE_MESSAGE_SUFFIX}")
        durations["trim"] = _ms(started)
    expired: int | None = None
    gc: dict[str, Any] | None = None
    if config.window is not None:
        cutoff = expiry_cutoff(
            repo, now, retention_seconds, recorded_snapshot_id=recorded_snapshot_id
        )
        started = time.monotonic()
        expired = len(repo.expire_snapshots(cutoff))
        durations["expire"] = _ms(started)
        started = time.monotonic()
        gc = gc_counts(repo.garbage_collect(cutoff))
        durations["gc"] = _ms(started)
    after = read_state(repo.readonly_session(BRANCH), config.append_dim)
    return RepoPass(
        exists=True,
        tip=tip,
        state=after if after.initialised else None,
        trimmed=trimmed,
        record_tip=healed or trimmed > 0,
        healed=healed,
        expired=expired,
        gc=gc,
        durations_ms=durations,
    )


def platform_lister(settings: Settings) -> ListObjects:
    """Production ``list_objects``: the cube prefix in the platform bucket.
    The trailing slash keeps ``_cube`` from matching a ``_cube…`` sibling."""

    def _list(sink: CubeSink) -> list[tuple[str, int]]:
        prefix = f"{cube_prefix(sink.cube_collection_id)}/"
        client = build_platform_client(settings)
        return [
            (key[len(prefix) :], size)
            for key, size in list_sizes(client, settings.staging_bucket, prefix)
        ]

    return _list


async def run_cube_maintain(cube_sink_id: str, deps: MaintainDeps) -> dict[str, Any] | None:
    """Maintain one sink and record the summary. ``None`` for a sink that is
    gone or disabled (nothing recorded). An error is recorded as status
    ``failed`` and then raised."""
    sink = await deps.repo.load_sink(cube_sink_id)
    if sink is None or not sink.enabled:
        return None
    now = deps.now()
    summary: dict[str, Any] = {
        "status": STATUS_OK,
        "started_at": now.isoformat(),
        "warn_bytes": deps.warn_bytes,
        "attention": [],
        "durations_ms": {},
    }
    try:
        await _maintain(sink, deps, now, summary)
    except Exception as exc:
        summary["status"] = STATUS_FAILED
        summary["error"] = error_text(exc)
        summary["finished_at"] = deps.now().isoformat()
        try:
            await deps.repo.record_maintenance(sink.id, summary=summary, maintained_at=None)
        except Exception:
            logger.exception(
                "cube_maintain: could not record the failure", extra={"cube_sink_id": sink.id}
            )
        raise
    finished = deps.now()
    summary["finished_at"] = finished.isoformat()
    await deps.repo.record_maintenance(sink.id, summary=summary, maintained_at=finished)
    logger.info(
        "cube_maintain finished",
        extra={
            "cube_sink_id": sink.id,
            "status": summary["status"],
            "trimmed": summary.get("trimmed", 0),
            "total_bytes": summary.get("total_bytes"),
            "ledger_pruned": summary["ledger_pruned"],
        },
    )
    return summary


async def _maintain(
    sink: CubeSink, deps: MaintainDeps, now: dt.datetime, summary: dict[str, Any]
) -> None:
    # The ledger first: a store outage must not stop DB hygiene.
    summary["ledger_pruned"] = await deps.repo.prune_ledger(sink.id, LEDGER_RETENTION_DAYS)
    config = parse_cube_sink_config(sink.config)
    summary["window"] = config.window is not None
    # A provisional repository (nothing recorded) belongs to the writer
    # (#98); a pending row means the next append trims anyway.
    pending = await deps.repo.has_pending(sink.id)
    may_trim = sink.last_snapshot_id is not None and not pending
    rp = await asyncio.to_thread(
        lambda: maintain_repository(
            deps.storage_for(sink),
            config,
            now,
            retention_seconds=deps.retention_seconds,
            recorded_snapshot_id=sink.last_snapshot_id,
            may_trim=may_trim,
        )
    )
    summary["repository"] = rp.exists
    summary["durations_ms"].update(rp.durations_ms)
    if not rp.exists:
        return
    summary.update(trimmed=rp.trimmed, healed=rp.healed, expired_snapshots=rp.expired, gc=rp.gc)
    recorded = sink.last_snapshot_id
    if rp.record_tip and recorded is not None and rp.tip is not None and rp.tip != recorded:
        if await deps.repo.record_snapshot(sink.id, snapshot_id=rp.tip, from_snapshot_id=recorded):
            recorded = rp.tip
        else:
            logger.warning(
                "cube_maintain: the sink moved during the trim; the next append records the tip",
                extra={"cube_sink_id": sink.id, "snapshot_id": rp.tip},
            )
    if recorded is not None and rp.tip != recorded and not pending:
        summary["attention"].append(ATTENTION_UNRECORDED_TIP)
        logger.warning(
            "cube_maintain: the tip is not the recorded snapshot and nothing is pending",
            extra={
                "cube_sink_id": sink.id,
                "snapshot_id": rp.tip,
                "recorded_snapshot_id": recorded,
            },
        )
    # Z-5's writer publishes only the recorded tip, so the trim is recorded first.
    summary["published"] = False
    if deps.after_batch is not None and rp.state is not None and recorded == rp.tip:
        await deps.after_batch(
            sink,
            config,
            BatchResult(
                outcomes={},
                snapshot_id=rp.tip,
                committed=rp.trimmed > 0,
                values=rp.state.values,
                trimmed=rp.trimmed,
                initialised=True,
                statics=rp.state.statics,
                spatial_dims=rp.state.spatial_dims,
            ),
        )
        summary["published"] = True
    if rp.gc is not None and rp.gc["objects_failed_to_delete"]:
        summary["attention"].append(ATTENTION_GC_DELETE_FAILURES)
        logger.warning(
            "cube GC could not delete some objects",
            extra={"cube_sink_id": sink.id, **rp.gc},
        )
    started = time.monotonic()
    sizes = size_by_kind(await asyncio.to_thread(deps.list_objects, sink))
    summary["durations_ms"]["list"] = _ms(started)
    total_bytes = sum(kind["bytes"] for kind in sizes.values())
    summary.update(
        sizes=sizes,
        total_objects=sum(kind["objects"] for kind in sizes.values()),
        total_bytes=total_bytes,
    )
    if total_bytes >= deps.warn_bytes:
        summary["attention"].append(ATTENTION_REPO_SIZE)
        logger.warning(
            "cube repository at or above CUBE_REPO_WARN_BYTES (I-143)",
            extra={
                "cube_sink_id": sink.id,
                "cube_collection_id": sink.cube_collection_id,
                "total_bytes": total_bytes,
                "warn_bytes": deps.warn_bytes,
                "transactions_bytes": sizes["transactions"]["bytes"],
                "overwritten_bytes": sizes["overwritten"]["bytes"],
            },
        )
    if summary["attention"]:
        summary["status"] = STATUS_ATTENTION
