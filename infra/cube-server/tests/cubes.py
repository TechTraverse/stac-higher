"""A tiny virtual GOES-like cube on the local filesystem, for tests.

``make_local_cube`` builds an Icechunk repository whose ``CMI`` chunks (one
per step) are VIRTUAL references to per-step files under a ``file://``
container, like the pipeline's references into NODD. Deleting a step's file
makes reading that step raise ``icechunk.StorageError``, the spike's Q5 case.

The grid is an 8 x 8 patch of GOES-East scan angles (radians, ``units="rad"``)
centred on Kansas (lon -97, lat 38.5), with a ``goes_imager_projection`` grid
mapping, so the server's geostationary rescale, tiles and EDR all see the
real shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import icechunk as ic
import numpy as np
import zarr

from stac_higher_cube_server.sinks import SinkRow

H = 35786023.0
LON_0 = -75.0
#: Kansas, the spike's EDR check point
KANSAS = (-97.0, 38.5)
#: its GOES-East scan angles (pyproj, +proj=geos +sweep=x, GRS80-ish ellipse)
_KX, _KY = -0.049573750505757394, 0.10483740425287064
N = 8
STEP_RAD = 0.0005
T0_NS = 1_790_000_000_123_456_789  # 2026-09-21T...: nanosecond precision on purpose
DT_NS = 300 * 1_000_000_000

PROJECTION_ATTRS = {
    "grid_mapping_name": "geostationary",
    "perspective_point_height": H,
    "semi_major_axis": 6378137.0,
    "semi_minor_axis": 6356752.31414,
    "inverse_flattening": 298.2572221,
    "latitude_of_projection_origin": 0.0,
    "longitude_of_projection_origin": LON_0,
    "sweep_angle_axis": "x",
}


def step_values(step: int) -> np.ndarray:
    """The CMI field of source file ``step``: 200 K + 10 K per step + a gradient."""
    return (200.0 + 10.0 * step + np.arange(N * N, dtype="<f4").reshape(N, N) / 10).astype("<f4")


def t_ns(step: int) -> int:
    return T0_NS + step * DT_NS


def iso_ns(value: int | np.datetime64) -> str:
    """Exact nanosecond ISO string, as ``stac_higher:time_values`` carries it (no ``Z``)."""
    return np.datetime_as_string(np.datetime64(int(value), "ns"), unit="ns")


@dataclass
class LocalCube:
    root: Path
    storage: ic.Storage
    prefix: str
    repo: ic.Repository
    #: the source step number behind each cube position, oldest first
    steps: list[int] = field(default_factory=list)
    _next: int = 0

    @property
    def credentials(self) -> dict:
        return {self.prefix: ic.Credentials.LocalFileSystemAccess()}

    @property
    def snapshot_id(self) -> str:
        return self.repo.lookup_branch("main")

    def _source(self, step: int) -> Path:
        return self.root / "src" / f"{step}.bin"

    def _write(self, steps: list[int], message: str) -> None:
        session = self.repo.writable_session("main")
        group = zarr.open_group(session.store, mode="r+")
        group["CMI"].resize((len(steps), N, N))
        group["t"].resize((len(steps),))
        if steps:
            group["t"][:] = np.array([t_ns(s) for s in steps], dtype="<i8")
        for i, s in enumerate(steps):
            session.store.set_virtual_ref(
                f"CMI/c/{i}/0/0", f"{self.prefix}{s}.bin", offset=0, length=N * N * 4
            )
        session.commit(message)
        self.steps = list(steps)

    def append(self, n: int = 1) -> None:
        new = []
        for _ in range(n):
            step = self._next
            self._source(step).write_bytes(step_values(step).tobytes())
            new.append(step)
            self._next += 1
        self._write([*self.steps, *new], f"append {new}")

    def trim_to(self, n: int) -> None:
        self._write(self.steps[len(self.steps) - n :] if n else [], f"trim to {n}")

    def delete_source(self, position: int) -> None:
        """Delete the source file behind cube position ``position``."""
        self._source(self.steps[position]).unlink()


def make_local_cube(tmp_path: Path, *, steps: int, geostationary: bool = True) -> LocalCube:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    prefix = f"file://{src}/"
    config = ic.RepositoryConfig.default()
    config.set_virtual_chunk_container(
        ic.VirtualChunkContainer(prefix, ic.local_filesystem_store(str(src)))
    )
    storage = ic.local_filesystem_storage(str(tmp_path / "repo"))
    repo = ic.Repository.create(
        storage,
        config,
        authorize_virtual_chunk_access=ic.containers_credentials(
            {prefix: ic.Credentials.LocalFileSystemAccess()}
        ),
    )
    session = repo.writable_session("main")
    group = zarr.group(store=session.store)
    cmi = group.create_array(
        "CMI", shape=(0, N, N), chunks=(1, N, N), dtype="<f4", fill_value=np.nan,
        compressors=None, dimension_names=("t", "y", "x"),
    )
    cmi.attrs.update({"units": "K", "valid_min": 190.0, "valid_max": 310.0})
    t = group.create_array(
        "t", shape=(0,), chunks=(1024,), dtype="<i8", dimension_names=("t",)
    )
    t.attrs.update({"units": "nanoseconds since 1970-01-01", "calendar": "proleptic_gregorian"})
    # GOES x grows west→east, y north→south (descending)
    xs = _KX + (np.arange(N) - N / 2 + 0.5) * STEP_RAD
    ys = _KY - (np.arange(N) - N / 2 + 0.5) * STEP_RAD
    x = group.create_array("x", shape=(N,), dtype="<f8", dimension_names=("x",))
    x[:] = xs
    y = group.create_array("y", shape=(N,), dtype="<f8", dimension_names=("y",))
    y[:] = ys
    units = "rad" if geostationary else "m"
    x.attrs.update({"units": units, "axis": "X", "standard_name": "projection_x_coordinate"})
    y.attrs.update({"units": units, "axis": "Y", "standard_name": "projection_y_coordinate"})
    if geostationary:
        proj = group.create_array("goes_imager_projection", shape=(), dtype="<i4")
        proj.attrs.update(PROJECTION_ATTRS)
        cmi.attrs["grid_mapping"] = "goes_imager_projection"
    session.commit("init")
    cube = LocalCube(root=tmp_path, storage=storage, prefix=prefix, repo=repo)
    if steps:
        cube.append(steps)
    return cube


class FakeSource:
    def __init__(self, *rows: SinkRow) -> None:
        self.rows = list(rows)
        self.error: Exception | None = None

    def enabled_sinks(self) -> list[SinkRow]:
        if self.error is not None:
            raise self.error
        return list(self.rows)


class LocalOpener:
    """Opens each collection's local cube; ``None`` for one with no cube yet."""

    def __init__(self, cubes: dict[str, LocalCube]) -> None:
        self.cubes = cubes
        self.error: Exception | None = None

    def open(self, sink: SinkRow) -> ic.Repository | None:
        if self.error is not None:
            raise self.error
        cube = self.cubes.get(sink.cube_collection_id)
        if cube is None:
            return None
        return ic.Repository.open(
            cube.storage, authorize_virtual_chunk_access=ic.containers_credentials(cube.credentials)
        )
