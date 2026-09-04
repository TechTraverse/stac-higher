"""stactools-noaa-cdr: one netCDF per item. The package leaves ``datetime``
null with ``start_datetime``/``end_datetime`` set; the merge fills it."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import pick


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.noaa_cdr.stac import create_item

    return create_item(str(pick(paths, (".nc",), "CDR netCDF")))
