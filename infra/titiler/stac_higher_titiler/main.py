"""titiler-pgstac with platform href mapping (GOES spec §7.1; ISSUES I-68).

Why a wrapper and not a copy of ``titiler.pgstac.main``: the upstream module
builds its factories at import time with the reader CLASSES baked in as
attribute defaults (``PGSTACBackend.reader``, ``MosaicTilerFactory.
dataset_reader``, ``MultiBaseTilerFactory(reader=PgSTACReader)``). Patching
the classes' ``_get_asset_info`` BEFORE importing the upstream app changes
every path at once without vendoring ~280 lines we would then have to keep in
step with upstream. The one place to touch on a titiler-pgstac bump is here
(rio-tiler 9 / titiler-pgstac 3.x changed ``_get_asset_info``'s return shape,
not its name).

Configuration:
- ``PLATFORM_ASSET_BUCKET`` (required): the platform bucket — the pipeline's
  ``STAGING_BUCKET``.
- ``ASSET_HREF_BASE`` (default ``/api/assets``): the pipeline's
  ``ASSET_HREF_BASE``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from titiler.pgstac.reader import PgSTACReader, SimpleSTACReader

from stac_higher_titiler.hrefmap import map_canonical_href

_bucket = os.environ.get("PLATFORM_ASSET_BUCKET", "").strip()
if not _bucket:
    raise RuntimeError(
        "PLATFORM_ASSET_BUCKET is required: the tile server maps /api/assets hrefs "
        "to s3://<bucket>/assets/... and cannot guess the bucket"
    )
_base = os.environ.get("ASSET_HREF_BASE", "/api/assets")


def _wrap(method: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def _get_asset_info(self, asset):  # type: ignore[no-untyped-def]
        info = method(self, asset)
        info["url"] = map_canonical_href(info.get("url"), bucket=_bucket, base=_base)
        return info

    _get_asset_info.__wrapped__ = method  # type: ignore[attr-defined]
    _get_asset_info.__doc__ = method.__doc__
    return _get_asset_info


# Idempotent: re-importing in tests must not double-wrap.
for _cls in (PgSTACReader, SimpleSTACReader):
    _current = _cls.__dict__.get("_get_asset_info", _cls._get_asset_info)
    if not hasattr(_current, "__wrapped__"):
        _cls._get_asset_info = _wrap(_current)  # type: ignore[method-assign]

from titiler.pgstac.main import app  # noqa: E402  (patch first, then import)

__all__ = ["app"]
