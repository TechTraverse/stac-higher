"""run_cube_append: claim, parse off the loop, commit, record, ledger, re-enqueue."""

from __future__ import annotations

import asyncio
import datetime as dt
import threading
from dataclasses import dataclass, field
from pathlib import Path

import icechunk as ic
import obstore
import pytest
import xarray as xr

import pipeline.cubes.write as write_mod
from _cube_fake import FakeCubeRepo, FakeSink
from _cube_sources import (
    GOES_CONFIG,
    SOURCE_LAST_MODIFIED,
    as_ns,
    local_libs,
    scan,
    write_goes_file,
)
from pipeline.cubes.append import (
    BATCH_LIMIT,
    MAX_ROW_ATTEMPTS,
    PARSE_CONCURRENCY,
    AppendDeps,
    run_cube_append,
)
from pipeline.cubes.icerepo import BRANCH
from pipeline.cubes.repo import LedgerEntry
from pipeline.cubes.resolve import ResolvedSource, SourceResolver, SourceUnavailable
from pipeline.cubes.source import SourceLibs
from pipeline.cubes.steps import parse_header

SINK = "s1"


@dataclass
class FakeResolver(SourceResolver):
    libs: SourceLibs
    #: item_id -> object key, or the SourceUnavailable to raise
    items: dict[str, str | SourceUnavailable] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def resolve(self, source_collection_id: str, item_id: str) -> ResolvedSource:
        found = self.items.get(item_id)
        if found is None:
            raise SourceUnavailable("skipped", "source_missing")
        if isinstance(found, SourceUnavailable):
            raise found
        return ResolvedSource(self.libs, found)

    async def last_modified(self, source: ResolvedSource) -> dt.datetime:
        self.calls.append(("head", source.key))
        return (await obstore.head_async(source.libs.store, source.key))["last_modified"]


@dataclass
class Harness:
    root: Path
    repo: FakeCubeRepo
    resolver: FakeResolver
    storage: object
    enqueued: list[str] = field(default_factory=list)
    hooked: list = field(default_factory=list)
    now: dt.datetime = field(default_factory=lambda: scan(1000))

    def deps(self, **over) -> AppendDeps:
        async def enqueue_next(cube_sink_id: str) -> None:
            self.enqueued.append(cube_sink_id)

        base = {
            "repo": self.repo,
            "resolver": self.resolver,
            "storage_for": lambda sink: self.storage,
            "enqueue_next": enqueue_next,
            "now": lambda: self.now,
        }
        return AppendDeps(**{**base, **over})

    async def pend(self, *ns: int, **file_kw) -> None:
        """A source file and a pending ledger row per scan n (item ``i{n}``)."""
        for n in ns:
            write_goes_file(self.root / f"{n}.nc", when=scan(n), value=float(n), **file_kw)
            self.resolver.items[f"i{n}"] = f"{n}.nc"
        await self.repo.record_appends([LedgerEntry(SINK, f"i{n}", scan(n)) for n in ns])

    def ledger(self) -> dict[str, tuple]:
        return {r.item_id: (r.status, r.reason) for r in self.repo.rows(SINK)}

    def sink(self) -> FakeSink:
        return self.repo.sinks[0]

    def times(self) -> list:
        repo = ic.Repository.open(
            self.storage,
            authorize_virtual_chunk_access=ic.containers_credentials(
                {self.resolver.libs.prefix: self.resolver.libs.credentials}
            ),
        )
        ds = xr.open_zarr(repo.readonly_session(BRANCH).store, consolidated=False, zarr_format=3)
        return list(ds["t"].values)


@pytest.fixture
def h(tmp_path) -> Harness:
    libs = local_libs(tmp_path)
    repo = FakeCubeRepo(sinks=[FakeSink(SINK, "src", "cube", config=dict(GOES_CONFIG))])
    return Harness(tmp_path, repo, FakeResolver(libs), ic.in_memory_storage())


def ns(*scans: int) -> list:
    return [as_ns(scan(n)) for n in scans]


