"""The GOES worked example's scripts (G-7, spec §9), tested as plain modules.

The runtime entrypoint exec()s each script with __name__ == "__main__"; here
they are exec'd with another name so their functions can be called on
synthetic data. No network, no container, no netCDF file: the footprint is
checked on a GeoTIFF in the geostationary CRS, which is the property that
made I-101 — a projection the built-in path cannot footprint.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from pipeline.demo.goes import OUTPUT_COLLECTION_TOKEN, extractor_code, geocolor_code

GEOS_CRS = (
    "+proj=geos +lon_0=-75 +h=35786023 +x_0=0 +y_0=0 "
    "+ellps=GRS80 +units=m +no_defs +sweep=x"
)


def _load(source: str) -> dict:
    ns: dict = {"__name__": "test_demo_goes"}
    exec(compile(source, "<goes>", "exec"), ns)
    return ns


@pytest.fixture(scope="module")
def extractor():
    return _load(extractor_code())


@pytest.fixture(scope="module")
def geocolor():
    return _load(geocolor_code("goes-geocolor"))


def test_scripts_compile_and_never_open_vsi_paths():
    for source in (extractor_code(), geocolor_code("x")):
        compile(source, "<goes>", "exec")
        # Spec §15: the runtime image's netCDF driver cannot read /vsi paths.
        assert "/vsis3" not in source and "/vsicurl" not in source
    assert OUTPUT_COLLECTION_TOKEN not in geocolor_code("goes-geocolor")
    assert '"goes-geocolor"' in geocolor_code("goes-geocolor")


def test_scan_time_parses_the_filename_token(extractor):
    when = extractor["scan_time"](
        "OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc"
    )
    assert when == dt.datetime(2026, 9, 3, 4, 1, 17, 200_000, tzinfo=dt.UTC)
    with pytest.raises(ValueError):
        extractor["scan_time"]("scene.nc")


def test_footprint_reprojects_a_geostationary_dataset(extractor):
    # A 10x10 tile of 2 km pixels near the sub-satellite point: every corner
    # is on-disk, so the polygon is closed, finite, and around lon -75.
    transform = from_origin(-10_000.0, 10_000.0, 2_000.0, 2_000.0)
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=10, height=10, count=1, dtype="uint16",
                      crs=GEOS_CRS, transform=transform) as ds:
            ds.write(np.zeros((1, 10, 10), dtype="uint16"))
        with mem.open() as ds:
            geometry, bbox = extractor["footprint"](ds)
    ring = geometry["coordinates"][0]
    assert geometry["type"] == "Polygon" and ring[0] == ring[-1] and len(ring) > 20
    assert all(math.isfinite(x) and math.isfinite(y) for x, y in ring)
    assert bbox[0] < -75 < bbox[2] and bbox[1] < 0 < bbox[3]
    assert abs(bbox[0] - bbox[2]) < 1 and abs(bbox[1] - bbox[3]) < 1


def test_compose_daylight_night_and_fill(geocolor):
    # pixel 0: bright day cloud; pixel 1: clear night land (warm); pixel 2:
    # night cold cloud top; pixel 3: fill (off-disk).
    c01 = np.array([[0.8, 0.0, 0.0, np.nan]])
    c02 = np.array([[0.9, 0.0, 0.0, np.nan]])
    c03 = np.array([[0.7, 0.0, 0.0, np.nan]])
    c13 = np.array([[220.0, 300.0, 200.0, np.nan]])
    rgb, mask = geocolor["compose"](c01, c02, c03, c13)
    assert rgb.shape == (3, 1, 4) and rgb.dtype == np.uint8 and mask.dtype == np.uint8
    assert rgb[0, 0, 0] > 200                       # day: red from C02 after gamma
    assert 0 < rgb[0, 0, 1] < 40                    # warm night surface: faint IR
    assert rgb[0, 0, 2] > rgb[0, 0, 1]              # cold cloud brighter than warm land at night
    assert (rgb[:, 0, 3] == 0).all() and mask[0, 3] == 0   # fill: black + masked
    assert (mask[0, :3] == 255).all()


def test_read_band_applies_scale_offset_and_fill(geocolor, tmp_path):
    # A scaled uint16 GeoTIFF stands in for a CMI subdataset.
    transform = from_origin(0, 0, 1, 1)
    path = tmp_path / "band.tif"
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=2, height=1, count=1, dtype="uint16",
                      crs=GEOS_CRS, transform=transform, nodata=65535) as ds:
            ds.write(np.array([[[1000, 65535]]], dtype="uint16"))
            ds.scales = (0.001,)
            ds.offsets = (0.0,)
        path.write_bytes(mem.read())
    values, crs, tr = geocolor["read_band"](str(path), None)
    assert values[0, 0] == pytest.approx(1.0) and np.isnan(values[0, 1])
    assert crs is not None and tr == transform


def test_footprint_drops_off_disk_vertices(extractor):
    # A full-disk extent: the bounding box's CORNERS fall off the Earth's
    # limb (radius 7.1e6 m) while its edge midpoints are on it (5.0e6 m,
    # inside the ~5.43e6 m disk). GDAL 3.12 does not hand the off-limb
    # points back as non-finite — it fails the whole reprojection — so
    # footprint() must survive it and keep the on-disk part of the ring.
    transform = from_origin(-5.0e6, 5.0e6, 1.0e6, 1.0e6)
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=10, height=10, count=1, dtype="uint16",
                      crs=GEOS_CRS, transform=transform) as ds:
            ds.write(np.zeros((1, 10, 10), dtype="uint16"))
        with mem.open() as ds:
            geometry, bbox = extractor["footprint"](ds)
    ring = geometry["coordinates"][0]
    assert 3 < len(ring) < 101  # some vertices dropped, some kept
    assert ring[0] == ring[-1]
    assert all(math.isfinite(x) and math.isfinite(y) for x, y in ring)
    assert -180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90
