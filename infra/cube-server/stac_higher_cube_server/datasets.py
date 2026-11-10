"""From an opened cube to the dataset xpublish serves (spec §8.2)."""

from __future__ import annotations

from collections.abc import Sequence

import xarray as xr


def _grid_mapping_of(var: xr.DataArray) -> str | None:
    """A variable's ``grid_mapping``. ``decode_coords="all"`` (how the registry
    opens a cube, spec §8.2) moves it from ``.attrs`` into ``.encoding``."""
    mapping = var.attrs.get("grid_mapping") or var.encoding.get("grid_mapping")
    return mapping if isinstance(mapping, str) else None


def _grid_mapping_name(ds: xr.Dataset) -> str | None:
    for name in ds.data_vars:
        mapping = _grid_mapping_of(ds[name])
        if mapping is not None and mapping in ds.variables:
            return mapping
    return None


def rescale_geostationary(ds: xr.Dataset) -> xr.Dataset:
    """Scan angles (rad) → projected metres (spike Q2).

    xpublish-edr returns empty 200s on GOES radians; tiles render the same
    either way. Applied only to a ``geostationary`` grid mapping whose ``x``
    is in ``rad``; anything else is returned unchanged.
    """
    mapping = _grid_mapping_name(ds)
    if mapping is None or "x" not in ds.coords or "y" not in ds.coords:
        return ds
    attrs = ds[mapping].attrs
    if attrs.get("grid_mapping_name") != "geostationary" or ds.x.attrs.get("units") != "rad":
        return ds
    h = float(attrs["perspective_point_height"])
    return ds.assign_coords(
        x=(ds.x * h).assign_attrs({**ds.x.attrs, "units": "m"}),
        y=(ds.y * h).assign_attrs({**ds.y.attrs, "units": "m"}),
    )


def prepare_dataset(
    raw: xr.Dataset,
    *,
    variables: Sequence[str],
    append_dim: str,
    collection_id: str,
    snapshot_id: str,
) -> xr.Dataset:
    """Keep the sink's ``variables`` and their grid mapping, rescale, and tag
    the dataset with its snapshot (``_xpublish_id`` invalidates xpublish-tiles'
    grid cache on every reopen) and its append dimension (the position route)."""
    keep = [v for v in variables if v in raw.data_vars]
    if not keep:
        raise ValueError(f"none of the sink's variables {list(variables)} is in the repository")
    mapping = None
    for name in keep:
        candidate = _grid_mapping_of(raw[name])
        if candidate is not None and candidate in raw.variables:
            mapping = candidate
            break
    if mapping is not None and mapping not in keep:
        keep.append(mapping)
    ds = rescale_geostationary(raw[keep])
    ds.attrs["_xpublish_id"] = f"{collection_id}:{snapshot_id}"
    ds.attrs["_cube_append_dim"] = append_dim
    return ds
