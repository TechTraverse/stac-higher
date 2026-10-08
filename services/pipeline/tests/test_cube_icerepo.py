"""The cube's Icechunk repository (spec §6.2 steps 2-3, I-143)."""

from __future__ import annotations

import icechunk as ic
import pytest

from _cube_sources import (
    GOES_CONFIG,
    SOURCE_LAST_MODIFIED,
    as_ns,
    local_libs,
    registry_for,
    scan,
    write_goes_file,
)
from pipeline.config import Settings
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.icerepo import (
    BRANCH,
    REPO_INFO_UPDATES,
    cube_prefix,
    cube_storage,
    open_repository,
    read_state,
    reset_to_root,
)
from pipeline.cubes.steps import ArraySpec, check_statics, parse_header, step_statics

CONFIG = parse_cube_sink_config(GOES_CONFIG)


def _append(repo, libs, root, name, n, *, first: bool) -> str:
    write_goes_file(root / name, when=scan(n))
    step = parse_header(libs.url(name), registry_for(libs), CONFIG)
    session = repo.writable_session(BRANCH)
    step.vz.to_icechunk(
        session.store, append_dim=None if first else "t", last_updated_at=SOURCE_LAST_MODIFIED
    )
    return session.commit(f"append {name}")


def test_create_sets_the_history_setting_and_the_source_container(tmp_path):
    storage, libs = ic.in_memory_storage(), local_libs(tmp_path)
    open_repository(storage, [libs], replace_containers=False)
    cfg = ic.Repository.fetch_config(storage)
    assert cfg.num_updates_per_repo_info_file == REPO_INFO_UPDATES == 100
    assert list(cfg.virtual_chunk_containers) == [libs.prefix]


def test_open_adds_a_new_source_container_and_keeps_the_old(tmp_path):
    storage = ic.in_memory_storage()
    a, b = local_libs(tmp_path / "a"), local_libs(tmp_path / "b")
    open_repository(storage, [a], replace_containers=False)
    open_repository(storage, [b], replace_containers=False)
    assert set(ic.Repository.fetch_config(storage).virtual_chunk_containers) == {a.prefix, b.prefix}


def test_replace_containers_keeps_exactly_the_given_sources(tmp_path):
    storage = ic.in_memory_storage()
    a, b = local_libs(tmp_path / "a"), local_libs(tmp_path / "b")
    open_repository(storage, [a], replace_containers=False)
    open_repository(storage, [b], replace_containers=True)
    assert list(ic.Repository.fetch_config(storage).virtual_chunk_containers) == [b.prefix]


def test_a_new_repository_is_uninitialised(tmp_path):
    repo = open_repository(ic.in_memory_storage(), [local_libs(tmp_path)], replace_containers=False)
    state = read_state(repo.readonly_session(BRANCH), "t")
    assert (state.initialised, len(state.values), state.specs, state.time_arrays) == (
        False, 0, {}, ()
    )


def test_state_after_a_write(tmp_path):
    libs = local_libs(tmp_path)
    repo = open_repository(ic.in_memory_storage(), [libs], replace_containers=False)
    _append(repo, libs, tmp_path, "a.nc", 0, first=True)
    _append(repo, libs, tmp_path, "b.nc", 1, first=False)
    state = read_state(repo.readonly_session(BRANCH), "t")
    assert state.initialised
    assert list(state.values) == [as_ns(scan(0)), as_ns(scan(1))]
    assert state.time_arrays == ("CMI", "DQF", "t")
    assert state.specs["CMI"] == ArraySpec((4, 6), (2, 3), "float32")
    # The grid read back from the cube equals the grid of the file it came from.
    assert set(state.statics) == {"x", "y", "goes_imager_projection"}
    fresh = parse_header(libs.url("a.nc"), registry_for(libs), CONFIG)
    check_statics(step_statics(fresh, CONFIG), state.statics)


def test_reset_to_root_empties_the_branch_and_is_compare_and_swap(tmp_path):
    libs = local_libs(tmp_path)
    repo = open_repository(ic.in_memory_storage(), [libs], replace_containers=False)
    snapshot = _append(repo, libs, tmp_path, "a.nc", 0, first=True)
    root = list(repo.ancestry(branch=BRANCH))[-1].id
    with pytest.raises(ic.ConflictError):
        reset_to_root(repo, from_snapshot_id=root)  # the branch is not at root
    reset_to_root(repo, from_snapshot_id=snapshot)
    assert repo.lookup_branch(BRANCH) == root
    assert not read_state(repo.readonly_session(BRANCH), "t").initialised


def test_cube_storage_points_at_the_reserved_prefix():
    settings = Settings.from_env(
        env={
            "STAGING_BUCKET": "stac-higher",
            "STAGING_S3_ENDPOINT": "http://minio:9000",
            "EGRESS_ALLOW_HOSTS": "minio",
        }
    )
    assert cube_prefix("goes19-c13-cube") == "assets/goes19-c13-cube/_cube"
    text = repr(cube_storage(settings, "goes19-c13-cube"))
    assert "bucket: stac-higher" in text
    assert "prefix: assets/goes19-c13-cube/_cube" in text
    assert "endpoint_url: http://minio:9000" in text
    assert "force_path_style: True" in text