async def test_pending_rows_become_one_recorded_commit(h):
    await h.pend(0, 1, 2)
    report = await run_cube_append(SINK, h.deps())
    assert (report.taken, report.appended, report.committed, report.recorded) == (3, 3, True, True)
    assert h.times() == ns(0, 1, 2)
    assert h.ledger() == {f"i{n}": ("appended", None) for n in (0, 1, 2)}
    assert {r.snapshot_id for r in h.repo.rows(SINK)} == {h.sink().last_snapshot_id}
    assert h.sink().source_prefixes == (h.resolver.libs.prefix,)
    assert h.enqueued == []


async def test_the_constants_are_the_specs():
    assert (BATCH_LIMIT, PARSE_CONCURRENCY, MAX_ROW_ATTEMPTS) == (50, 4, 6)


async def test_a_first_commit_that_loses_to_an_app_write_stays_provisional(h):
    await h.pend(0, 1)

    def app_patch(repo: FakeCubeRepo) -> None:  # a PUT/PATCH between commit and record
        repo.sinks[0].version = "v2"

    h.repo.before_record = app_patch
    lost = await run_cube_append(SINK, h.deps())
    assert (lost.committed, lost.recorded, lost.requeued) == (True, False, True)
    assert h.sink().last_snapshot_id is None
    assert set(h.ledger().values()) == {("pending", None)}
    assert h.enqueued == [SINK]

    won = await run_cube_append(SINK, h.deps())  # the next job rebuilds under v2
    assert won.recorded
    # Rewritten after the reset, so not duplicates:
    assert h.ledger() == {"i0": ("appended", None), "i1": ("appended", None)}
    assert h.times() == ns(0, 1)


async def test_a_double_run_during_the_first_commit_keeps_both_runs_steps(h, monkeypatch):
    # B loads the sink before any snapshot is recorded; A then runs to the end
    # (commits S1, records it, finishes i0/i1); B's claim returns the next rows.
    await h.pend(0, 1, 2, 3)
    real_take = h.repo.take_pending
    a_ran = False

    async def take_after_a(cube_sink_id, limit):
        nonlocal a_ran
        if not a_ran:
            a_ran = True
            first = await run_cube_append(SINK, h.deps(batch_limit=2))  # run A
            assert first.recorded and h.sink().last_snapshot_id is not None
        return await real_take(cube_sink_id, limit)

    monkeypatch.setattr(h.repo, "take_pending", take_after_a)
    second = await run_cube_append(SINK, h.deps(batch_limit=2))  # run B
    assert a_ran and second.recorded
    assert h.times() == ns(0, 1, 2, 3)
    assert set(h.ledger().values()) == {("appended", None)}
    cube = set(h.times())
    assert {as_ns(r.item_datetime) for r in h.repo.rows(SINK) if r.status == "appended"} <= cube
    assert h.sink().last_snapshot_id == second.snapshot_id


async def test_a_sink_disabled_or_deleted_before_the_write_ends_quietly(h):
    for gone in (False, True):
        await h.pend(0)

        def vanish(url, registry, config, gone=gone):
            if gone:
                h.repo.sinks.clear()
            else:
                h.sink().enabled = False
            return parse_header(url, registry, config)

        report = await run_cube_append(SINK, h.deps(parse=vanish))
        assert (report.taken, report.committed, report.recorded) == (1, False, False)
        if not gone:
            assert h.ledger() == {"i0": ("pending", None)}
            assert h.sink().last_error is None
            assert h.sink().last_snapshot_id is None
            h.sink().enabled = True
    assert h.enqueued == []


async def test_unrecorded_data_from_a_crash_is_reset_before_writing(h):
    await h.pend(0)
    h.repo.record_commit_error = RuntimeError("worker killed")
    with pytest.raises(RuntimeError):
        await run_cube_append(SINK, h.deps())
    assert h.ledger() == {"i0": ("pending", None)}
    await h.pend(1)
    await run_cube_append(SINK, h.deps())
    assert h.ledger() == {"i0": ("appended", None), "i1": ("appended", None)}
    assert h.times() == ns(0, 1)


