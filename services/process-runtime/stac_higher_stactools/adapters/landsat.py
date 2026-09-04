"""stactools-landsat: the scene's ``_MTL.xml`` plus siblings (``supports:
grouped``). The USGS STAC geometry lookup is a network call and is off; the
``_ANG.txt`` angle file, which the package reads for the footprint instead,
must be in the group."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import pick


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.landsat.stac import create_item

    mtl = pick(paths, ("_mtl.xml",), "Landsat MTL XML")
    return create_item(str(mtl), use_usgs_geometry=False)
