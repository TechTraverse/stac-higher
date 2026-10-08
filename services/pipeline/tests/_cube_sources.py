"""GOES-shaped HDF5 files on local disk: the fake NODD source for cube tests.

``write_goes_file`` writes what VirtualiZarr's HDFParser reads from a real ABI
L2 CMIP file: chunked ``CMI``/``DQF`` (y, x) arrays with ``x``/``y``
dimension scales, a scalar ``t`` in seconds since J2000 and a scalar
``goes_imager_projection``. Every file gets a whole-second mtime, like S3's
LastModified, because Icechunk compares ``last_updated_at`` to it. A
sub-second mtime fails the read.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import h5py
import icechunk as ic
import numpy as np
from obspec_utils.registry import ObjectStoreRegistry
from obstore.store import LocalStore

from pipeline.cubes.source import SourceLibs

J2000 = dt.datetime(2000, 1, 1, 12, tzinfo=dt.UTC)
T0 = dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)
SOURCE_MTIME = 1_790_000_000
SOURCE_LAST_MODIFIED = dt.datetime.fromtimestamp(SOURCE_MTIME, dt.UTC)
GOES_CONFIG = {
    "parser": "hdf5",
    "append_dim": "t",
    "variables": ["CMI", "DQF"],
    "loadable_variables": ["t", "x", "y", "goes_imager_projection"],
}


def scan(n: int) -> dt.datetime:
    """The n-th 5-minute scan after T0."""
    return T0 + dt.timedelta(minutes=5 * n)


def as_ns(when: dt.datetime) -> np.datetime64:
    """How the cube holds ``t``: naive UTC datetime64[ns]."""
    return np.datetime64(when.astimezone(dt.UTC).replace(tzinfo=None), "ns")


def write_goes_file(
    path,
    *,
    when: dt.datetime,
    value: float = 0.0,
    shape: tuple[int, int] = (4, 6),
    chunks: tuple[int, int] = (2, 3),
    variables: tuple[str, ...] = ("CMI", "DQF"),
    mtime: int = SOURCE_MTIME,
    x0: float = 0.0,
    perspective_point_height: float = 35786023.0,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        y = f.create_dataset("y", data=np.arange(shape[0], dtype="f4") * -1e-4)
        y.make_scale("y")
        x = f.create_dataset("x", data=np.arange(shape[1], dtype="f4") * 1e-4 + x0)
        x.make_scale("x")
        for name in variables:
            dtype = "i1" if name == "DQF" else "f4"
            data = f.create_dataset(name, data=np.full(shape, value, dtype), chunks=chunks)
            data.dims[0].attach_scale(y)
            data.dims[1].attach_scale(x)
        if "CMI" in variables:
            f["CMI"].attrs["grid_mapping"] = "goes_imager_projection"
        t = f.create_dataset("t", data=np.float64((when - J2000).total_seconds()))
        t.attrs["units"] = "seconds since 2000-01-01 12:00:00"
        proj = f.create_dataset("goes_imager_projection", data=np.int32(-2147483647))
        proj.attrs["grid_mapping_name"] = "geostationary"
        proj.attrs["perspective_point_height"] = perspective_point_height
    os.utime(path, (mtime, mtime))
    return path


def local_libs(root) -> SourceLibs:
    """A local directory as a cube source: the same shape as an S3 bucket."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return SourceLibs(
        prefix=f"file://{root}/",
        registry_key=f"file://{root}",
        store=LocalStore(prefix=str(root)),
        container_store=ic.local_filesystem_store(str(root)),
        credentials=ic.credentials.LocalFileSystemAccess,
    )


def registry_for(*libs: SourceLibs) -> ObjectStoreRegistry:
    return ObjectStoreRegistry({lib.registry_key: lib.store for lib in libs})
