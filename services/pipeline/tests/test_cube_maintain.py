"""cube_maintain_sink: age trim, expiry + GC, ledger prune, size readout (spec §10, I-143)."""

from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path

import icechunk as ic
import pytest
import xarray as xr

from _cube_fake import FakeCubeRepo, FakeLedgerRow, FakeSink
from _cube_sources import (
    GOES_CONFIG,
    SOURCE_LAST_MODIFIED,
    as_ns,
    local_libs,
    registry_for,
    scan,
    write_goes_file,
)
from pipeline.cubes.collection import published_snapshot_of
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, open_repository, reset_to_root
from pipeline.cubes.maintain import (
    ATTENTION_REPO_SIZE,
    KINDS,
    LEDGER_RETENTION_DAYS,
    MaintainDeps,
    MeasureDeps,
    expiry_cutoff,
    maintain_repository,
    run_cube_maintain,
    run_cube_measure,
    size_by_kind,
)
from pipeline.cubes.steps import parse_header, step_value
from pipeline.cubes.write import BatchResult, ParsedStep, write_batch

#: Icechunk stamps snapshots and objects with the wall clock, so expiry tests
#: run "two hours from now" to put every snapshot past the 1 h retention.
LATER = dt.datetime.now(dt.UTC) + dt.timedelta(hours=2)


class Cube:
    """A real Icechunk cube on local disk plus a fake sink row."""

    def __init__(self, tmp: Path, window: dict | None) -> None:
        self.src = local_libs(tmp / "src")
        self.dir = tmp / "repo"
        raw = {**GOES_CONFIG, **({"window": window} if window else {})}
        self.config = parse_cube_sink_config(raw)
        self.repo = FakeCubeRepo(sinks=[FakeSink("s1", "src", "cube1", config=raw)])
        self.published: list[tuple[str, BatchResult]] = []
        self.listed = 0
        self.measure_queue: list[str] = []
        self.readouts = 0
        self.publish_outcome: str | None = "published"

    def storage(self) -> ic.Storage:
        return ic.local_filesystem_storage(str(self.dir))

    def append(self, *ns: int, now: dt.datetime | None = None) -> str:
        """Append scans ``ns`` in one commit and record it on the sink."""
        repo = open_repository(self.storage(), [self.src], replace_containers=False)
        parsed = []
        for n in ns:
            name = f"f{n}.nc"
            write_goes_file(self.src.prefix.removeprefix("file://") + name, when=scan(n))
            step = parse_header(self.src.url(name), registry_for(self.src), self.config)
            parsed.append(ParsedStep(n, f"i{n}", step, step_value(step, "t"), SOURCE_LAST_MODIFIED))
        result = write_batch(repo, parsed, self.config, now or scan(100))
        self.sink.last_snapshot_id = result.snapshot_id
        return result.snapshot_id

    @property
    def sink(self) -> FakeSink:
        return self.repo.sinks[0]

    def tip(self) -> str:
        return ic.Repository.open(self.storage()).lookup_branch(BRANCH)

    def commits(self) -> int:
        return len(list(ic.Repository.open(self.storage()).ancestry(branch=BRANCH))) - 1

    def snapshot_files(self) -> int:
        return len(list((self.dir / "snapshots").iterdir()))

    def times(self) -> list:
        repo = open_repository(self.storage(), [self.src], replace_containers=False)
        ds = xr.open_zarr(repo.readonly_session(BRANCH).store, consolidated=False, zarr_format=3)
        return list(ds["t"].values)

    def pixels(self) -> list[float]:
        repo = open_repository(self.storage(), [self.src], replace_containers=False)
        ds = xr.open_zarr(repo.readonly_session(BRANCH).store, consolidated=False, zarr_format=3)
        return [float(v) for v in ds["CMI"].isel(x=0, y=0).values]

    def list_objects(self, sink) -> list[tuple[str, int]]:
        self.listed += 1
        return [
            (p.relative_to(self.dir).as_posix(), p.stat().st_size)
            for p in sorted(self.dir.rglob("*"))
            if p.is_file()
        ]

    async def publish(self, sink, config, result: BatchResult) -> str:
        self.published.append((sink.id, result))
        return self.publish_outcome

    async def enqueue_measure(self, cube_sink_id: str) -> None:
        self.measure_queue.append(cube_sink_id)

    def deps(self, *, now: dt.datetime = LATER) -> MaintainDeps:
        return MaintainDeps(
            repo=self.repo,
            storage_for=lambda sink: self.storage(),
            retention_seconds=3600,
            after_batch=self.publish,
            enqueue_measure=self.enqueue_measure,
            now=lambda: now,
        )

    def measure_deps(self, *, warn_bytes: int = 1024**3) -> MeasureDeps:
        return MeasureDeps(
            repo=self.repo,
            list_objects=self.list_objects,
            warn_bytes=warn_bytes,
            now=self._measure_clock,
        )

    def _measure_clock(self) -> dt.datetime:
        """Readouts start after every run in these tests, one second apart."""
        self.readouts += 1
        return LATER + dt.timedelta(days=1, seconds=self.readouts)

    async def run(self, *, warn_bytes: int = 1024**3, **kw) -> dict | None:
        """What production does: the maintenance run, then the size readout
        it enqueued. Returns the sink's merged ``last_maintenance``."""
        if await run_cube_maintain("s1", self.deps(**kw)) is None:
            return None
        while self.measure_queue:
            deps = self.measure_deps(warn_bytes=warn_bytes)
            await run_cube_measure(self.measure_queue.pop(0), deps)
        return self.sink.last_maintenance


