"""Z-4 live check: real NODD GOES-19 C13 files appended virtually to a cube on S3.

Skipped unless CUBE_IT=1. Needs network to NODD and a throwaway S3-compatible
store for the cube (a z4-prefixed Silo, never the compose stack's):

    CUBE_IT=1 CUBE_IT_ENDPOINT=http://localhost:19000 CUBE_IT_BUCKET=probe \\
    CUBE_IT_KEY=minioadmin CUBE_IT_SECRET=minioadmin \\
    uv run pytest tests/test_cube_it.py -v
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import os
import uuid

import h5py
import icechunk as ic
import numpy as np
import obstore
import pytest
import xarray as xr
from obspec_utils.registry import ObjectStoreRegistry
from obstore.store import S3Store

from pipeline.connections.repo import ConnectionRow
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, open_repository
from pipeline.cubes.source import libs_from_connection
from pipeline.cubes.steps import parse_header, step_value
from pipeline.cubes.write import ParsedStep, write_batch

pytestmark = pytest.mark.skipif(os.environ.get("CUBE_IT") != "1", reason="set CUBE_IT=1")

NODD = ConnectionRow(
    id="nodd", name="nodd", protocol="s3",
    config={"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": True},
    credentials=None, host_key=None,
)
CONFIG = parse_cube_sink_config(
    {
        "parser": "hdf5",
        "append_dim": "t",
        "variables": ["CMI", "DQF"],
        "loadable_variables": ["t", "x", "y", "goes_imager_projection"],
        "window": {"max_steps": 2},
    }
)


def _attr(value):
    """An HDF5 attribute as CF decoding wants it: a scalar, bytes as str."""
    if not isinstance(value, bytes | str):
        value = np.ravel(value)[0]
    return value.decode() if isinstance(value, bytes) else value


def _newest_c13_keys(store, n: int) -> list[str]:
    now = dt.datetime.now(dt.UTC)
    keys: list[str] = []
    for hours in range(3):
        t = now - dt.timedelta(hours=hours)
        for batch in obstore.list(store, prefix=f"ABI-L2-CMIPC/{t:%Y/%j/%H}/", chunk_size=1000):
            keys += [o["path"] for o in batch if "M6C13" in o["path"]]
    return sorted(keys)[-n:]


async def test_nodd_files_append_virtually_and_read_back():
    libs = libs_from_connection(NODD, frozenset())
    keys = _newest_c13_keys(libs.store, 3)
    assert len(keys) == 3
    registry = ObjectStoreRegistry({libs.registry_key: libs.store})
    parsed = []
    for i, key in enumerate(keys):
        meta = await obstore.head_async(libs.store, key)
        step = await asyncio.to_thread(parse_header, libs.url(key), registry, CONFIG)
        name, t = key.rsplit("/", 1)[1], step_value(step, "t")
        parsed.append(ParsedStep(i, name, step, t, meta["last_modified"]))

    endpoint = os.environ["CUBE_IT_ENDPOINT"]
    bucket, prefix = os.environ["CUBE_IT_BUCKET"], f"z4-it-{uuid.uuid4().hex[:8]}/_cube"
    creds = {
        "access_key_id": os.environ["CUBE_IT_KEY"],
        "secret_access_key": os.environ["CUBE_IT_SECRET"],
    }
    storage = ic.s3_storage(
        bucket=bucket, prefix=prefix, endpoint_url=endpoint, region="us-east-1",
        allow_http=endpoint.startswith("http://"), force_path_style=True, **creds,
    )
    repo = open_repository(storage, [libs], replace_containers=True)
    result = write_batch(repo, parsed, CONFIG, dt.datetime.now(dt.UTC))
    # Real files from one sector share the grid check: none is skipped.
    assert set(result.outcomes.values()) == {("appended", None)}
    assert result.committed and result.trimmed == 1
    assert list(ic.Repository.fetch_config(storage).virtual_chunk_containers) == ["s3://noaa-goes19/"]

    ds = xr.open_zarr(repo.readonly_session(BRANCH).store, consolidated=False, zarr_format=3)
    assert ds.sizes["t"] == 2
    virtual = float(ds["CMI"].isel(t=-1, y=750, x=1250).values)
    raw = obstore.get(libs.store, keys[-1]).bytes()
    with h5py.File(io.BytesIO(bytes(raw)), "r") as f:
        cmi = f["CMI"]
        attrs = {
            k: _attr(cmi.attrs[k])
            for k in ("scale_factor", "add_offset", "_FillValue", "_Unsigned")
            if k in cmi.attrs
        }
        # The same CF decoding xarray applies to the virtual read.
        pixel = xr.Variable((), cmi[750, 1250], attrs=attrs)
        direct = float(xr.conventions.decode_cf_variable("CMI", pixel).values)
    assert virtual == pytest.approx(direct, rel=1e-6, nan_ok=True)  # the same pixel, read virtually

    cube_store = S3Store(
        bucket=bucket, endpoint=endpoint, region="us-east-1", virtual_hosted_style_request=False,
        client_options={"allow_http": endpoint.startswith("http://")}, **creds,
    )
    repo_bytes = sum(o["size"] for batch in obstore.list(cube_store, prefix=prefix) for o in batch)
    nodd_bytes = (await obstore.head_async(libs.store, keys[-1]))["size"]
    assert repo_bytes < nodd_bytes  # no source bytes copied
