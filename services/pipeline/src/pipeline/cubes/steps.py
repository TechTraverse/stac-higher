"""One NODD file → one cube step, and the layout and window rules (spec §6.2 steps 4-6).

``parse_header`` reads only the file's metadata and its loadable variables:
the ``variables`` become virtual references (VirtualiZarr ManifestArrays),
never bytes. It is blocking, so the job runs it through ``asyncio.to_thread``.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import xarray as xr
from obspec_utils.registry import ObjectStoreRegistry
from virtualizarr import open_virtual_dataset  # also registers the .vz accessor
from virtualizarr.parsers import HDFParser

from pipeline.cubes.config import CubeSinkConfig, CubeWindow


class LayoutError(Exception):
    """The file cannot become a step of this cube (``skipped: unsupported_layout``)."""


@dataclass(frozen=True)
class ArraySpec:
    """A time-dimensioned array's shape, chunks and dtype without the time axis."""

    shape: tuple[int, ...]
    chunks: tuple[int, ...]
    dtype: str


def build_step(vds: xr.Dataset, config: CubeSinkConfig) -> xr.Dataset:
    """Select ``variables`` + ``loadable_variables`` and add the append axis.

    A GOES file carries ``t`` as a scalar. Whether the parser yields it as a
    data variable or a coordinate, it becomes the length-1 index of
    ``append_dim``. Every ``variables`` entry gains that axis (time chunk 1).
    The other loadable variables (``x``, ``y``, the scalar grid mapping) are
    kept as they are.
    """
    ds = vds.reset_coords()
    dim = config.append_dim
    wanted = (*config.variables, *config.loadable_variables)
    missing = sorted({n for n in wanted if n not in ds.variables})
    if missing:
        raise LayoutError(f"missing variables: {', '.join(missing)}")
    if ds[dim].ndim != 0:
        raise LayoutError(f"{dim} is not a scalar in this file")
    names = list(dict.fromkeys([*config.variables, dim]))
    step = ds[names].set_coords(dim).expand_dims(dim)
    for name in config.loadable_variables:
        if name != dim and name not in step.variables:
            step[name] = ds[name]
    return step


def parse_header(url: str, registry: ObjectStoreRegistry, config: CubeSinkConfig) -> xr.Dataset:
    """Blocking: run it through ``asyncio.to_thread``."""
    vds = open_virtual_dataset(
        url,
        registry=registry,
        parser=HDFParser(),
        loadable_variables=list(config.loadable_variables),
    )
    return build_step(vds, config)


def step_value(step: xr.Dataset, append_dim: str) -> np.generic:
    """The step's single ``append_dim`` value."""
    return step[append_dim].values[0]


def step_specs(step: xr.Dataset, config: CubeSinkConfig) -> dict[str, ArraySpec]:
    """Raises :class:`LayoutError` for an array whose time chunk is not 1: the
    window shifts by chunks (spec §6.2 step 5)."""
    specs: dict[str, ArraySpec] = {}
    for name in config.variables:
        data = step[name].data
        metadata = getattr(data, "metadata", None)
        chunks = tuple(getattr(metadata, "chunks", None) or data.shape)
        if chunks[0] != 1:
            raise LayoutError(f"{name} has time chunk {chunks[0]}; a cube step needs 1")
        specs[name] = ArraySpec(tuple(data.shape[1:]), chunks[1:], str(step[name].dtype))
    return specs


@dataclass(frozen=True, eq=False)
class StaticSpec:
    """A variable without the append axis (``x``, ``y``, the grid mapping): its
    decoded values and, for a scalar, its attributes in canonical JSON."""

    values: np.ndarray
    attrs: str | None

    def same_as(self, other: StaticSpec) -> bool:
        if self.values.shape != other.values.shape or self.attrs != other.attrs:
            return False
        try:
            return bool(np.array_equal(self.values, other.values, equal_nan=True))
        except TypeError:  # a dtype without NaN (strings, objects)
            return bool(np.array_equal(self.values, other.values))


def canonical_attrs(attrs: Mapping[str, Any]) -> str:
    """Attributes as JSON, so a file's HDF5 attributes compare equal to the
    same attributes after a round trip through the Zarr metadata (numpy to
    plain, bytes to str, a one-element array to its element)."""

    def plain(value: Any) -> Any:
        if isinstance(value, np.ndarray):
            value = value.tolist()
        elif isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, bytes):
            value = value.decode()
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        return value

    return json.dumps({k: plain(v) for k, v in attrs.items()}, sort_keys=True, default=str)


def static_spec(var: xr.DataArray) -> StaticSpec:
    return StaticSpec(np.asarray(var.values), canonical_attrs(var.attrs) if var.ndim == 0 else None)


def step_statics(step: xr.Dataset, config: CubeSinkConfig) -> dict[str, StaticSpec]:
    return {
        name: static_spec(step[name])
        for name in config.loadable_variables
        if name != config.append_dim
    }


def check_statics(step: Mapping[str, StaticSpec], cube: Mapping[str, StaticSpec]) -> None:
    """The step's grid must be the cube's. An append rewrites the cube's
    non-time variables with the step's, so a step from another grid would
    silently re-georeference every earlier step."""
    for name, spec in step.items():
        have = cube.get(name)
        if have is None:
            raise LayoutError(f"{name} is not a variable of this cube")
        if not spec.same_as(have):
            raise LayoutError(f"{name} differs from the cube's (grid or projection changed)")


def check_layout(step: Mapping[str, ArraySpec], cube: Mapping[str, ArraySpec]) -> None:
    """Every step array must match the cube's array of that name: a virtual
    append needs the same chunk grid, shape and dtype."""
    for name, spec in step.items():
        have = cube.get(name)
        if have is None:
            raise LayoutError(f"{name} is not an array of this cube")
        if have != spec:
            raise LayoutError(f"{name} is {spec}; the cube has {have}")


def trim_count(values: np.ndarray, window: CubeWindow | None, now: dt.datetime) -> int:
    """How many of the oldest steps the window drops (spec §6.2 step 6):
    ``max(len - max_steps, steps older than now - max_age)``. ``values`` is
    ascending. ``max_age`` applies only to a datetime ``append_dim``. The
    result may equal ``len(values)``: a cube may be trimmed to zero steps."""
    if window is None or len(values) == 0:
        return 0
    k = 0
    if window.max_steps is not None:
        k = max(k, len(values) - window.max_steps)
    if window.max_age_seconds is not None and np.issubdtype(values.dtype, np.datetime64):
        cutoff = now.astimezone(dt.UTC).replace(tzinfo=None) - dt.timedelta(
            seconds=window.max_age_seconds
        )
        k = max(k, int(np.searchsorted(values, np.datetime64(cutoff, "ns"), side="left")))
    return k