def test_size_by_kind_buckets_by_top_level_directory():
    sizes = size_by_kind(
        [
            ("transactions/A", 10),
            ("transactions/B", 5),
            ("overwritten/X", 7),
            ("manifests/M", 3),
            ("snapshots/S", 2),
            ("chunks/C", 1),
            ("repo", 100),
            ("config.yaml", 4),
            ("transactions", 9),  # a top-level object named like a kind is "other"
        ]
    )
    assert sizes["transactions"] == {"objects": 2, "bytes": 15}
    assert sizes["overwritten"] == {"objects": 1, "bytes": 7}
    assert sizes["other"] == {"objects": 3, "bytes": 113}
    assert set(sizes) == {*KINDS, "other"}


async def test_a_sink_without_a_window_is_never_expired_or_garbage_collected(tmp_path):
    cube = Cube(tmp_path, window=None)
    for n in range(4):
        cube.append(n)
    before = cube.snapshot_files()

    summary = await cube.run()

    assert cube.snapshot_files() == before
    assert summary["window"] is False
    assert (summary["expired_snapshots"], summary["gc"], summary["trimmed"]) == (None, None, 0)
    assert summary["sizes"]["snapshots"]["objects"] == before
    assert summary["status"] == "ok"
    assert cube.sink.last_maintenance == summary
    assert cube.sink.last_maintained_at == LATER


async def test_a_windowed_sink_expires_and_collects_old_snapshots(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 3})
    for n in range(5):
        cube.append(n)
    before = cube.snapshot_files()

    summary = await cube.run()

    assert summary["expired_snapshots"] >= 4
    assert summary["gc"]["snapshots_deleted"] == before - 2  # the root and the tip stay
    assert cube.snapshot_files() == 2
    assert set(summary["durations_ms"]) >= {"expire", "gc", "list"}
    # The cube is still whole: its window reads, through the source.
    assert cube.times() == [as_ns(scan(n)) for n in (2, 3, 4)]
    assert cube.pixels() == [0.0, 0.0, 0.0]


async def test_gc_collects_the_snapshots_a_provisional_reset_orphaned(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 3})
    cube.append(0)
    cube.append(1)
    repo = open_repository(cube.storage(), [cube.src], replace_containers=False)
    reset_to_root(repo, from_snapshot_id=repo.lookup_branch(BRANCH))
    cube.sink.last_snapshot_id = None  # provisional, as Z-4 leaves it

    summary = await cube.run()

    assert summary["gc"]["snapshots_deleted"] == 2
    assert cube.snapshot_files() == 1  # the root
    assert cube.published == []  # nothing recorded, nothing to publish


async def test_an_age_trim_commits_once_and_records_the_snapshot(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, 4, 5, now=scan(5))
    before = cube.commits()
    now = scan(5) + dt.timedelta(minutes=20)  # cutoff = scan(3): scans 0-2 age out

    summary = await cube.run(now=now)

    assert summary["trimmed"] == 3
    assert cube.commits() == before + 1
    assert cube.sink.last_snapshot_id == cube.tip()
    assert cube.times() == [as_ns(scan(n)) for n in (3, 4, 5)]
    again = await cube.run(now=now)
    assert again["trimmed"] == 0
    assert cube.commits() == before + 1  # nothing left to trim: no empty commit


async def test_a_trim_keeps_the_snapshot_readers_were_on(tmp_path):
    # Icechunk ages snapshots by write time; the old tip must outlive the
    # trim that replaced it, or a reader still on it loses its manifests.
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, now=scan(1))
    old_tip = cube.append(2, 3, now=scan(3))
    repo = open_repository(cube.storage(), [cube.src], replace_containers=False)
    reader = xr.open_zarr(
        repo.readonly_session(snapshot_id=old_tip).store, consolidated=False, zarr_format=3
    )
    time.sleep(1.1)  # both appends are now older than the 1 s retention
    deps = cube.deps(now=dt.datetime.now(dt.UTC))
    deps.retention_seconds = 1

    summary = await run_cube_maintain("s1", deps)

    assert summary["trimmed"] == 4  # the 2026-10-03 scans are all past max_age
    assert summary["gc"]["snapshots_deleted"] >= 1  # older history still goes
    assert [float(v) for v in reader["CMI"].isel(x=0, y=0).values] == [0.0] * 4


