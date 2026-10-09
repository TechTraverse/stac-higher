"""cube_maintain_sink: age trim, expiry + GC, ledger prune, size readout (spec §10, I-143)."""

from __future__ import annotations

import datetime as dt
import logging
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
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, open_repository, reset_to_root
from pipeline.cubes.maintain import (
    ATTENTION_REPO_SIZE,
    KINDS,
    LEDGER_RETENTION_DAYS,
    MaintainDeps,
    maintain_repository,
    run_cube_maintain,
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

    async def publish(self, sink, config, result: BatchResult) -> None:
        self.published.append((sink.id, result))

    def deps(self, *, now: dt.datetime = LATER, warn_bytes: int = 1024**3) -> MaintainDeps:
        return MaintainDeps(
            repo=self.repo,
            storage_for=lambda sink: self.storage(),
            list_objects=self.list_objects,
            retention_seconds=3600,
            warn_bytes=warn_bytes,
            after_batch=self.publish,
            now=lambda: now,
        )

    async def run(self, **kw) -> dict | None:
        return await run_cube_maintain("s1", self.deps(**kw))


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
        await cube.publish(sink, config, result)

    cube.repo.record_snapshot = tracking_record
    deps = cube.deps(now=scan(3) + dt.timedelta(minutes=25))  # cutoff scan(2)
    deps.after_batch = tracking_publish

    await run_cube_maintain("s1", deps)

    assert order == ["record", "publish:True"]
    _, result = cube.published[0]
    assert (result.committed, result.trimmed, result.initialised) == (True, 2, True)
    assert list(result.values) == [as_ns(scan(2)), as_ns(scan(3))]


async def test_the_recorded_tip_is_republished_on_every_run(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 10})
    tip = cube.append(0, 1)

    summary = await cube.run(now=scan(1))

    assert summary["published"] is True
    _, result = cube.published[0]
    assert (result.snapshot_id, result.committed, result.trimmed) == (tip, False, 0)
    assert len(result.values) == 2


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
    assert "the sink moved during the trim" in caplog.text


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

    deps.list_objects = boom
    with pytest.raises(ConnectionError):
        await run_cube_maintain("s1", deps)

    assert cube.sink.last_maintenance["status"] == "failed"
    assert "platform bucket unreachable" in cube.sink.last_maintenance["error"]
    assert cube.sink.last_maintained_at == scan(0)  # the last success stands


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
