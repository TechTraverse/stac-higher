"""stactools-viirs: one HDF5 per item; no COG hrefs, no network."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import pick


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.viirs.stac import create_item

    return create_item(str(pick(paths, (".h5",), "VIIRS HDF5")))