async def test_no_age_trim_while_an_append_is_pending(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, now=scan(2))
    cube.repo.ledger[("s1", "next")] = FakeLedgerRow("s1", "next", scan(9), "pending", None)
    before = cube.commits()

    summary = await cube.run(now=scan(9))

    assert summary["trimmed"] == 0
    assert cube.commits() == before


async def test_no_age_trim_on_a_provisional_repository(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, now=scan(2))
    cube.sink.last_snapshot_id = None
    before = cube.commits()

    summary = await cube.run(now=scan(9))

    assert summary["trimmed"] == 0
    assert cube.commits() == before


async def test_no_age_trim_when_the_tip_is_not_the_recorded_snapshot(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    recorded = cube.append(0, 1, now=scan(1))
    cube.append(2, now=scan(2))  # committed, then the job died before recording
    cube.sink.last_snapshot_id = recorded
    before = cube.commits()

    summary = await cube.run(now=scan(9))

    assert summary["trimmed"] == 0
    assert cube.commits() == before
    assert cube.published == []  # the unrecorded tip is the next append's to record
    assert summary["attention"] == ["unrecorded_tip"]
    assert summary["healed"] is False


async def test_a_trim_whose_record_was_lost_heals_on_the_next_run(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, now=scan(3))
    now = scan(3) + dt.timedelta(minutes=25)
    real = cube.repo.record_snapshot
    calls = []

    async def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("db blip")
        return await real(*a, **kw)

    cube.repo.record_snapshot = flaky
    with pytest.raises(ConnectionError):
        await cube.run(now=now)
    assert cube.sink.last_snapshot_id != cube.tip()  # trim committed, record lost
    before = cube.commits()

    summary = await cube.run(now=now)

    assert summary["healed"] is True
    assert cube.sink.last_snapshot_id == cube.tip()
    assert summary["published"] is True
    assert cube.published[-1][1].snapshot_id == cube.tip()
    assert summary["status"] == "ok"
    assert cube.commits() == before  # nothing left to trim


async def test_two_lost_trim_records_in_a_row_still_heal(tmp_path):
    # Run 1 trims and loses its record; run 2 heals that trim, trims again,
    # and loses its record too. Run 3 must heal the two-commit chain, or a
    # stopped source stays unrecorded until an append that never comes.
    cube = Cube(tmp_path, window={"max_age": "30m"})
    recorded = cube.append(0, 1, 2, 3, 4, 5, now=scan(5))
    real = cube.repo.record_snapshot
    lose = [True, True]

    async def flaky(*a, **kw):
        if lose and lose.pop(0):
            raise ConnectionError("db blip")
        return await real(*a, **kw)

    cube.repo.record_snapshot = flaky
    with pytest.raises(ConnectionError):
        await cube.run(now=scan(5) + dt.timedelta(minutes=20))  # trims 0-2
    with pytest.raises(ConnectionError):
        await cube.run(now=scan(5) + dt.timedelta(minutes=25))  # heals, trims 3
    assert cube.sink.last_snapshot_id == recorded
    before = cube.commits()

    summary = await cube.run(now=scan(5) + dt.timedelta(minutes=25))

    assert summary["healed"] is True
    assert cube.sink.last_snapshot_id == cube.tip()
    assert summary["published"] is True
    assert summary["status"] == "ok"
    assert cube.commits() == before
    assert cube.times() == [as_ns(scan(4)), as_ns(scan(5))]


async def test_only_a_commit_tagged_as_maintenance_is_healed(tmp_path):
    # The heal reads the snapshot metadata, not the message: a commit that
    # merely says "(maintenance)" is someone else's tip.
    cube = Cube(tmp_path, window={"max_age": "30m"})
    recorded = cube.append(0, 1, now=scan(1))
    session = ic.Repository.open(cube.storage()).writable_session(BRANCH)
    session.commit("trim 1 steps (maintenance)", allow_empty=True)

    summary = await cube.run(now=scan(1) + dt.timedelta(minutes=5))

    assert summary["healed"] is False
    assert cube.sink.last_snapshot_id == recorded
    assert summary["attention"] == ["unrecorded_tip"]


async def test_expiry_never_collects_the_recorded_snapshot(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 10})
    recorded = cube.append(0)
    cube.append(1)  # an unrecorded append tip: not ours to heal
    cube.sink.last_snapshot_id = recorded

    summary = await cube.run(now=LATER)

    repo = open_repository(cube.storage(), [cube.src], replace_containers=False)
    old = xr.open_zarr(
        repo.readonly_session(snapshot_id=recorded).store, consolidated=False, zarr_format=3
    )
    assert len(old["t"].values) == 1
    assert "unrecorded_tip" in summary["attention"]
    assert summary["status"] == "attention"


