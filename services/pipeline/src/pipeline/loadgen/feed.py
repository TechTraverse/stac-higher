"""Synthetic granule generation and the emission schedule.

Two properties the measurement depends on, both pinned by tests:

- **One granule is one item.** The harness's ingest config uses grouping rule
  `none`, so files/s offered IS items/s offered. A generator that let two
  granules share a basename would halve the item count without saying so.
- **The schedule is computed from the index, never accumulated.** `t += 1/rate`
  drifts, and a drifting feed means the "offered 30/s" line in the report is
  not the rate that was offered.

Two profiles, because they measure different ceilings:

- :func:`granule` — opaque bytes, paired with the `defaults_only` metadata
  strategy. This is the PLUMBING ceiling: discover → fetch → itemize → outbox
  with no GDAL in the path.
- :func:`raster_granule` — a real single-band GeoTIFF, paired with
  `raster_auto`. This is what NOAA-class ingest actually costs, and it is the
  only profile that says anything about I-26 (EXTRACT buffers rasters in
  memory).

The first S-A run learned this the expensive way: opaque bytes named `.tif`
under the default `raster_auto` strategy failed 30 of 30 items, and the
measurement was of the failure path.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Granule:
    key: str
    body: bytes


def emission_offsets(count: int, rate: float) -> list[float]:
    """Seconds-from-start at which to emit each granule.

    ``rate=0`` means "all at once" — the saturation probe, which asks where
    the backlog forms rather than whether a target rate holds.
    """
    if rate < 0:
        raise ValueError(f"rate must not be negative, got {rate}")
    if rate == 0:
        return [0.0] * count
    return [index / rate for index in range(count)]


def granule(index: int, *, asset_bytes: int, prefix: str) -> Granule:
    """One synthetic source file.

    The body is derived from the index so no two granules are byte-identical:
    an object store that deduped them would make the bytes/s column fiction.
    Exact size, because byte volume is half of what M3-S-A measures.
    """
    if asset_bytes < 0:
        raise ValueError("asset_bytes must not be negative")
    seed = f"stac-higher-load-{index}".encode()
    block = hashlib.blake2b(seed, digest_size=64).digest()
    body = (block * (asset_bytes // len(block) + 1))[:asset_bytes]
    return Granule(key=f"{prefix}g{index:09d}.tif", body=body)


def raster_granule(index: int, *, asset_bytes: int, prefix: str) -> Granule:
    """One synthetic single-band GeoTIFF, sized to roughly ``asset_bytes``.

    Real enough for `raster_auto`: rio-stac needs a CRS and a transform to
    produce a geometry, so the tile is placed at a per-index position on a
    plate-carrée grid — which also makes the items spatially distinct rather
    than 2.6M copies of the same footprint.

    Size is approximate (a GeoTIFF carries a header and the side length is an
    integer), so the harness reports bytes actually written rather than bytes
    requested.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    if asset_bytes < 0:
        raise ValueError("asset_bytes must not be negative")
    side = max(1, math.isqrt(max(asset_bytes, 1)))
    # Deterministic per index, and never a constant block: a store that deduped
    # identical objects would make the byte numbers fiction.
    rng = np.random.default_rng(index)
    data = rng.integers(0, 256, size=(side, side), dtype="uint8")

    # 1°-square tiles walking the grid, so footprints differ item to item.
    west = -180.0 + (index % 360)
    south = -85.0 + ((index // 360) % 170)
    transform = from_bounds(west, south, west + 1.0, south + 1.0, side, side)

    with rasterio.MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff",
            height=side,
            width=side,
            count=1,
            dtype="uint8",
            crs="EPSG:4326",
            transform=transform,
        ) as dataset:
            dataset.write(data, 1)
        body = memfile.read()
    return Granule(key=f"{prefix}r{index:09d}.tif", body=body)
