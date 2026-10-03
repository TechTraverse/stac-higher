# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "mercantile", "numpy"]
# ///
"""Q3: tile latency without overviews. Run right after a server (re)start so the
first request is process-cold. Per zoom (tile over Kansas): every frame once
(frame-cold: chunks fetched from NODD) and again (warm). Then an animation
viewport: all CONUS tiles at z, all frames, 6 concurrent requests (browser-like)."""
import asyncio
import json
import os
import sys
import time

import httpx
import mercantile
import numpy as np

DS = os.environ.get("DS", "goes-c13-m")
PORT = os.environ.get("PORT", "9100")
B = f"http://localhost:{PORT}/datasets/{DS}"
Q = "variables=CMI&style=raster/gray&colorscalerange=190,310&width=256&height=256&f=png"
c = httpx.Client(timeout=600)
TIMES = os.environ["TIMES"].split(",")  # exact t values, ISO with ns
out = dict(ds=DS, frames=len(TIMES))


def get(z, x, y, t):
    t0 = time.perf_counter()
    r = c.get(f"{B}/tiles/WebMercatorQuad/{z}/{y}/{x}?{Q}&t={t}")
    return r.status_code, time.perf_counter() - t0


tile = mercantile.tile(-97.0, 38.5, 4)
st, el = get(4, tile.x, tile.y, TIMES[-1])
out["process_cold_first_tile_s"] = round(el, 3)
out["per_zoom"] = []
for z in range(0, 9):
    tl = mercantile.tile(-97.0, 38.5, z)
    cold = [get(z, tl.x, tl.y, t)[1] for t in TIMES]
    warm = [get(z, tl.x, tl.y, t)[1] for t in TIMES]
    rec = dict(z=z, tile=f"{z}/{tl.x}/{tl.y}", frame_cold_median_s=round(float(np.median(cold)), 3),
               frame_cold_max_s=round(max(cold), 3), warm_median_s=round(float(np.median(warm)), 3),
               warm_p90_s=round(float(np.percentile(warm, 90)), 3))
    out["per_zoom"].append(rec)
    print(json.dumps(rec), flush=True)


async def viewport(z, conc=6):
    tiles = list(mercantile.tiles(-125, 24, -66, 50, [z]))
    sem = asyncio.Semaphore(conc)
    lat = []
    async with httpx.AsyncClient(timeout=600) as ac:
        async def one(tl, t):
            async with sem:
                t0 = time.perf_counter()
                r = await ac.get(f"{B}/tiles/WebMercatorQuad/{z}/{tl.y}/{tl.x}?{Q}&t={t}")
                lat.append(time.perf_counter() - t0)
                return r.status_code
        res = {}
        for label in ("first_pass", "second_pass"):
            lat.clear()
            t0 = time.perf_counter()
            codes = await asyncio.gather(*(one(tl, t) for t in TIMES for tl in tiles))
            res[label] = dict(wall_s=round(time.perf_counter() - t0, 2), requests=len(codes),
                              non200=sum(1 for x in codes if x != 200),
                              median_s=round(float(np.median(lat)), 3), p90_s=round(float(np.percentile(lat, 90)), 3),
                              per_frame_s=round((time.perf_counter() - t0) / len(TIMES), 2))
        return dict(z=z, tiles_per_frame=len(tiles), **res)


out["viewport"] = []
for z in [int(v) for v in os.environ.get("VP_Z", "3,4,5").split(",")]:
    rec = asyncio.run(viewport(z))
    out["viewport"].append(rec)
    print(json.dumps(rec), flush=True)
json.dump(out, open(os.environ.get("OUT", "q3_results.json"), "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k not in ("per_zoom", "viewport")}))