async def test_expiry_spares_the_snapshot_the_collection_asset_names(tmp_path):
    # A republish that keeps failing leaves the asset on an older snapshot
    # than the recorded one; readers open that one, so it must survive.
    cube = Cube(tmp_path, window={"max_steps": 10})
    cube.append(0)  # older than the named snapshot: still expires
    named = cube.append(1)
    cube.append(2)
    cube.append(3)  # recorded tip (append sets last_snapshot_id)
    deps = cube.deps()
    deps.published_snapshot = _published(named)

    summary = await run_cube_maintain("s1", deps)

    assert summary["expired_snapshots"] >= 1
    repo = open_repository(cube.storage(), [cube.src], replace_containers=False)
    old = xr.open_zarr(
        repo.readonly_session(snapshot_id=named).store, consolidated=False, zarr_format=3
    )
    assert len(old["t"].values) == 2


async def test_without_the_published_seam_an_older_named_snapshot_is_expired(tmp_path):
    # The counterpart of the test above: proves the seam is what saves it.
    cube = Cube(tmp_path, window={"max_steps": 10})
    named = cube.append(0)
    cube.append(1)
    cube.append(2)

    await cube.run()

    repo = open_repository(cube.storage(), [cube.src], replace_containers=False)
    with pytest.raises(Exception):  # noqa: B017 - icechunk's error type varies by version
        repo.readonly_session(snapshot_id=named)


async def test_a_published_snapshot_off_main_skips_expiry_and_gc(tmp_path):
    # GC keeps only what a ref reaches, so a cutoff can't save a snapshot that
    # is off main: its manifests predate it. The run must not collect at all.
    cube = Cube(tmp_path, window={"max_steps": 10})
    base = cube.append(0)
    published = cube.append(1)
    repo = ic.Repository.open(cube.storage())
    repo.reset_branch(BRANCH, base)  # main moves off the published snapshot
    cube.append(2)  # the recorded tip, on the new line
    deps = cube.deps()
    deps.published_snapshot = _published(published)

    summary = await run_cube_maintain("s1", deps)

    assert summary["expired_snapshots"] is None and summary["gc"] is None
    assert "expiry_skipped" in summary["attention"]
    assert summary["status"] == "attention"
    reader = open_repository(cube.storage(), [cube.src], replace_containers=False)
    old = xr.open_zarr(
        reader.readonly_session(snapshot_id=published).store, consolidated=False, zarr_format=3
    )
    assert len(old["t"].values) == 2


async def test_a_malformed_published_version_skips_expiry_not_the_run(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 10})
    cube.append(0)
    cube.append(1)
    deps = cube.deps()
    deps.published_snapshot = _published("not-a-snapshot-id")

    summary = await run_cube_maintain("s1", deps)

    assert summary["expired_snapshots"] is None
    assert summary["attention"] == ["expiry_skipped"]
    assert summary["published"] is True  # the republish still ran


async def test_expiry_skipped_without_a_repository_is_still_attention(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 10})  # no append: no repository

    async def broken(sink):
        raise ConnectionError("pool exhausted")

    deps = cube.deps()
    deps.published_snapshot = broken

    summary = await run_cube_maintain("s1", deps)

    assert summary["repository"] is False
    assert summary["status"] == "attention"


def test_a_spared_id_that_no_longer_exists_is_ignored(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 10})
    for n in range(3):
        cube.append(n)
    repo = ic.Repository.open(cube.storage())
    tip = repo.lookup_branch(BRANCH)
    plain = expiry_cutoff(repo, LATER, 3600, recorded_snapshot_id=tip)

    cutoff = expiry_cutoff(
        repo, LATER, 3600, recorded_snapshot_id=tip, published_snapshot_id="1CECHNKREP0F1RSTCMTX"
    )

    assert cutoff == plain


def test_a_deep_spared_snapshot_on_main_lowers_the_cutoff_to_it(tmp_path, monkeypatch):
    # The walk stops once it has met every spared snapshot (and the aged
    # ancestor); it goes no further back than the deepest spared one.
    cube = Cube(tmp_path, window={"max_steps": 10})
    cube.append(0)
    deep = cube.append(1)
    for n in range(2, 5):
        cube.append(n)
    repo = ic.Repository.open(cube.storage())
    walked = []
    real_ancestry = repo.ancestry

    def counting_ancestry(*a, **kw):
        for snapshot in real_ancestry(*a, **kw):
            walked.append(snapshot.id)
            yield snapshot

    monkeypatch.setattr(repo, "ancestry", counting_ancestry, raising=False)

    cutoff = expiry_cutoff(
        repo,
        LATER,
        3600,
        recorded_snapshot_id=repo.lookup_branch(BRANCH),
        published_snapshot_id=deep,
    )

    assert cutoff == repo.lookup_snapshot(deep).written_at
    assert walked[-1] == deep  # stopped there: the older append and root unread
    assert len(walked) == 4


