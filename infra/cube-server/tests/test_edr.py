import numpy as np
import pytest
import xarray as xr

from stac_higher_cube_server import edr
from stac_higher_cube_server.edr import load_steps_tolerant
from tests.cubes import make_local_cube, step_values


def _pixel(cube) -> xr.Dataset:
    session = cube.repo.readonly_session("main")
    ds = xr.open_zarr(session.store, zarr_format=3, consolidated=False, chunks=None)
    return ds[["CMI"]].isel(x=2, y=3)


def test_missing_step_is_nan_and_counted(tmp_path):
    cube = make_local_cube(tmp_path, steps=3)
    cube.delete_source(1)
    pixel = _pixel(cube)
    loaded, missing = load_steps_tolerant(pixel, "t")
    assert missing == 1
    values = loaded.CMI.values
    assert values[0] == step_values(0)[3, 2]
    assert np.isnan(values[1])
    assert values[2] == step_values(2)[3, 2]
    np.testing.assert_array_equal(loaded.t.values, pixel.t.values)


def test_no_missing_steps_counts_zero(tmp_path):
    cube = make_local_cube(tmp_path, steps=3)
    loaded, missing = load_steps_tolerant(_pixel(cube), "t")
    assert missing == 0
    assert not np.isnan(loaded.CMI.values).any()


def test_other_errors_propagate(tmp_path, monkeypatch):
    cube = make_local_cube(tmp_path, steps=2)

    async def boom(step):
        raise RuntimeError("not a missing source")

    monkeypatch.setattr(edr, "_load_one", boom)
    with pytest.raises(RuntimeError, match="not a missing source"):
        load_steps_tolerant(_pixel(cube), "t")


def test_collapsed_dim_is_one_step(tmp_path):
    cube = make_local_cube(tmp_path, steps=2)
    cube.delete_source(0)
    loaded, missing = load_steps_tolerant(_pixel(cube).isel(t=0), "t")
    assert missing == 1
    assert np.isnan(loaded.CMI.values)
