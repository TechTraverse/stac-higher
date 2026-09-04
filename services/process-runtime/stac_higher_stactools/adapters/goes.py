"""stactools-goes: one ABI L2 netCDF per item; COG sidecars are never asked for."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import pick


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.goes.stac import ProductHrefs, create_item

    nc = pick(paths, (".nc",), "ABI netCDF")
    return create_item([ProductHrefs(nc_href=str(nc), cog_hrefs=None)])