async def test_an_unreadable_published_snapshot_skips_expiry_not_the_run(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, now=scan(3))
    before = cube.snapshot_files()

    async def broken(sink):
        raise ConnectionError("pool exhausted")

    deps = cube.deps(now=scan(3) + dt.timedelta(minutes=25))
    deps.published_snapshot = broken

    summary = await run_cube_maintain("s1", deps)

    assert summary["trimmed"] == 2  # the trim still ran
    assert summary["expired_snapshots"] is None and summary["gc"] is None
    assert cube.snapshot_files() == before + 1  # the trim's, nothing collected
    assert summary["attention"] == ["expiry_skipped"]
    assert summary["status"] == "attention"


async def test_a_run_that_fails_after_its_repository_pass_still_gets_a_readout(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, now=scan(3))

    async def broken(*a, **kw):
        raise ConnectionError("db blip")

    cube.repo.record_snapshot = broken
    with pytest.raises(ConnectionError):
        await run_cube_maintain("s1", cube.deps(now=scan(3) + dt.timedelta(minutes=25)))

    assert cube.sink.last_maintenance["status"] == "failed"
    assert cube.measure_queue == ["s1"]


async def test_a_run_that_fails_before_its_repository_gets_no_readout(tmp_path):
    # Nothing was reached, so a readout would only post "0 bytes" onto a
    # failed summary as if it had measured an empty repository.
    cube = Cube(tmp_path, window=None)
    cube.append(0)
    deps = cube.deps()

    def boom(sink):
        raise ConnectionError("platform bucket unreachable")

    deps.storage_for = boom
    with pytest.raises(ConnectionError):
        await run_cube_maintain("s1", deps)

    assert cube.measure_queue == []


async def test_an_untouched_tip_is_read_once(tmp_path, monkeypatch):
    import pipeline.cubes.maintain as maintain_mod

    cube = Cube(tmp_path, window={"max_steps": 10})  # nothing to trim
    cube.append(0, 1)
    reads = []
    real = maintain_mod.read_state
    monkeypatch.setattr(
        maintain_mod, "read_state", lambda *a, **kw: reads.append(1) or real(*a, **kw)
    )

    summary = await cube.run(now=scan(1))

    assert summary["trimmed"] == 0
    assert len(reads) == 1
    assert list(cube.published[-1][1].values) == [as_ns(scan(0)), as_ns(scan(1))]


async def test_a_failed_republish_is_attention_and_the_run_still_finishes(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, now=scan(3))

    async def broken(sink, config, result):
        raise TimeoutError("canceling statement due to lock timeout")

    deps = cube.deps(now=scan(3) + dt.timedelta(minutes=25))
    deps.after_batch = broken

    summary = await run_cube_maintain("s1", deps)

    assert summary["trimmed"] == 2
    assert cube.sink.last_snapshot_id == cube.tip()  # the trim is still recorded
    assert summary["status"] == "attention"
    assert summary["attention"] == ["publish_failed"]
    assert summary["publish_outcome"] == "error"
    assert "lock timeout" in summary["publish_error"]
    assert summary["published"] is False
    assert cube.sink.last_maintained_at is not None


def test_published_snapshot_of_finds_this_sinks_asset_version():
    assets = {
        "thumb": {"href": "x"},
        "cube": {"href": "s3://b/_cube/", "version": "SNAP", "stac_higher:cube_sink_id": "s1"},
        "other": {"version": "NOPE", "stac_higher:cube_sink_id": "s2"},
    }
    assert published_snapshot_of(assets, "s1") == "SNAP"
    assert published_snapshot_of(assets, "s3") is None
    no_version = {"c": {"stac_higher:cube_sink_id": "s1"}}
    assert published_snapshot_of(no_version, "s1") is None
    assert published_snapshot_of(None, "s1") is None
    assert published_snapshot_of([], "s1") is None


async def test_a_windowless_sink_never_reads_the_published_snapshot(tmp_path):
    cube = Cube(tmp_path, window=None)
    cube.append(0)
    reads = []

    async def tracking(sink):
        reads.append(sink.id)
        return None

    deps = cube.deps()
    deps.published_snapshot = tracking
    await run_cube_maintain("s1", deps)

    assert reads == []


def _published(snapshot_id: str | None):
    async def _read(sink) -> str | None:
        return snapshot_id

    return _read


