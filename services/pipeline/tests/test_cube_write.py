"""write_batch: classify, write, trim and commit once (spec §6.2 steps 5-7)."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

import icechunk as ic
import numpy as np
import pytest
import xarray as xr

import pipeline.cubes.write as write_mod
from _cube_sources import (
    GOES_CONFIG,
    SOURCE_LAST_MODIFIED,
    SOURCE_MTIME,
    as_ns,
    local_libs,
    registry_for,
    scan,
    write_goes_file,
)
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, open_repository
from pipeline.cubes.source import SourceLibs
from pipeline.cubes.steps import parse_header, step_value
from pipeline.cubes.write import ParsedStep, write_batch

PLAIN = parse_cube_sink_config(GOES_CONFIG)
NOW = scan(100)


def _config(**window) -> object:
    return parse_cube_sink_config({**GOES_CONFIG, "window": window})


@dataclass
class Cube:
    root: Path
    libs: SourceLibs
    repo: ic.Repository

    def parsed(self, row_id: int, n: int, *, value: float | None = None, **kw) -> ParsedStep:
        name = f"f{n}-{row_id}.nc"
        pixel = float(n if value is None else value)
        write_goes_file(self.root / name, when=scan(n), value=pixel, **kw)
        step = parse_header(self.libs.url(name), registry_for(self.libs), PLAIN)
        t = step_value(step, "t")
        return ParsedStep(row_id, f"item-{row_id}", step, t, SOURCE_LAST_MODIFIED)

    def write(self, *steps: ParsedStep, config=PLAIN, now: dt.datetime = NOW):
        return write_batch(self.repo, list(steps), config, now)

    def read(self) -> xr.Dataset:
        return xr.open_zarr(
            self.repo.readonly_session(BRANCH).store, consolidated=False, zarr_format=3
        )

    def times(self) -> list:
        return list(self.read()["t"].values)

    def pixels(self) -> list[float]:
        return [float(v) for v in self.read()["CMI"].isel(x=0, y=0).values]

    def commits(self) -> int:
        return len(list(self.repo.ancestry(branch=BRANCH))) - 1  # minus the root


@pytest.fixture
def cube(tmp_path) -> Cube:
    libs = local_libs(tmp_path)
    repo = open_repository(ic.in_memory_storage(), [libs], replace_containers=False)
    return Cube(tmp_path, libs, repo)


def test_a_batch_appends_in_time_order_in_one_commit(cube):
    result = cube.write(cube.parsed(2, 2), cube.parsed(1, 0), cube.parsed(3, 1))
    assert result.committed and result.initialised
    assert result.outcomes == {1: ("appended", None), 2: ("appended", None), 3: ("appended", None)}
    assert cube.times() == [as_ns(scan(0)), as_ns(scan(1)), as_ns(scan(2))]
    assert cube.pixels() == [0.0, 1.0, 2.0]
    assert cube.commits() == 1
    assert next(cube.repo.ancestry(branch=BRANCH)).message == "append 3 items: item-1…item-2"
    assert result.snapshot_id == cube.repo.lookup_branch(BRANCH)


def test_a_step_already_present_is_appended_as_a_duplicate(cube):
    first = cube.write(cube.parsed(1, 0))
    # A reprocessed NODD file: a new item, the same scan t, other bytes.
    again = cube.write(cube.parsed(2, 0, value=9.0))
    assert again.outcomes == {2: ("appended", "duplicate")}
    assert (again.committed, again.snapshot_id) == (False, first.snapshot_id)
    assert cube.pixels() == [0.0]


def test_a_step_at_or_before_the_tip_is_skipped_late(cube):
    cube.write(cube.parsed(1, 0), cube.parsed(2, 2))
    result = cube.write(cube.parsed(3, 1))
    assert result.outcomes == {3: ("skipped", "late")}
    assert not result.committed


def test_a_mismatched_layout_is_skipped_and_the_rest_append(cube):
    cube.write(cube.parsed(1, 0))
    result = cube.write(cube.parsed(2, 1, chunks=(4, 6)), cube.parsed(3, 2))
    assert result.outcomes == {2: ("skipped", "unsupported_layout"), 3: ("appended", None)}
    assert cube.times() == [as_ns(scan(0)), as_ns(scan(2))]


def test_a_step_on_another_grid_is_skipped_and_the_cube_grid_kept(cube):
    cube.write(cube.parsed(1, 0))
    before = cube.read()["x"].values.copy()
    result = cube.write(cube.parsed(2, 1, x0=0.5), cube.parsed(3, 2))
    assert result.outcomes == {2: ("skipped", "unsupported_layout"), 3: ("appended", None)}
    # An append rewrites the non-time variables, so this is what the check protects.
    assert np.array_equal(cube.read()["x"].values, before)


def test_a_step_with_another_grid_mapping_is_skipped(cube):
    cube.write(cube.parsed(1, 0))
    result = cube.write(cube.parsed(2, 1, perspective_point_height=35786000.0))
    assert result.outcomes == {2: ("skipped", "unsupported_layout")}


def test_the_first_step_of_a_new_cube_sets_the_layout(cube):
    result = cube.write(cube.parsed(1, 0), cube.parsed(2, 1, chunks=(4, 6)))
    assert result.outcomes == {1: ("appended", None), 2: ("skipped", "unsupported_layout")}


def test_max_steps_trims_in_the_same_commit(cube):
    result = cube.write(*[cube.parsed(i + 1, i) for i in range(5)], config=_config(max_steps=3))
    assert result.trimmed == 2
    assert cube.times() == [as_ns(scan(2)), as_ns(scan(3)), as_ns(scan(4))]
    assert cube.pixels() == [2.0, 3.0, 4.0]  # the virtual refs moved with t
    assert cube.commits() == 1


def test_max_age_trims_by_now(cube):
    steps = [cube.parsed(i + 1, i) for i in range(5)]
    result = cube.write(*steps, config=_config(max_age="10m"), now=scan(4))
    assert result.trimmed == 2
    assert cube.times() == [as_ns(scan(2)), as_ns(scan(3)), as_ns(scan(4))]


def test_a_cube_trimmed_to_empty_still_appends(cube):
    config = _config(max_age="10m")
    emptied = write_batch(cube.repo, [cube.parsed(1, 0)], config, scan(10))
    assert (emptied.trimmed, emptied.initialised, cube.times()) == (1, True, [])
    resumed = write_batch(cube.repo, [cube.parsed(2, 11)], config, scan(11))
    assert resumed.outcomes == {2: ("appended", None)}
    assert cube.times() == [as_ns(scan(11))]


def test_last_updated_at_is_the_source_last_modified(cube):
    cube.write(cube.parsed(1, 0))
    assert cube.pixels() == [0.0]  # LastModified matches: readable
    source = cube.root / "f0-1.nc"
    os.utime(source, (SOURCE_MTIME + 60, SOURCE_MTIME + 60))  # rewritten after the append
    with pytest.raises(Exception, match="checksum"):
        cube.pixels()


def test_a_conflicting_commit_is_redone_once_from_the_new_tip(cube, monkeypatch):
    cube.write(cube.parsed(1, 0))
    real = write_mod.commit_session
    calls: list[str] = []

    def racing(session, message):
        calls.append(message)
        if len(calls) == 1:  # another writer commits scan 1 first
            rival = cube.parsed(9, 1)
            other = cube.repo.writable_session(BRANCH)
            rival.step.vz.to_icechunk(
                other.store, append_dim="t", last_updated_at=SOURCE_LAST_MODIFIED
            )
            other.commit("rival")
        return real(session, message)

    monkeypatch.setattr(write_mod, "commit_session", racing)
    result = cube.write(cube.parsed(2, 1), cube.parsed(3, 2))
    assert len(calls) == 2
    assert result.outcomes == {2: ("appended", "duplicate"), 3: ("appended", None)}
    assert cube.times() == [as_ns(scan(0)), as_ns(scan(1)), as_ns(scan(2))]


def test_a_second_conflict_propagates(cube, monkeypatch):
    cube.write(cube.parsed(1, 0))
    real = write_mod.commit_session
    calls: list[str] = []

    def always_racing(session, message):
        calls.append(message)
        rival = cube.parsed(100 + len(calls), 10 + len(calls))
        other = cube.repo.writable_session(BRANCH)
        rival.step.vz.to_icechunk(other.store, append_dim="t", last_updated_at=SOURCE_LAST_MODIFIED)
        other.commit("rival")
        return real(session, message)

    monkeypatch.setattr(write_mod, "commit_session", always_racing)
    # The step is newer than every rival (scans 11, 12), so the redo still
    # has something to commit and meets the second conflict.
    with pytest.raises(ic.ConflictError):
        cube.write(cube.parsed(2, 50))
    assert len(calls) == 2  # one redo, never a rebase


def test_a_step_whose_write_fails_is_failed_and_the_rest_commit(cube, monkeypatch):
    real = write_mod.write_step

    def flaky(session, parsed, append_dim):
        if parsed.row_id == 2:
            raise RuntimeError("disk on fire")
        real(session, parsed, append_dim)

    monkeypatch.setattr(write_mod, "write_step", flaky)
    result = cube.write(cube.parsed(1, 0), cube.parsed(2, 1), cube.parsed(3, 2))
    assert result.outcomes == {
        1: ("appended", None),
        2: ("failed", "RuntimeError: disk on fire"),
        3: ("appended", None),
    }
    assert cube.times() == [as_ns(scan(0)), as_ns(scan(2))]
    assert cube.commits() == 1


@pytest.mark.parametrize("chained", [False, True])
def test_a_storage_error_fails_the_job_not_the_row(cube, monkeypatch, chained):
    first = cube.write(cube.parsed(1, 0))

    def down(session, parsed, append_dim):
        if chained:
            raise RuntimeError("write failed") from ic.StorageError("silo is down")
        raise ic.StorageError("silo is down")

    monkeypatch.setattr(write_mod, "write_step", down)
    with pytest.raises(Exception, match=r"silo is down|write failed"):
        cube.write(cube.parsed(2, 1), cube.parsed(3, 2))
    assert cube.repo.lookup_branch(BRANCH) == first.snapshot_id
    assert cube.times() == [as_ns(scan(0))]


def test_a_failing_first_step_lets_the_next_one_create_the_cube(cube, monkeypatch):
    real = write_mod.write_step

    def flaky(session, parsed, append_dim):
        if parsed.row_id == 1:
            raise OSError("connection reset")
        real(session, parsed, append_dim)

    monkeypatch.setattr(write_mod, "write_step", flaky)
    result = cube.write(cube.parsed(1, 0), cube.parsed(2, 1))
    assert result.outcomes == {1: ("failed", "OSError: connection reset"), 2: ("appended", None)}
    assert cube.times() == [as_ns(scan(1))]


def test_nothing_to_write_commits_nothing(cube):
    first = cube.write(cube.parsed(1, 1))
    result = cube.write(cube.parsed(2, 0))
    assert (result.committed, result.snapshot_id) == (False, first.snapshot_id)
    assert cube.commits() == 1


def test_error_text_is_bounded():
    text = write_mod.error_text(ValueError("x" * 2000))
    assert text.startswith("ValueError: x") and len(text) == write_mod.MAX_ERROR_CHARS


def test_the_unused_now_does_not_matter_without_a_window(cube):
    result = cube.write(cube.parsed(1, 0), now=dt.datetime(1970, 1, 1, tzinfo=dt.UTC))
    assert result.trimmed == 0