async def test_redone_steps_of_a_recorded_cube_are_duplicates(h):
    await h.pend(0, 1)
    h.repo.finish_error = RuntimeError("worker killed")  # crash after the record
    with pytest.raises(RuntimeError):
        await run_cube_append(SINK, h.deps())
    recorded = h.sink().last_snapshot_id
    report = await run_cube_append(SINK, h.deps())
    assert not report.committed
    assert h.ledger() == {"i0": ("appended", "duplicate"), "i1": ("appended", "duplicate")}
    assert {r.snapshot_id for r in h.repo.rows(SINK)} == {recorded}
    assert h.times() == ns(0, 1)


async def test_source_outcomes_land_on_the_ledger(h):
    await h.pend(0)
    h.resolver.items["nc"] = SourceUnavailable("skipped", "no_source_connection")
    h.resolver.items["eg"] = SourceUnavailable("failed", "EgressBlocked: egress to x is blocked")
    h.resolver.items["gone"] = "deleted.nc"  # resolves, but the object is missing
    await h.repo.record_appends(
        [LedgerEntry(SINK, i, scan(5)) for i in ("nc", "eg", "gone", "nohref")]
    )
    report = await run_cube_append(SINK, h.deps())
    assert h.ledger() == {
        "i0": ("appended", None),
        "nc": ("skipped", "no_source_connection"),
        "eg": ("failed", "EgressBlocked: egress to x is blocked"),
        "gone": ("skipped", "source_missing"),
        "nohref": ("skipped", "source_missing"),
    }
    assert (report.appended, report.skipped, report.failed) == (1, 3, 1)


async def test_unsupported_layout_and_late(h):
    await h.pend(5)
    await run_cube_append(SINK, h.deps())
    await h.pend(3)  # before the tip: late
    write_goes_file(h.root / "6.nc", when=scan(6), variables=("CMI",))  # DQF missing
    h.resolver.items["i6"] = "6.nc"
    await h.repo.record_appends([LedgerEntry(SINK, "i6", scan(6))])
    await run_cube_append(SINK, h.deps())
    assert h.ledger()["i3"] == ("skipped", "late")
    assert h.ledger()["i6"] == ("skipped", "unsupported_layout")
    assert h.times() == ns(5)


async def test_one_bad_file_fails_only_its_row(h):
    await h.pend(0, 1, 2)
    real = parse_header

    def flaky(url, registry, config):
        if url.endswith("/1.nc"):
            raise OSError("connection reset by peer")
        return real(url, registry, config)

    await run_cube_append(SINK, h.deps(parse=flaky))
    assert h.ledger() == {
        "i0": ("appended", None),
        "i1": ("failed", "OSError: connection reset by peer"),
        "i2": ("appended", None),
    }
    assert h.times() == ns(0, 2)


async def test_a_row_past_the_attempt_cap_fails_crash_loop(h):
    await h.pend(0, 1)
    h.repo.ledger[(SINK, "i0")].attempts = MAX_ROW_ATTEMPTS  # this take makes it 7
    await run_cube_append(SINK, h.deps())
    assert h.ledger() == {"i0": ("failed", "crash_loop"), "i1": ("appended", None)}


async def test_the_batch_limit_re_enqueues_the_rest(h):
    await h.pend(0, 1, 2)
    first = await run_cube_append(SINK, h.deps(batch_limit=2))
    assert (first.taken, first.requeued, h.enqueued) == (2, True, [SINK])
    second = await run_cube_append(SINK, h.deps(batch_limit=2))
    assert (second.taken, second.requeued, h.enqueued) == (1, False, [SINK])
    assert h.times() == ns(0, 1, 2)


async def test_a_backlog_larger_than_the_window_converges(h):
    h.sink().config = {**GOES_CONFIG, "window": {"max_steps": 2}}
    await h.pend(0, 1, 2, 3, 4)
    runs = 0
    while True:
        runs += 1
        report = await run_cube_append(SINK, h.deps(batch_limit=2))
        if not report.requeued:
            break
    assert runs == 3
    assert h.times() == ns(3, 4)
    assert all(status == "appended" for status, _ in h.ledger().values())