async def test_a_trim_is_published_after_it_is_recorded(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, now=scan(3))
    order: list[str] = []
    record = cube.repo.record_snapshot

    async def tracking_record(*a, **kw):
        order.append("record")
        return await record(*a, **kw)

    async def tracking_publish(sink, config, result):
        order.append(f"publish:{cube.sink.last_snapshot_id == result.snapshot_id}")
        return await cube.publish(sink, config, result)

    cube.repo.record_snapshot = tracking_record
    deps = cube.deps(now=scan(3) + dt.timedelta(minutes=25))  # cutoff scan(2)
    deps.after_batch = tracking_publish

    await run_cube_maintain("s1", deps)

    assert order == ["record", "publish:True"]
    _, result = cube.published[0]
    assert (result.committed, result.trimmed, result.initialised) == (True, 2, True)
    assert list(result.values) == [as_ns(scan(2)), as_ns(scan(3))]


@pytest.mark.parametrize(
    ("outcome", "published"),
    [
        ("published", True),
        ("unchanged", True),
        ("superseded", False),
        ("missing_collection", False),
    ],
)
async def test_published_reports_what_the_writer_did(tmp_path, outcome, published):
    cube = Cube(tmp_path, window={"max_steps": 10})
    cube.append(0, 1)
    cube.publish_outcome = outcome

    summary = await cube.run(now=scan(1))

    assert len(cube.published) == 1  # the writer was always called
    assert summary["publish_outcome"] == outcome
    assert summary["published"] is published


async def test_the_recorded_tip_is_republished_on_every_run(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 10})
    tip = cube.append(0, 1)

    summary = await cube.run(now=scan(1))

    assert summary["published"] is True
    _, result = cube.published[0]
    assert (result.snapshot_id, result.committed, result.trimmed) == (tip, False, 0)
    assert len(result.values) == 2
    assert set(result.statics) == {"x", "y", "goes_imager_projection"}
    assert result.spatial_dims == ("y", "x")


def test_the_repository_pass_carries_the_grid_for_the_writer(tmp_path):
    # A form save drops cube:dimensions, and for a stopped source this
    # republish is the only one: it must carry x/y and the projection.
    cube = Cube(tmp_path, window=None)
    cube.append(0, 1)

    rp = maintain_repository(
        cube.storage(),
        cube.config,
        LATER,
        retention_seconds=3600,
        recorded_snapshot_id=cube.sink.last_snapshot_id,
        may_trim=True,
    )

    assert set(rp.state.statics) == {"x", "y", "goes_imager_projection"}
    assert list(rp.state.values) == [as_ns(scan(0)), as_ns(scan(1))]


async def test_a_trim_the_sink_lost_is_not_published(tmp_path, caplog):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, now=scan(3))

    async def lost(*a, **kw):
        return False  # another writer recorded a different tip meanwhile

    cube.repo.record_snapshot = lost
    with caplog.at_level(logging.WARNING, logger="pipeline.cubes.maintain"):
        summary = await cube.run(now=scan(3) + dt.timedelta(minutes=25))

    assert summary["trimmed"] == 2
    assert cube.published == []
    assert "the trim stays unrecorded and unpublished" in caplog.text
    assert summary["attention"] == ["unrecorded_tip"]


async def test_a_cube_trimmed_to_empty_is_published_with_no_values(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, now=scan(1))

    summary = await cube.run(now=scan(20))

    assert summary["trimmed"] == 2
    _, result = cube.published[0]
    assert len(result.values) == 0
    assert cube.times() == []


async def test_the_size_warning_fires_at_the_threshold(tmp_path, caplog):
    cube = Cube(tmp_path, window=None)
    cube.append(0)
    total = (await cube.run())["total_bytes"]
    assert total > 0

    below = await cube.run(warn_bytes=total + 1)
    assert (below["status"], below["attention"]) == ("ok", [])
    with caplog.at_level(logging.WARNING, logger="pipeline.cubes.maintain"):
        at = await cube.run(warn_bytes=total)

    assert (at["status"], at["attention"]) == ("attention", [ATTENTION_REPO_SIZE])
    assert at["warn_bytes"] == total
    assert "at or above CUBE_REPO_WARN_BYTES" in caplog.text
    assert cube.sink.last_maintenance["status"] == "attention"


async def test_terminal_ledger_rows_older_than_seven_days_are_pruned(tmp_path):
    cube = Cube(tmp_path, window=None)
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=LEDGER_RETENTION_DAYS, hours=1)
    rows = {
        "old-appended": ("appended", old),
        "old-skipped": ("skipped", old),
        "old-pending": ("pending", old),  # never pruned
        "new-failed": ("failed", dt.datetime.now(dt.UTC)),
    }
    for item, (status, updated) in rows.items():
        cube.repo.ledger[("s1", item)] = FakeLedgerRow(
            "s1", item, scan(0), status, None, updated_at=updated
        )

    summary = await cube.run()

    assert summary["ledger_pruned"] == 2
    assert sorted(r.item_id for r in cube.repo.rows("s1")) == ["new-failed", "old-pending"]


