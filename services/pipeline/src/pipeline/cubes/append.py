"""``pipeline.cube_append``: pending ledger rows → one virtual commit (spec §6.1-6.2; ADR 0022).

One writer per sink: the job runs under the Procrastinate lock ``cube:{id}``.
It stays correct if it runs twice anyway (a stalled-job requeue while the
first run is still alive, #90):
- rows are claimed by bumping ``attempts``;
- the Icechunk commit is optimistic (``write_batch`` redoes a conflict once);
- steps already in the cube read as duplicates;
- ``finish_rows`` touches only rows still pending.

Until the sink records a snapshot (``last_snapshot_id``), its repository is
PROVISIONAL, because the app may still change the source and layout (#98):
- A job that finds unrecorded data resets ``main`` to the root snapshot
  before writing. Those rows are still pending, so nothing is lost. The
  sink is read again just before the write, so a run never resets a
  snapshot another run has already recorded.
- The first commit is recorded only while the sink still has the app
  version the job read. If the race is lost, the rows stay pending and the
  next job rebuilds under the new config.
Nothing is deleted: the orphaned snapshots are Icechunk garbage for Z-6.

Source errors (the lead's rule on PR #101, amending plan Decision 7): an error
about the FILE (missing, denied, corrupt, wrong layout, a real egress block)
is that row's outcome, and the rest of the batch commits. An error in TRANSIT
(``resolve.is_transport_error``: NODD 5xx or throttling, timeouts, refused or
reset connections, DNS) ends the job with :class:`SourceTransportError` before
anything is written: ``last_error`` is set and the job returns WITHOUT raising,
so Procrastinate does not retry it (each retry would be another take and
another bump). The 5-minute ``cube_kick`` re-enqueues the sink and paces the
retries. The rows that hit the error keep the take's attempt bump: the next
take then works the oldest row alone, so a file that never becomes readable
climbs to ``MAX_ROW_ATTEMPTS`` by itself (about 6 kicks, ~25-30 min) and ends
``crash_loop``, while an outage that clears appends every row, no gap (I-145).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from obspec_utils.registry import ObjectStoreRegistry
from obstore.exceptions import NotFoundError

from pipeline.cubes.config import CubeSinkConfig, CubeSinkConfigError, parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, open_repository, read_state, reset_to_root
from pipeline.cubes.repo import CubeRepo, CubeSink, PendingRow, RowOutcome
from pipeline.cubes.resolve import (
    ResolvedSource,
    SourceResolver,
    SourceUnavailable,
    is_transport_error,
)
from pipeline.cubes.source import SourceLibs
from pipeline.cubes.steps import LayoutError, parse_header, step_value
from pipeline.cubes.write import BatchResult, ParsedStep, error_text, write_batch

logger = logging.getLogger(__name__)

#: rows per job; a backlog re-enqueues instead of holding the lock (§14.7)
BATCH_LIMIT = 50
#: headers parsed at once (spike: ~1.45 s per header, ~0.04 s per commit)
PARSE_CONCURRENCY = 4
#: a row claimed more often than this is failed instead of parsed (#90)
MAX_ROW_ATTEMPTS = 6
#: a row claimed more than this often was in a job that died (the exception
#: path and the quiet returns give attempts back): such a take keeps only its
#: oldest row, so a poison file crash-loops alone instead of taking the rest
#: of its batch to `crash_loop` with it (#90; plan Decision 9)
CRASHED_ATTEMPTS = 1
REASON_CRASH_LOOP = "crash_loop"
REASON_LATE = "late"
REASON_SOURCE_MISSING = "source_missing"
REASON_UNSUPPORTED_LAYOUT = "unsupported_layout"

AfterBatch = Callable[[CubeSink, CubeSinkConfig, BatchResult], Awaitable[None]]
Parse = Callable[[str, ObjectStoreRegistry, CubeSinkConfig], Any]


class SourceTransportError(Exception):
    """Source reads failed in transit (``is_transport_error``): the job ends
    and the next ``cube_kick`` retries it. ``row_ids`` keep the take's attempt
    bump; the batch's other rows are given back, and nothing reaches the cube
    or the ledger. ``run_cube_append`` catches it; it never reaches the queue."""

    def __init__(self, row_ids: frozenset[int], first: BaseException) -> None:
        n = len(row_ids)
        reads = "source read" if n == 1 else "source reads"
        super().__init__(f"{n} {reads} failed in transit; first: {error_text(first)}")
        self.row_ids = row_ids


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass
class AppendDeps:
    repo: CubeRepo
    resolver: SourceResolver
    #: the Icechunk Storage of a sink's repository (``icerepo.cube_storage``)
    storage_for: Callable[[CubeSink], Any]
    #: wake the sink's next job (``jobs.cubes.enqueue_cube_append``: same locks)
    enqueue_next: Callable[[str], Awaitable[object]]
    #: Z-5's collection asset writer: awaited after every batch that reached
    #: the repository (recorded tip, ledger written); must be idempotent
    after_batch: AfterBatch | None = None
    now: Callable[[], dt.datetime] = _utcnow
    batch_limit: int = BATCH_LIMIT
    parse: Parse = parse_header


@dataclass
class AppendReport:
    taken: int = 0
    appended: int = 0
    skipped: int = 0
    failed: int = 0
    snapshot_id: str | None = None
    committed: bool = False
    recorded: bool = False
    requeued: bool = False
    #: rows whose source read failed in transit: nothing was finished, and the
    #: job returned for the next cube_kick to retry (``requeued`` stays False)
    in_transit: int = 0


async def run_cube_append(cube_sink_id: str, deps: AppendDeps) -> AppendReport:
    report = AppendReport()
    sink = await deps.repo.load_sink(cube_sink_id)
    if sink is None or not sink.enabled:
        return report  # a disabled sink's rows wait; cube_kick wakes it once re-enabled
    try:
        config = parse_cube_sink_config(sink.config)
    except CubeSinkConfigError as exc:
        await deps.repo.record_error(sink.id, f"invalid config: {exc}")
        logger.error(
            "cube_append: invalid sink config",
            extra={"cube_sink_id": sink.id, "error": str(exc)},
        )
        return report

    rows = await deps.repo.take_pending(sink.id, deps.batch_limit)
    if len(rows) > 1 and any(r.attempts > CRASHED_ATTEMPTS for r in rows):
        # A previous claim never finished: a worker died somewhere in this
        # batch. Work the oldest row alone (rows come in (item_datetime, id)
        # order); has_pending -> enqueue_next carries the backlog on.
        rest = [r.id for r in rows[1:]]
        await deps.repo.release_rows(sink.id, rest)
        logger.warning(
            "cube_append: a previous job died; working one row alone",
            extra={"cube_sink_id": sink.id, "row_id": rows[0].id, "released": len(rest)},
        )
        rows = rows[:1]
    report.taken = len(rows)
    if not rows:
        return report
    try:
        await _append_rows(sink, config, rows, deps, report)
    except Exception as exc:
        # An exception is an outage or a bug, not a crash loop: give the
        # attempts back, so a platform-store outage cannot fail rows
        # `crash_loop`. A worker that dies mid-job never gets here, so real
        # crashes still count (#90). A cleanup failure (likely the same DB
        # blip) is logged and must not replace the original exception: the
        # rows then keep their bump, which is the conservative direction.
        # A source read that failed in transit is the exception: its rows keep
        # the bump, so a file NODD never serves ends `crash_loop` alone after
        # MAX_ROW_ATTEMPTS takes instead of failing every retry forever.
        transport = isinstance(exc, SourceTransportError)
        kept = exc.row_ids if transport else frozenset()
        try:
            await deps.repo.release_rows(sink.id, [r.id for r in rows if r.id not in kept])
        except Exception:
            logger.exception(
                "cube_append: could not release rows", extra={"cube_sink_id": sink.id}
            )
        try:
            await deps.repo.record_error(sink.id, error_text(exc))
        except Exception:
            logger.exception(
                "cube_append: could not record the error", extra={"cube_sink_id": sink.id}
            )
        if not transport:
            raise
        # Not raised: a Procrastinate retry (3 x 30 s) would be three more takes
        # and bumps within 90 s. The 5-minute cube_kick re-enqueues the sink
        # while rows are pending, so it paces the retries (~6 kicks per row).
        report.in_transit = len(kept)
        logger.warning(
            "cube_append: source reads failed in transit; the next cube_kick retries",
            extra={"cube_sink_id": sink.id, "rows": len(kept), "error": str(exc)},
        )
    return report


async def _append_rows(
    sink: CubeSink,
    config: CubeSinkConfig,
    rows: Sequence[PendingRow],
    deps: AppendDeps,
    report: AppendReport,
) -> None:
    looping = [
        RowOutcome(r.id, "failed", REASON_CRASH_LOOP) for r in rows if r.attempts > MAX_ROW_ATTEMPTS
    ]
    if looping:
        await deps.repo.finish_rows(sink.id, looping)
        logger.error(
            "cube_append: rows failed after too many attempts",
            extra={"cube_sink_id": sink.id, "rows": len(looping)},
        )
    live = [r for r in rows if r.attempts <= MAX_ROW_ATTEMPTS]

    outcomes: dict[int, RowOutcome] = {}
    cutoff = _age_cutoff(config, deps.now())
    if cutoff is not None:
        # Already outside the window: skip before any header is read.
        for row in [r for r in live if r.item_datetime < cutoff]:
            outcomes[row.id] = RowOutcome(row.id, "skipped", REASON_LATE)
        live = [r for r in live if r.item_datetime >= cutoff]
    sources: list[tuple[PendingRow, ResolvedSource]] = []
    #: row id -> the error of a source read that failed in transit
    transient: dict[int, BaseException] = {}
    for row in live:
        try:
            resolved = await deps.resolver.resolve(sink.source_collection_id, row.item_id)
        except SourceUnavailable as exc:
            if is_transport_error(exc):  # DNS failed while vetting the endpoint
                _in_transit(transient, row, exc)
            else:
                outcomes[row.id] = RowOutcome(row.id, exc.status, exc.reason)
            continue
        sources.append((row, resolved))
    parsed = await _parse_all(sources, config, deps, outcomes, transient)
    if transient:
        # End the job before writing anything: the next cube_kick re-reads the
        # whole batch once the source answers again.
        raise SourceTransportError(frozenset(transient), next(iter(transient.values())))

    result: BatchResult | None = None
    if parsed:
        # Re-read the sink just before writing: another run may have recorded
        # the first snapshot since this job started, and resetting it then
        # would drop that run's finished steps. The first-commit compare-and-
        # swap in `_record` still uses the version read at job start (the
        # config this job parsed with).
        fresh = await deps.repo.load_sink(sink.id)
        if fresh is None or not fresh.enabled:
            # As at job start: a disabled sink's rows wait. Nothing crashed,
            # so the take's attempts are given back (finished rows unaffected).
            await deps.repo.release_rows(sink.id, [r.id for r in rows])
            return
        libs = list({source.libs.prefix: source.libs for _, source in sources}.values())
        result, prefixes = await asyncio.to_thread(
            _write,
            deps.storage_for,
            sink,
            libs,
            parsed,
            config,
            deps.now(),
            fresh.last_snapshot_id is None,
        )
        report.snapshot_id, report.committed = result.snapshot_id, result.committed
        needs_record = result.initialised and (
            result.snapshot_id != sink.last_snapshot_id
            or set(prefixes) != set(sink.source_prefixes)
        )
        if needs_record:
            if not await _record(deps.repo, sink, result, prefixes, deps.now()):
                # Lost the first-commit race: the rows wait for the next job,
                # and losing is not a crash, so the attempts are given back.
                await deps.repo.release_rows(sink.id, [r.id for r in rows])
                await deps.enqueue_next(sink.id)
                report.requeued = True
                return
            report.recorded = True
        for row_id, (status, reason) in result.outcomes.items():
            snapshot = result.snapshot_id if status == "appended" else None
            outcomes[row_id] = RowOutcome(row_id, status, reason, snapshot)

    await deps.repo.finish_rows(sink.id, list(outcomes.values()))
    for outcome in [*looping, *outcomes.values()]:
        if outcome.status == "appended":
            report.appended += 1
        elif outcome.status == "skipped":
            report.skipped += 1
        else:
            report.failed += 1
    if result is not None and result.initialised and deps.after_batch is not None:
        await deps.after_batch(sink, config, result)
    if await deps.repo.has_pending(sink.id):
        await deps.enqueue_next(sink.id)
        report.requeued = True


def _in_transit(transient: dict[int, BaseException], row: PendingRow, exc: BaseException) -> None:
    logger.warning(
        "cube_append: source read failed in transit; the next cube_kick retries",
        extra={"item_id": row.item_id, "row_id": row.id, "error": error_text(exc)},
    )
    transient[row.id] = exc


def _age_cutoff(config: CubeSinkConfig, now: dt.datetime) -> dt.datetime | None:
    window = config.window
    if window is None or window.max_age_seconds is None:
        return None
    return now - dt.timedelta(seconds=window.max_age_seconds)


async def _parse_all(
    sources: Sequence[tuple[PendingRow, ResolvedSource]],
    config: CubeSinkConfig,
    deps: AppendDeps,
    outcomes: dict[int, RowOutcome],
    transient: dict[int, BaseException],
) -> list[ParsedStep]:
    if not sources:
        return []
    registry = ObjectStoreRegistry({s.libs.registry_key: s.libs.store for _, s in sources})
    gate = asyncio.Semaphore(PARSE_CONCURRENCY)

    async def one(row: PendingRow, source: ResolvedSource) -> ParsedStep | None:
        async with gate:
            try:
                # HEAD first: a rewrite after it makes reads fail loudly, while
                # a HEAD after the parse could pair new bytes with old offsets.
                last_modified = await deps.resolver.last_modified(source)
                step = await asyncio.to_thread(deps.parse, source.url, registry, config)
            except (FileNotFoundError, NotFoundError):
                outcomes[row.id] = RowOutcome(row.id, "skipped", REASON_SOURCE_MISSING)
                return None
            except LayoutError as exc:
                logger.warning(
                    "cube_append: unsupported layout",
                    extra={"item_id": row.item_id, "detail": str(exc)},
                )
                outcomes[row.id] = RowOutcome(row.id, "skipped", REASON_UNSUPPORTED_LAYOUT)
                return None
            except Exception as exc:
                if is_transport_error(exc):  # not the file's fault: retry the job
                    _in_transit(transient, row, exc)
                    return None
                logger.warning(
                    "cube_append: header parse failed",
                    exc_info=True,
                    extra={"item_id": row.item_id},
                )
                outcomes[row.id] = RowOutcome(row.id, "failed", error_text(exc))
                return None
        value = step_value(step, config.append_dim)
        return ParsedStep(row.id, row.item_id, step, value, last_modified)

    results = await asyncio.gather(*(one(row, source) for row, source in sources))
    return [p for p in results if p is not None]


def _write(
    storage_for: Callable[[CubeSink], Any],
    sink: CubeSink,
    libs: Sequence[SourceLibs],
    parsed: Sequence[ParsedStep],
    config: CubeSinkConfig,
    now: dt.datetime,
    provisional: bool,
) -> tuple[BatchResult, list[str]]:
    """Blocking: resolve the storage (DNS), open, reset unrecorded data, write."""
    repo = open_repository(storage_for(sink), libs, replace_containers=provisional)
    if provisional:
        tip = repo.lookup_branch(BRANCH)
        if read_state(repo.readonly_session(BRANCH), config.append_dim).initialised:
            logger.warning(
                "cube repository holds unrecorded data; resetting it before writing",
                extra={"snapshot_id": tip},
            )
            reset_to_root(repo, from_snapshot_id=tip)
    result = write_batch(repo, parsed, config, now)
    return result, sorted(repo.config.virtual_chunk_containers or {})


async def _record(
    repo: CubeRepo,
    sink: CubeSink,
    result: BatchResult,
    prefixes: Sequence[str],
    now: dt.datetime,
) -> bool:
    """Record the tip. The first commit is conditional on the version read;
    a loss leaves the repository provisional (False)."""
    first = sink.version if sink.last_snapshot_id is None else None
    kw = {"snapshot_id": result.snapshot_id, "appended_at": now, "source_prefixes": prefixes}
    if await repo.record_commit(sink.id, first_commit_version=first, **kw):
        return True
    fresh = await repo.load_sink(sink.id)
    if fresh is None or fresh.last_snapshot_id is None:
        logger.warning(
            "cube_append: the sink changed or went away during its first append;"
            " the repository stays provisional",
            extra={"cube_sink_id": sink.id, "snapshot_id": result.snapshot_id},
        )
        return False
    # Another run recorded first (a double run); record over it with this tip.
    return await repo.record_commit(sink.id, first_commit_version=None, **kw)
