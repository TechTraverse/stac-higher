"""Header → step, layout and window rules (spec §6.2 steps 4-6)."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from _cube_sources import GOES_CONFIG, as_ns, local_libs, registry_for, scan, write_goes_file
from pipeline.cubes.config import CubeWindow, parse_cube_sink_config
from pipeline.cubes.steps import (
    ArraySpec,
    LayoutError,
    build_step,
    canonical_attrs,
    check_layout,
    check_statics,
    parse_header,
    step_specs,
    step_statics,
    step_value,
    trim_count,
)

CONFIG = parse_cube_sink_config(GOES_CONFIG)


def _parse(tmp_path, name: str = "a.nc", **kw) -> xr.Dataset:
    write_goes_file(tmp_path / name, when=kw.pop("when", scan(0)), **kw)
    libs = local_libs(tmp_path)
    return parse_header(libs.url(name), registry_for(libs), CONFIG)


def test_a_goes_file_becomes_one_step(tmp_path):
    step = _parse(tmp_path, value=7.0)
    assert dict(step.sizes) == {"t": 1, "y": 4, "x": 6}
    assert step["CMI"].dims == ("t", "y", "x")
    assert step["DQF"].dims == ("t", "y", "x")
    assert step["goes_imager_projection"].dims == ()  # the grid mapping stays a scalar
    assert step_value(step, "t") == as_ns(scan(0))


def test_a_missing_variable_is_a_layout_error(tmp_path):
    with pytest.raises(LayoutError, match="DQF"):
        _parse(tmp_path, variables=("CMI",))


def _plain(t, cmi_dims=("y", "x"), cmi_shape=(2, 3)) -> xr.Dataset:
    return xr.Dataset(
        {
            "CMI": (cmi_dims, np.zeros(cmi_shape, "f4")),
            "DQF": (cmi_dims, np.zeros(cmi_shape, "i1")),
            "goes_imager_projection": ((), np.int32(0)),
        },
        coords={"t": t, "x": np.arange(3.0), "y": np.arange(2.0)},
    )


def test_t_as_a_scalar_coordinate_also_works():
    step = build_step(_plain(as_ns(scan(1))), CONFIG)
    assert step["CMI"].dims == ("t", "y", "x")
    assert step_value(step, "t") == as_ns(scan(1))


def test_a_file_holding_several_times_is_a_layout_error():
    many = _plain([as_ns(scan(0)), as_ns(scan(1))], ("t", "y", "x"), (2, 2, 3))
    with pytest.raises(LayoutError, match="not a scalar"):
        build_step(many, CONFIG)


def test_step_specs_and_the_layout_check(tmp_path):
    specs = step_specs(_parse(tmp_path), CONFIG)
    assert specs == {
        "CMI": ArraySpec((4, 6), (2, 3), "float32"),
        "DQF": ArraySpec((4, 6), (2, 3), "int8"),
    }
    check_layout(specs, specs)
    with pytest.raises(LayoutError, match="CMI"):
        check_layout(specs, {**specs, "CMI": ArraySpec((4, 6), (4, 6), "float32")})
    with pytest.raises(LayoutError, match="DQF"):
        check_layout(specs, {"CMI": specs["CMI"]})


def test_a_step_with_more_than_one_time_per_chunk_is_a_layout_error():
    step = build_step(_plain(as_ns(scan(0))), CONFIG)
    # numpy-backed: one chunk of 2 along t
    doubled = xr.concat([step, step], dim="t", data_vars="all")
    with pytest.raises(LayoutError, match="time chunk 2"):
        step_specs(doubled, CONFIG)


def test_statics_compare_the_grid_values_and_the_grid_mapping_attrs(tmp_path):
    base = step_statics(_parse(tmp_path, "a.nc"), CONFIG)
    assert set(base) == {"x", "y", "goes_imager_projection"}
    check_statics(base, base)
    shifted = step_statics(_parse(tmp_path, "b.nc", x0=0.5), CONFIG)
    with pytest.raises(LayoutError, match="x differs"):
        check_statics(shifted, base)
    moved = step_statics(_parse(tmp_path, "c.nc", perspective_point_height=1.0), CONFIG)
    with pytest.raises(LayoutError, match="goes_imager_projection differs"):
        check_statics(moved, base)


def test_canonical_attrs_ignores_the_hdf5_array_wrapping():
    hdf5 = {"h": np.array([35786023.0]), "name": np.bytes_(b"geostationary")}
    zarr_json = {"name": "geostationary", "h": 35786023.0}
    assert canonical_attrs(hdf5) == canonical_attrs(zarr_json)


VALUES = np.array([as_ns(scan(i)) for i in range(5)])


def test_trim_by_max_steps():
    assert trim_count(VALUES, CubeWindow(max_steps=3, max_age_seconds=None), scan(4)) == 2


def test_trim_by_max_age_drops_steps_strictly_older_than_the_cutoff():
    # now = T0+20 min, max_age 10 min: the cutoff is T0+10 min (scan 2), kept.
    assert trim_count(VALUES, CubeWindow(max_steps=None, max_age_seconds=600), scan(4)) == 2


def test_trim_takes_the_larger_of_both_rules():
    assert trim_count(VALUES, CubeWindow(max_steps=4, max_age_seconds=600), scan(4)) == 2


def test_no_window_or_no_steps_trims_nothing():
    assert trim_count(VALUES, None, scan(4)) == 0
    empty = np.array([], dtype="datetime64[ns]")
    assert trim_count(empty, CubeWindow(1, 60), scan(4)) == 0


def test_max_age_ignores_an_append_dim_that_is_not_time():
    assert trim_count(np.array([1.0, 2.0]), CubeWindow(None, 60), scan(4)) == 0


def test_a_window_can_trim_every_step():
    assert trim_count(VALUES, CubeWindow(None, 60), scan(10)) == 5