async def test_a_sink_with_no_repository_is_pruned_but_never_creates_one(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 3})

    summary = await cube.run()

    assert summary["repository"] is False
    assert summary["status"] == "ok"
    assert "sizes" not in summary
    assert cube.listed == 0
    assert not cube.dir.exists()
    assert cube.sink.last_maintained_at == LATER


async def test_a_failure_is_recorded_and_raised(tmp_path):
    cube = Cube(tmp_path, window=None)
    cube.append(0)
    cube.sink.last_maintained_at = scan(0)  # an earlier success
    deps = cube.deps()

    def boom(sink):
        raise ConnectionError("platform bucket unreachable")

    deps.storage_for = boom
    with pytest.raises(ConnectionError):
        await run_cube_maintain("s1", deps)

    assert cube.sink.last_maintenance["status"] == "failed"
    assert "platform bucket unreachable" in cube.sink.last_maintenance["error"]
    assert cube.sink.last_maintained_at == scan(0)  # the last success stands


async def test_the_size_readout_is_enqueued_after_the_record_not_run_inside(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 10})
    cube.append(0)
    order: list[str] = []
    record = cube.repo.record_maintenance

    async def tracking_record(*a, **kw):
        order.append("record")
        return await record(*a, **kw)

    async def tracking_enqueue(cube_sink_id):
        order.append(f"enqueue:{cube_sink_id}")

    cube.repo.record_maintenance = tracking_record
    deps = cube.deps()
    deps.enqueue_measure = tracking_enqueue

    summary = await run_cube_maintain("s1", deps)

    assert order == ["record", "enqueue:s1"]
    assert cube.listed == 0  # the listing is the measure job's, outside the lock
    assert "sizes" not in summary


async def test_a_readout_that_waited_behind_a_newer_run_lands_on_it(tmp_path):
    # A backlog: two runs, one coalesced readout. It measures the current
    # summary, so neither hour goes dark (review of #118).
    cube = Cube(tmp_path, window={"max_steps": 10})
    cube.append(0)
    await run_cube_maintain("s1", cube.deps(now=LATER))
    await run_cube_maintain("s1", cube.deps(now=LATER + dt.timedelta(hours=1)))

    result = await run_cube_measure("s1", cube.measure_deps(warn_bytes=1))
    again = await run_cube_measure("s1", cube.measure_deps(warn_bytes=1))  # redelivered

    assert (result["merged"], again["merged"]) == (True, True)
    summary = cube.sink.last_maintenance
    assert summary["started_at"] == (LATER + dt.timedelta(hours=1)).isoformat()
    assert summary["total_bytes"] == result["total_bytes"] > 0
    assert summary["attention"] == ["repo_size"]  # once, not once per readout
    assert summary["status"] == "attention"
    assert "list" in summary["durations_ms"]


async def test_a_readout_before_any_maintenance_is_dropped(tmp_path):
    cube = Cube(tmp_path, window=None)
    cube.append(0)

    result = await run_cube_measure("s1", cube.measure_deps())

    assert result["merged"] is False
    assert cube.sink.last_maintenance is None


async def test_a_listing_failure_is_attention_and_a_later_success_clears_the_error(tmp_path):
    cube = Cube(tmp_path, window=None)
    cube.append(0)
    await run_cube_maintain("s1", cube.deps())
    deps = cube.measure_deps()

    def boom(sink):
        raise ConnectionError("platform bucket unreachable")

    deps.list_objects = boom
    await run_cube_measure("s1", deps)

    summary = cube.sink.last_maintenance
    assert "platform bucket unreachable" in summary["measure_error"]
    assert summary["attention"] == ["measure_failed"]
    assert summary["status"] == "attention"  # a readout that keeps failing is visible
    assert "sizes" not in summary

    await run_cube_measure("s1", cube.measure_deps())
    summary = cube.sink.last_maintenance
    assert "measure_error" not in summary
    assert "sizes" in summary
    assert summary["attention"] == []  # the false alarm clears (peer review #3)
    assert summary["status"] == "ok"


async def test_a_readout_under_the_threshold_clears_an_earlier_size_warning(tmp_path):
    cube = Cube(tmp_path, window=None)
    cube.append(0)
    await run_cube_maintain("s1", cube.deps())

    await run_cube_measure("s1", cube.measure_deps(warn_bytes=1))
    assert cube.sink.last_maintenance["attention"] == ["repo_size"]
    await run_cube_measure("s1", cube.measure_deps(warn_bytes=1024**3))

    assert cube.sink.last_maintenance["attention"] == []
    assert cube.sink.last_maintenance["status"] == "ok"