async def test_the_window_holds_across_runs(h):
    h.sink().config = {**GOES_CONFIG, "window": {"max_steps": 2}}
    await h.pend(0, 1, 2)
    await run_cube_append(SINK, h.deps())
    await h.pend(3)
    await run_cube_append(SINK, h.deps())
    assert h.times() == ns(2, 3)


async def test_parsing_runs_off_the_loop_at_most_four_at_a_time(h):
    await h.pend(*range(8))
    loop_thread = threading.get_ident()
    seen: list[int] = []
    live, peak = 0, 0
    guard = threading.Lock()

    def tracked(url, registry, config):
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
            seen.append(threading.get_ident())
        try:
            return parse_header(url, registry, config)
        finally:
            with guard:
                live -= 1

    await run_cube_append(SINK, h.deps(parse=tracked))
    assert len(seen) == 8 and loop_thread not in seen
    assert peak <= PARSE_CONCURRENCY


async def test_the_storage_is_resolved_off_the_loop(h):
    # cube_storage resolves the platform endpoint (DNS): never on the event loop
    await h.pend(0)
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def storage_for(sink):
        seen.append(threading.get_ident())
        return h.storage

    await run_cube_append(SINK, h.deps(storage_for=storage_for))
    assert len(seen) == 1 and loop_thread not in seen


async def test_the_head_comes_before_the_parse_and_its_time_is_passed(h, monkeypatch):
    await h.pend(0)
    order: list[str] = []
    passed: list[dt.datetime] = []
    real_write = write_mod.write_step

    def parse(url, registry, config):
        order.append("parse")
        return parse_header(url, registry, config)

    def capture(session, parsed, append_dim):
        passed.append(parsed.last_modified)
        real_write(session, parsed, append_dim)

    monkeypatch.setattr(write_mod, "write_step", capture)
    original_head = h.resolver.last_modified

    async def head(source):
        order.append("head")
        return await original_head(source)

    h.resolver.last_modified = head  # type: ignore[method-assign]
    await run_cube_append(SINK, h.deps(parse=parse))
    assert order == ["head", "parse"]
    assert passed == [SOURCE_LAST_MODIFIED]


async def test_a_disabled_or_missing_sink_does_nothing(h):
    await h.pend(0)
    h.sink().enabled = False
    assert (await run_cube_append(SINK, h.deps())).taken == 0
    assert (await run_cube_append("gone", h.deps())).taken == 0
    assert h.ledger() == {"i0": ("pending", None)}


async def test_a_sink_deleted_mid_job_ends_quietly(h):
    await h.pend(0)
    h.repo.before_record = lambda repo: repo.sinks.clear()
    report = await run_cube_append(SINK, h.deps())
    assert (report.recorded, report.requeued) == (False, True)  # the next job sees no sink


async def test_an_invalid_config_records_an_error_and_leaves_rows(h):
    h.sink().config = {**GOES_CONFIG, "parser": "grib"}
    await h.pend(0)
    report = await run_cube_append(SINK, h.deps())
    assert report.taken == 0
    assert h.sink().last_error.startswith("invalid config:")
    assert h.ledger() == {"i0": ("pending", None)}


async def test_after_batch_runs_after_every_batch_that_reached_the_cube(h):
    calls = []

    async def hook(sink, config, result):
        calls.append((sink.id, config.asset_key, result.snapshot_id))

    await h.pend(0)
    await run_cube_append(SINK, h.deps(after_batch=hook))
    first = h.sink().last_snapshot_id
    assert calls == [(SINK, "cube", first)]
    await h.pend(-1)  # late: nothing committed, and the hook re-confirms the tip
    await run_cube_append(SINK, h.deps(after_batch=hook))
    assert calls == [(SINK, "cube", first), (SINK, "cube", first)]


