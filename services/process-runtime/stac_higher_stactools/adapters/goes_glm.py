"""stactools-goes-glm: one GLM LCFA netCDF per item.

The package opens the file for APPEND (it patches missing ``_Unsigned``
attributes and reverts them), so it must see the staged copy, never a
read-only mount; the geoparquet conversion is switched off because it would
write sidecars next to the file that the platform would then drop anyway.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import pick


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.goes_glm.stac import create_item

    nc = pick(paths, (".nc",), "GLM netCDF")
    return create_item(str(nc), nogeoparquet=True)
