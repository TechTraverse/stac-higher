"""Where a raster lives, and how to open it there (M3-C, spec §3 / S-E).

EXTRACT used to read a whole object into memory and hand rasterio a
`MemoryFile`: +145 MB of RSS for a 64 MB GeoTIFF, scaling with the asset.
A `RasterLocation` instead names a URI GDAL can range-read (`/vsis3/...` for
an object store, a plain path for a local file) together with the session and
GDAL config options it must be opened under; GDAL's block cache — capped by
`GDAL_CACHEMAX` — does the buffering, so memory is a setting, not a multiple
of the asset (S-E measured +55 MB for the same file, byte-identical item).
For a `/vsi*` URI, `open_raster` also applies fixed vsicurl tuning
(`VSICURL_TUNING`) beneath the location's own options, so a statistics pass
issues large merged range requests instead of thousands of small ones (I-129).

The session is opaque on purpose: for a reference-mode source it carries the
connection's decrypted credentials (`rasterio.session.AWSSession`), which is a
new place they live (spec §5). `__repr__` shows the URI only.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class RasterLocation:
    uri: str
    #: A `rasterio.session.Session` (AWSSession for /vsis3) or None for a local
    #: path. Never logged, never serialised.
    session: Any | None = None
    #: GDAL config options for `rasterio.Env` — only the ones rasterio allows
    #: as kwargs (`AWS_HTTPS`, `AWS_VIRTUAL_HOSTING`, `GDAL_CACHEMAX`, ...).
    #: `GDAL_CACHEMAX` is in **MB** here (matching `Settings.gdal_cachemax_mb`,
    #: M3-C Task 1); `open_raster` converts it to bytes for rasterio. For a
    #: `/vsi*` URI these values are overlaid on top of `VSICURL_TUNING`, so a
    #: key set here always wins over that fixed tuning (I-129).
    options: Mapping[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"RasterLocation({self.uri!r})"


RasterSource = bytes | RasterLocation

#: GDAL vsicurl tuning `open_raster` applies beneath a `/vsi*` location's own
#: options (I-129): no directory listing on open, consecutive ranges merged,
#: a 1 MiB range-read chunk, and the per-handle VSI cache pinned to 16 MiB
#: so it is a known term of the memory envelope (README "Memory envelope").
VSICURL_TUNING: Mapping[str, str] = MappingProxyType(
    {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": str(16 * 1024 * 1024),
        "CPL_VSIL_CURL_CHUNK_SIZE": str(1024 * 1024),
    }
)


def env_kwargs_for(location: RasterLocation) -> dict[str, Any]:
    """The `rasterio.Env` kwargs `open_raster` enters a `RasterLocation`
    under: the I-129 vsicurl tuning for a `/vsi*` URI, its own GDAL options
    overlaid on top (a location's options always win), `GDAL_CACHEMAX`
    coerced from MB to bytes, and its session (when it has one). Pure — no
    rasterio.Env is entered here — so it doubles as the fixture a test builds
    directly without opening anything."""
    env_kwargs: dict[str, Any] = dict(VSICURL_TUNING) if location.uri.startswith("/vsi") else {}
    env_kwargs.update(location.options)
    if "GDAL_CACHEMAX" in env_kwargs:
        # rasterio special-cases this key and routes it straight to
        # GDALSetCacheMax64, which requires a C integer (not a string)
        # AND takes bytes, while the option here (like GDAL's own config
        # string form and Settings.gdal_cachemax_mb) is in MB.
        env_kwargs["GDAL_CACHEMAX"] = int(env_kwargs["GDAL_CACHEMAX"]) * 1024 * 1024
    if location.session is not None:
        env_kwargs["session"] = location.session
    return env_kwargs


@contextmanager
def open_raster(source: RasterSource) -> Iterator[Any]:
    """Open ``source`` as a rasterio dataset: bytes through `/vsimem`, a
    `RasterLocation` in place under its own `rasterio.Env`. rasterio is
    imported lazily so non-raster code paths stay GDAL-free at import time."""
    import rasterio

    if isinstance(source, RasterLocation):
        with rasterio.Env(**env_kwargs_for(source)), rasterio.open(source.uri) as ds:
            yield ds
    elif isinstance(source, bytes | bytearray | memoryview):
        with rasterio.io.MemoryFile(bytes(source)) as mem, mem.open() as ds:
            yield ds
    else:
        raise TypeError(f"open_raster: unsupported source {type(source).__name__}")
