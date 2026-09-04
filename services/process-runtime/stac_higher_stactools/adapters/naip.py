"""stactools-naip: the COG plus its FGDC metadata (``supports: grouped``).

The package wants the state and NAIP year as parameters and neither is in
the filename (``m_3907864_sw_17_060_20210912.tif``): the year is the date
token's first four digits, the state the two-letter segment that precedes
the year in the archive layout (``naip-analytic/va/2021/...``), read off the
draft's source href.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import (
    AdapterInputError,
    hrefs_of,
    path_segment,
    pick,
    pick_optional,
)

_DATE = re.compile(r"_(\d{8})(?:[_.-]|$)")


def parameters(tif_name: str, hrefs: list[str]) -> tuple[str, str]:
    date = _DATE.search(tif_name)
    if not date:
        raise AdapterInputError(f"{tif_name!r} carries no NAIP acquisition date token")
    year = date.group(1)[:4]
    state = path_segment(hrefs, r"/([a-z]{2})/" + year + r"/")
    if state is None:
        raise AdapterInputError(
            f"no /<state>/{year}/ segment in the draft's hrefs; NAIP needs the state and the "
            "archive layout is the only place it is recorded (use a reference-mode association)"
        )
    return state.group(1), year


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.naip.stac import create_item

    tif = pick(paths, (".tif", ".tiff"), "NAIP GeoTIFF")
    fgdc = pick_optional(paths, (".xml", ".txt"))
    state, year = parameters(tif.name, hrefs_of(draft))
    return create_item(state, year, str(tif), str(fgdc) if fgdc else None)
