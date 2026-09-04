"""stactools-modis: the ``.hdf`` and its ``.hdf.xml`` (``supports: grouped``).

The package derives one path from the other by naming convention, so both
must sit in the same directory — which the staged group guarantees. Given
the HDF it also reads the subdatasets' projection; given only the XML it
builds the item from metadata alone. COGs are never created.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import pick, pick_optional


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.modis.stac import create_item

    hdf = pick_optional(paths, (".hdf",))
    source = hdf if hdf is not None else pick(paths, (".hdf.xml",), "MODIS metadata XML")
    return create_item(str(source), create_cogs=False)
