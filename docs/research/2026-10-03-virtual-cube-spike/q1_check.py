# /// script
# requires-python = ">=3.12"
# dependencies = ["xarray", "h5netcdf", "h5py", "numpy", "pyproj", "pillow", "obstore", "httpx", "mercantile"]
# ///
"""Q1: do xpublish-tiles tiles of the virtual cube land in the right place with the
right values? Renders raster/gray with a fixed colorscalerange, inverts it to K and
compares every tile pixel against a direct h5netcdf read of the same NODD file
(downloaded once to ./local/ — test tooling, not the product path), resampled with
pyproj's geostationary transform. Also searches source-pixel shifts so a misplaced
grid shows up as a best shift != (0, 0)."""
import io
import json
import os
import sys
import time

import httpx
import mercantile
import numpy as np
import obstore as obs
import pyproj
import xarray as xr
from obstore.store import S3Store
from PIL import Image

KEY = "ABI-L2-CMIPC/2026/276/17/OR_ABI-L2-CMIPC-M6C13_G19_s20262761721174_e20262761723559_c20262761724044.nc"
LO, HI = 190.0, 310.0
BASE = f"http://localhost:9100/datasets/{os.environ.get('DS', 'goes-c13')}/tiles/WebMercatorQuad"
POINTS = {"kansas": (-97.0, 38.5), "dc": (-77.0, 38.9), "seattle": (-122.3, 47.6), "miami": (-80.2, 25.8)}

os.makedirs("local", exist_ok=True)
path = os.path.join("local", KEY.rsplit("/", 1)[1])
if not os.path.exists(path):
    src = S3Store(bucket="noaa-goes19", region="us-east-1", skip_signature=True)
    open(path, "wb").write(bytes(obs.get(src, KEY).bytes()))
nc = xr.open_dataset(path, engine="h5netcdf")
T = str(nc.t.values)[:19]
cmi = nc.CMI.values  # CF-decoded (scale/offset/_Unsigned/_FillValue -> NaN)
raw = xr.open_dataset(path, engine="h5netcdf", mask_and_scale=False).CMI
gp = nc.goes_imager_projection.attrs
H = float(gp["perspective_point_height"])
geos = pyproj.CRS.from_cf(gp)
to_geos = pyproj.Transformer.from_crs(3857, geos, always_xy=True)
xs, ys = nc.x.values, nc.y.values
dx, dy = xs[1] - xs[0], ys[1] - ys[0]

# Scaling check on one pixel: raw uint16 * scale + offset vs CF-decoded
r = int(raw.values[750, 1250]) & 0xFFFF
scaling = dict(raw=r, manual=float(r * raw.attrs["scale_factor"] + raw.attrs["add_offset"]), decoded=float(cmi[750, 1250]),
               fill=int(raw.attrs["_FillValue"]), unsigned=raw.attrs.get("_Unsigned"),
               nan_count=int(np.isnan(cmi).sum()))


def expected(tile, n=256):
    b = mercantile.xy_bounds(tile)
    px = b.left + (np.arange(n) + 0.5) * (b.right - b.left) / n
    py = b.top - (np.arange(n) + 0.5) * (b.top - b.bottom) / n
    X, Y = np.meshgrid(px, py)
    gx, gy = to_geos.transform(X, Y)
    return np.asarray(gx) / H, np.asarray(gy) / H


def sample(ax, ay, sx=0, sy=0):
    ix = np.rint((ax - xs[0]) / dx).astype(float) + sx
    iy = np.rint((ay - ys[0]) / dy).astype(float) + sy
    ok = np.isfinite(ix) & np.isfinite(iy) & (ix >= 0) & (ix < len(xs)) & (iy >= 0) & (iy < len(ys))
    out = np.full(ax.shape, np.nan)
    out[ok] = cmi[iy[ok].astype(int), ix[ok].astype(int)]
    return out


results = []
client = httpx.Client(timeout=120)
for name, (lon, lat) in POINTS.items():
    for z in range(3, 9):
        tile = mercantile.tile(lon, lat, z)
        url = f"{BASE}/{z}/{tile.y}/{tile.x}?variables=CMI&style=raster/gray&colorscalerange={LO},{HI}&width=256&height=256&f=png&t=nearest::{T}"
        t0 = time.perf_counter()
        resp = client.get(url)
        el = time.perf_counter() - t0
        img = np.asarray(Image.open(io.BytesIO(resp.content)).convert("RGBA")).astype(float)
        alpha = img[..., 3]
        got = np.where(alpha > 0, LO + img[..., 0] / 255.0 * (HI - LO), np.nan)
        ax, ay = expected(tile)
        best = None
        for sy in range(-3, 4):
            for sx in range(-3, 4):
                exp = sample(ax, ay, sx, sy)
                m = np.isfinite(exp) & np.isfinite(got)
                if m.sum() < 100:
                    continue
                mae = float(np.abs(exp[m] - got[m]).mean())
                if best is None or mae < best[0]:
                    best = (mae, sx, sy)
        exp0 = sample(ax, ay)
        m = np.isfinite(exp0) & np.isfinite(got)
        coverage_mismatch = int((np.isfinite(exp0) != np.isfinite(got)).sum())
        rec = dict(point=name, z=z, tile=f"{z}/{tile.x}/{tile.y}", status=resp.status_code, seconds=round(el, 3),
                   valid_px=int(m.sum()),
                   mae_k=round(float(np.abs(exp0[m] - got[m]).mean()), 3) if m.any() else None,
                   p99_abs_k=round(float(np.percentile(np.abs(exp0[m] - got[m]), 99)), 3) if m.any() else None,
                   best_shift=None if best is None else dict(mae=round(best[0], 3), sx=best[1], sy=best[2]),
                   coverage_mismatch_px=coverage_mismatch)
        results.append(rec)
        print(json.dumps(rec), flush=True)
        if z in (4, 7) and name == "kansas":
            open(f"tiles/q1-{name}-z{z}.png", "wb").write(resp.content)

print(json.dumps(dict(scaling=scaling, t=T), default=str))
json.dump(dict(results=results, scaling=scaling, t=T, key=KEY), open("q1_results.json", "w"), indent=1, default=str)
