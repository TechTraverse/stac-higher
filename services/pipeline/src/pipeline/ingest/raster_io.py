"""Where a raster lives, and how to open it there (M3-C, spec §3 / S-E).

EXTRACT used to read a whole object into memory and hand rasterio a
`MemoryFile`: +145 MB of RSS for a 64 MB GeoTIFF, scaling with the asset.
A `RasterLocation` instead names a URI GDAL can range-read (`/vsis3/...` for
an object store, a plain path for a local file) together with the session and
GDAL config options it must be opened under; GDAL's block cache — capped by
`GDAL_CACHEMAX` — does the buffering, so memory is a setting, not a multiple
of the asset (S-E measured +55 MB for the same file, byte-identical item).

The session is opaque on purpose: for a reference-mode source it carries the
connection's decrypted credentials (`rasterio.session.AWSSession`), which is a
new place they live (spec §5). `__repr__` shows the URI only.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
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
    #: M3-C Task 1); `open_raster` converts it to bytes for rasterio.
    options: Mapping[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"RasterLocation({self.uri!r})"


RasterSource = bytes | RasterLocation


@contextmanager
def open_raster(source: RasterSource) -> Iterator[Any]:
    """Open ``source`` as a rasterio dataset: bytes through `/vsimem`, a
    `RasterLocation` in place under its own `rasterio.Env`. rasterio is
    imported lazily so non-raster code paths stay GDAL-free at import time."""
    import rasterio

    if isinstance(source, RasterLocation):
        env_kwargs: dict[str, Any] = dict(source.options)
        if "GDAL_CACHEMAX" in env_kwargs:
            # rasterio special-cases this key and routes it straight to
            # GDALSetCacheMax64, which requires a C integer (not a string)
            # AND takes bytes, while the option here (like GDAL's own config
            # string form and Settings.gdal_cachemax_mb) is in MB.
            env_kwargs["GDAL_CACHEMAX"] = int(env_kwargs["GDAL_CACHEMAX"]) * 1024 * 1024
        if source.session is not None:
            env_kwargs["session"] = source.session
        with rasterio.Env(**env_kwargs), rasterio.open(source.uri) as ds:
            yield ds
    elif isinstance(source, bytes | bytearray | memoryview):
        with rasterio.io.MemoryFile(bytes(source)) as mem, mem.open() as ds:
            yield ds
    else:
        raise TypeError(f"open_raster: unsupported source {type(source).__name__}")