async def test_the_asset_hook_catches_up_after_a_crash(h):
    tips: list[str] = []

    async def hook(sink, config, result):
        tips.append(result.snapshot_id)

    await h.pend(0)
    await run_cube_append(SINK, h.deps(after_batch=hook))
    await h.pend(1)
    h.repo.finish_error = RuntimeError("worker killed")  # recorded, hook not reached
    with pytest.raises(RuntimeError):
        await run_cube_append(SINK, h.deps(after_batch=hook))
    tip = h.sink().last_snapshot_id
    assert tips[-1] != tip
    await run_cube_append(SINK, h.deps(after_batch=hook))  # the redo: duplicates only
    assert tips[-1] == tip


async def test_a_storage_outage_hands_the_attempts_back(h):
    await h.pend(0)

    def down(sink):
        raise OSError("platform store unreachable")

    for _ in range(MAX_ROW_ATTEMPTS + 2):
        with pytest.raises(OSError):
            await run_cube_append(SINK, h.deps(storage_for=down))
    row = h.repo.rows(SINK)[0]
    assert (row.status, row.attempts) == ("pending", 0)  # never crash_loop
    assert h.sink().last_error == "OSError: platform store unreachable"
    await run_cube_append(SINK, h.deps())  # the store is back
    assert h.ledger() == {"i0": ("appended", None)}
    assert h.sink().last_error is None


async def test_a_failed_release_keeps_the_original_exception(h, monkeypatch):
    await h.pend(0)

    async def db_blip(cube_sink_id, row_ids):
        raise ConnectionError("database unreachable")

    def down(sink):
        raise OSError("platform store unreachable")

    monkeypatch.setattr(h.repo, "release_rows", db_blip)
    with pytest.raises(OSError, match="platform store"):
        await run_cube_append(SINK, h.deps(storage_for=down))
    assert h.repo.rows(SINK)[0].attempts == 1  # kept: the conservative direction
    assert h.sink().last_error == "OSError: platform store unreachable"  # still recorded


async def test_a_storage_error_in_the_write_phase_fails_the_job_not_the_rows(h, monkeypatch):
    await h.pend(0)
    await run_cube_append(SINK, h.deps())  # one committed step
    tip = h.sink().last_snapshot_id
    await h.pend(1, 2)
    real_write = write_mod.write_step

    def silo_down(session, parsed, append_dim):
        raise ic.StorageError("silo is down")

    monkeypatch.setattr(write_mod, "write_step", silo_down)
    for _ in range(MAX_ROW_ATTEMPTS + 2):
        with pytest.raises(ic.StorageError):
            await run_cube_append(SINK, h.deps())
    pending = [r for r in h.repo.rows(SINK) if r.item_id != "i0"]
    assert [(r.status, r.attempts) for r in pending] == [("pending", 0)] * 2  # never crash_loop
    assert "StorageError" in h.sink().last_error
    assert h.sink().last_snapshot_id == tip

    monkeypatch.setattr(write_mod, "write_step", real_write)  # the store is back
    await run_cube_append(SINK, h.deps())
    assert h.ledger() == {f"i{n}": ("appended", None) for n in (0, 1, 2)}
    assert h.times() == ns(0, 1, 2)
    assert h.sink().last_error is None


async def test_rows_older_than_max_age_are_skipped_without_parsing(h):
    h.sink().config = {**GOES_CONFIG, "window": {"max_age": "10m"}}
    h.now = scan(10)
    await h.pend(0, 9)
    parsed: list[str] = []

    def tracked(url, registry, config):
        parsed.append(url.rsplit("/", 1)[1])
        return parse_header(url, registry, config)

    await run_cube_append(SINK, h.deps(parse=tracked))
    assert h.ledger() == {"i0": ("skipped", "late"), "i9": ("appended", None)}
    assert parsed == ["9.nc"]
    assert h.times() == ns(9)


async def test_parse_does_not_block_the_event_loop(h):
    await h.pend(0)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    try:
        await run_cube_append(SINK, h.deps())
    finally:
        task.cancel()
    assert ticks > 1