async def test_a_stale_readout_never_lands_on_a_newer_summary_or_readout(tmp_path):
    # Peer review #6: a slow readout that started listing before the current
    # run (pre-GC sizes), or before the readout already merged, is dropped.
    cube = Cube(tmp_path, window=None)
    cube.append(0)
    await run_cube_maintain("s1", cube.deps(now=LATER))

    early = cube.measure_deps(warn_bytes=1)
    early.now = lambda: LATER - dt.timedelta(minutes=1)  # before the run started
    assert (await run_cube_measure("s1", early))["merged"] is False

    fresh = await run_cube_measure("s1", cube.measure_deps())
    assert fresh["merged"] is True
    behind = cube.measure_deps(warn_bytes=1)
    behind.now = lambda: LATER + dt.timedelta(seconds=1)  # after the run, before `fresh`
    assert (await run_cube_measure("s1", behind))["merged"] is False

    summary = cube.sink.last_maintenance
    assert summary["attention"] == [] and summary["status"] == "ok"


async def test_a_readout_never_turns_a_failed_run_into_ok(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, now=scan(3))

    async def broken(*a, **kw):
        raise ConnectionError("db blip")

    cube.repo.record_snapshot = broken
    with pytest.raises(ConnectionError):
        await run_cube_maintain("s1", cube.deps(now=scan(3) + dt.timedelta(minutes=25)))
    await run_cube_measure("s1", cube.measure_deps())

    assert cube.sink.last_maintenance["status"] == "failed"
    assert "sizes" in cube.sink.last_maintenance


async def test_the_size_warning_adds_to_existing_attention(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    recorded = cube.append(0, 1, now=scan(1))
    cube.append(2, now=scan(2))  # unrecorded append tip
    cube.sink.last_snapshot_id = recorded

    summary = await cube.run(now=scan(9), warn_bytes=1)

    assert summary["attention"] == ["unrecorded_tip", "repo_size"]
    assert summary["status"] == "attention"


async def test_an_invalid_config_fails_after_the_ledger_prune(tmp_path):
    cube = Cube(tmp_path, window=None)
    cube.sink.config = {"parser": "grib"}
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
    cube.repo.ledger[("s1", "a")] = FakeLedgerRow(
        "s1", "a", scan(0), "appended", None, updated_at=old
    )

    with pytest.raises(ValueError, match="parser"):
        await cube.run()

    assert cube.repo.rows("s1") == []
    assert cube.sink.last_maintenance["status"] == "failed"


@pytest.mark.parametrize("change", ["gone", "disabled"])
async def test_a_gone_or_disabled_sink_is_left_alone(tmp_path, change):
    cube = Cube(tmp_path, window={"max_steps": 3})
    if change == "gone":
        cube.repo.sinks.clear()
    else:
        cube.sink.enabled = False

    assert await cube.run() is None
    assert cube.listed == 0


async def test_the_platform_lister_lists_only_the_cube_prefix(monkeypatch):
    import pipeline.cubes.maintain as maintain_mod
    from pipeline.config import Settings
    from pipeline.cubes.maintain import platform_lister

    seen: list[tuple[str, str]] = []

    def fake_list_sizes(client, bucket, prefix):
        seen.append((bucket, prefix))
        return [(f"{prefix}repo", 3), (f"{prefix}snapshots/S", 4)]

    monkeypatch.setattr(maintain_mod, "build_platform_client", lambda settings: object())
    monkeypatch.setattr(maintain_mod, "list_sizes", fake_list_sizes)
    settings = Settings.from_env(env={"STAGING_BUCKET": "plat"})
    sink = await FakeCubeRepo(sinks=[FakeSink("s1", "src", "cube1")]).load_sink("s1")

    assert platform_lister(settings)(sink) == [("repo", 3), ("snapshots/S", 4)]
    # The trailing slash: a sibling item "_cube2" must never be counted.
    assert seen == [("plat", "assets/cube1/_cube/")]


async def test_a_platform_lister_builds_one_client_for_all_its_calls(monkeypatch):
    import pipeline.cubes.maintain as maintain_mod
    from pipeline.config import Settings
    from pipeline.cubes.maintain import platform_lister

    built: list[object] = []
    used: list[object] = []

    def fake_build(settings):
        built.append(object())
        return built[-1]

    monkeypatch.setattr(maintain_mod, "build_platform_client", fake_build)
    monkeypatch.setattr(
        maintain_mod, "list_sizes", lambda client, bucket, prefix: used.append(client) or []
    )
    lister = platform_lister(Settings.from_env(env={"STAGING_BUCKET": "plat"}))
    sink = await FakeCubeRepo(sinks=[FakeSink("s1", "src", "cube1")]).load_sink("s1")

    assert built == []  # nothing built until the first listing
    lister(sink)
    lister(sink)

    assert len(built) == 1
    assert used == [built[0], built[0]]
