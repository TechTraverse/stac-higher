import numpy as np
import xarray as xr

from stac_higher_cube_server.datasets import prepare_dataset, rescale_geostationary
from tests.cubes import PROJECTION_ATTRS, H, make_local_cube, step_values


def _raw(units: str = "rad", grid: str = "geostationary") -> xr.Dataset:
    data = np.zeros((1, 2, 2), dtype="f4")
    ds = xr.Dataset(
        {
            "CMI": (("t", "y", "x"), data, {"grid_mapping": "goes_imager_projection"}),
            "DQF": (("t", "y", "x"), data),
            "goes_imager_projection": ((), 0, {**PROJECTION_ATTRS, "grid_mapping_name": grid}),
        },
        coords={
            "t": [np.datetime64("2026-10-03T17:02:36.714359936", "ns")],
            "x": ("x", [0.01, 0.02], {"units": units, "axis": "X"}),
            "y": ("y", [0.1, 0.09], {"units": units, "axis": "Y"}),
        },
    )
    return ds


def test_geostationary_xy_become_metres():
    raw = _raw()
    ds = rescale_geostationary(raw)
    assert ds.x.values[0] == raw.x.values[0] * H
    assert ds.y.values[1] == raw.y.values[1] * H
    assert ds.x.attrs["units"] == "m"
    assert ds.x.attrs["axis"] == "X"


def test_non_geostationary_is_untouched():
    raw = _raw(grid="latitude_longitude")
    assert rescale_geostationary(raw).x.values[0] == 0.01


def test_metres_already_is_untouched():
    raw = _raw(units="m")
    assert rescale_geostationary(raw).x.values[0] == 0.01


def test_no_grid_mapping_is_untouched():
    raw = _raw().drop_vars("goes_imager_projection")
    raw["CMI"].attrs.pop("grid_mapping")
    assert rescale_geostationary(raw).x.values[0] == 0.01


def test_keeps_variables_and_grid_mapping_only():
    ds = prepare_dataset(_raw(), variables=["CMI"], append_dim="t",
                         collection_id="c1", snapshot_id="SNAP")
    assert set(ds.data_vars) == {"CMI", "goes_imager_projection"}


def test_xpublish_id_names_collection_and_snapshot():
    ds = prepare_dataset(_raw(), variables=["CMI"], append_dim="t",
                         collection_id="c1", snapshot_id="SNAP")
    assert ds.attrs["_xpublish_id"] == "c1:SNAP"
    assert ds.attrs["_cube_append_dim"] == "t"


def test_make_local_cube_round_trips(tmp_path):
    cube = make_local_cube(tmp_path, steps=2)
    session = cube.repo.readonly_session("main")
    ds = xr.open_zarr(session.store, zarr_format=3, consolidated=False, chunks=None,
                      decode_coords="all")
    assert ds.sizes["t"] == 2
    np.testing.assert_array_equal(ds.CMI.isel(t=1).values, step_values(1))
    assert ds.t.dtype.kind == "M"


def test_no_requested_variable_in_the_repository_raises():
    import pytest

    with pytest.raises(ValueError, match="none of the sink's variables"):
        prepare_dataset(_raw(), variables=["NOPE"], append_dim="t", collection_id="c",
                        snapshot_id="s")
