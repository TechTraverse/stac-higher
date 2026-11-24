"""#183: tiles over the GOES CONUS sector must not smear past the Earth's limb.

The CONUS sector's north-west corner (about x=-0.101, y=0.128 rad) lies off
the disk. xpublish-tiles masks quads touching the limb only on its
``Geostationary`` grid, which it picks for scan angles in radians; the server
used to serve metres (for EDR), the grid fell back to plain ``Rectilinear``,
and limb quads were drawn as a grey band from the sector's west edge out to
the antimeridian (live check of the cube Preview, tile z3/2/0).
"""

from __future__ import annotations

import io

import numpy as np
import xarray as xr
import xpublish
from fastapi.testclient import TestClient
from PIL import Image
from pyproj import Transformer
from xpublish_tiles.xpublish.tiles import TilesPlugin

from stac_higher_cube_server.datasets import prepare_dataset

H, LON0 = 35786023.0, -75.2
#: 10x coarser than real C13 (5.6e-5 rad) so the render stays fast; the leak
#: measured 6712 px before the fix at this resolution and 184 after.
STEP = 5.6e-4
WEB_MERCATOR_HALF = 20037508.342789244


def conus_sector() -> xr.Dataset:
    x = np.arange(-0.101332, 0.038612 + STEP / 2, STEP)
    y = np.arange(0.128212, 0.044268 - STEP / 2, -STEP)
    projection = {
        "grid_mapping_name": "geostationary",
        "perspective_point_height": H,
        "semi_major_axis": 6378137.0,
        "semi_minor_axis": 6356752.31414,
        "inverse_flattening": 298.2572221,
        "latitude_of_projection_origin": 0.0,
        "longitude_of_projection_origin": LON0,
        "sweep_angle_axis": "x",
    }
    cmi = np.full((1, y.size, x.size), 250.0, dtype="f4")
    return xr.Dataset(
        {
            "CMI": (("t", "y", "x"), cmi, {"grid_mapping": "goes_imager_projection"}),
            "goes_imager_projection": ((), 0, projection),
        },
        coords={
            "t": np.array(["2026-11-10T12:00:00"], dtype="datetime64[ns]"),
            "x": ("x", x, {"units": "rad", "axis": "X"}),
            "y": ("y", y, {"units": "rad", "axis": "Y"}),
        },
    )


def footprint_mask(z: int, ty: int, tx: int, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """True for the tile's pixels that fall inside the sector on the visible disk."""
    size = 2 * WEB_MERCATOR_HALF / 2**z
    px = -WEB_MERCATOR_HALF + tx * size + (np.arange(256) + 0.5) * size / 256
    py = WEB_MERCATOR_HALF - ty * size - (np.arange(256) + 0.5) * size / 256
    lon, lat = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True).transform(
        *np.meshgrid(px, py)
    )
    gx, gy = Transformer.from_crs(
        "EPSG:4326", f"+proj=geos +h={H} +lon_0={LON0} +sweep=x +ellps=GRS80", always_xy=True
    ).transform(lon, lat)
    return (
        np.isfinite(gx)
        & (gx >= x.min() * H) & (gx <= x.max() * H)
        & (gy >= y.min() * H) & (gy <= y.max() * H)
    )


def test_conus_tile_west_of_the_sector_is_transparent_past_the_limb():
    raw = conus_sector()
    ds = prepare_dataset(raw, variables=["CMI"], append_dim="t", collection_id="c",
                         snapshot_id="s")
    assert ds.x.attrs["units"] == "rad"  # what selects xpublish-tiles' Geostationary grid

    client = TestClient(xpublish.Rest({"c": ds}, plugins={"tiles": TilesPlugin()}).app)
    r = client.get("/datasets/c/tiles/WebMercatorQuad/3/2/0", params={
        "variables": "CMI", "t": "2026-11-10T12:00:00", "style": "raster/Greys",
        "colorscalerange": "190,310", "width": 256, "height": 256, "f": "png",
    })
    assert r.status_code == 200, r.text[:300]
    alpha = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGBA"))[..., 3]

    outside = ~footprint_mask(3, 2, 0, raw.x.values, raw.y.values)
    leaked = int(((alpha > 0) & outside).sum())
    # Edge bleed of half a (coarse) cell is expected; the smear was ~6700 px.
    assert leaked < 400, f"{leaked} opaque pixels outside the sector's visible footprint"
