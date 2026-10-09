"""A cube's Icechunk repository: storage, open/create, state, reset (spec §6.2 steps 2-3).

The repository lives at ``assets/{cube_collection}/_cube/`` in the platform
bucket (ADR 0022). Every create and open sets
``num_updates_per_repo_info_file = 100`` (I-143). Each source bucket is one
``VirtualChunkContainer`` (``s3://{bucket}/``, never ``s3://``), authorized at
open with that source's credentials. Everything here is blocking, so the job
runs it through ``asyncio.to_thread``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import icechunk as ic
import numpy as np
import xarray as xr
import zarr
from zarr.errors import GroupNotFoundError

from pipeline.config import Settings
from pipeline.cubes.config import CUBE_ITEM_ID
from pipeline.cubes.source import SourceLibs
from pipeline.cubes.steps import ArraySpec, StaticSpec, static_spec
from pipeline.storage.platform import platform_s3_access

BRANCH = "main"
#: I-143: keeps the ``repo`` object near 10 KB (Icechunk's default is 1,000)
REPO_INFO_UPDATES = 100


def cube_prefix(cube_collection_id: str) -> str:
    return f"assets/{cube_collection_id}/{CUBE_ITEM_ID}"


def cube_storage(settings: Settings, cube_collection_id: str) -> ic.Storage:
    """The platform bucket, egress-pinned like every other platform client."""
    access = platform_s3_access(settings)
    endpoint = access.endpoint_url
    return ic.s3_storage(
        bucket=settings.staging_bucket,
        prefix=cube_prefix(cube_collection_id),
        region=access.region,
        endpoint_url=endpoint,
        allow_http=bool(endpoint and endpoint.startswith("http://")),
        force_path_style=access.force_path_style,
        access_key_id=access.access_key,
        secret_access_key=access.secret_key,
    )


def _config(libs: Sequence[SourceLibs] = ()) -> ic.RepositoryConfig:
    cfg = ic.RepositoryConfig.default()
    cfg.num_updates_per_repo_info_file = REPO_INFO_UPDATES
    for lib in libs:
        cfg.set_virtual_chunk_container(ic.VirtualChunkContainer(lib.prefix, lib.container_store))
    return cfg


def open_repository(
    storage: ic.Storage, libs: Sequence[SourceLibs], *, replace_containers: bool
) -> ic.Repository:
    """Open the repository, creating it with ``libs`` as its containers if absent.

    An open repository gains any container it lacks. With
    ``replace_containers`` its containers become exactly ``libs``: the job
    uses that while the repository is provisional, when the app may still
    have changed the source (#98). Either change is saved to the repository
    config."""
    authorize = ic.containers_credentials({lib.prefix: lib.credentials for lib in libs})
    if not ic.Repository.exists(storage):
        return ic.Repository.create(
            storage, _config(libs), authorize_virtual_chunk_access=authorize
        )
    repo = ic.Repository.open(storage, config=_config(), authorize_virtual_chunk_access=authorize)
    known = set(repo.config.virtual_chunk_containers or {})
    wanted = {lib.prefix for lib in libs}
    if (replace_containers and known != wanted) or not wanted <= known:
        cfg = repo.config
        if replace_containers:
            cfg.clear_virtual_chunk_containers()
        for lib in libs:
            cfg.set_virtual_chunk_container(
                ic.VirtualChunkContainer(lib.prefix, lib.container_store)
            )
        repo = repo.reopen(config=cfg, authorize_virtual_chunk_access=authorize)
        repo.save_config()
    return repo


@dataclass(frozen=True)
class CubeState:
    #: ``append_dim`` values, ascending (datetime64[ns] for a time axis)
    values: np.ndarray = field(default_factory=lambda: np.array([], dtype="datetime64[ns]"))
    #: every time-dimensioned array except ``append_dim`` itself
    specs: dict[str, ArraySpec] = field(default_factory=dict)
    #: every array whose first dimension is ``append_dim``, ``append_dim`` included
    time_arrays: tuple[str, ...] = ()
    #: the cube has its ``append_dim`` array (it may hold zero steps)
    initialised: bool = False
    #: every variable without the append axis (``x``, ``y``, the grid mapping)
    statics: dict[str, StaticSpec] = field(default_factory=dict)


def read_state(session: ic.Session, append_dim: str) -> CubeState:
    """The cube as ``session`` sees it: its time values and array layout."""
    try:
        group = zarr.open_group(session.store, mode="r")
    except GroupNotFoundError:
        return CubeState()
    names = sorted(group.array_keys())
    if append_dim not in names:
        return CubeState()
    time_arrays: list[str] = []
    specs: dict[str, ArraySpec] = {}
    for name in names:
        array = group[name]
        dims = array.metadata.dimension_names or ()
        if dims and dims[0] == append_dim:
            time_arrays.append(name)
            if name != append_dim:
                specs[name] = ArraySpec(
                    tuple(array.shape[1:]), tuple(array.chunks[1:]), str(array.dtype)
                )
    # xarray decodes the CF time units; only the small native t array is read.
    ds = xr.open_zarr(session.store, consolidated=False, zarr_format=3, chunks=None)
    return CubeState(
        values=ds[append_dim].values,
        specs=specs,
        time_arrays=tuple(time_arrays),
        initialised=True,
        statics={
            str(name): static_spec(ds[name]) for name in ds.variables if name not in time_arrays
        },
    )


def reset_to_root(repo: ic.Repository, *, from_snapshot_id: str) -> None:
    """Point ``main`` back at the repository's root snapshot, only if it is
    still at ``from_snapshot_id`` (else ``ic.ConflictError``). Moves a ref and
    deletes nothing: the dropped snapshots are garbage for Z-6's GC."""
    root = list(repo.ancestry(branch=BRANCH))[-1].id
    repo.reset_branch(BRANCH, root, from_snapshot_id=from_snapshot_id)
