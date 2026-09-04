"""stactools-noaa-mrms-qpe: one (gzipped) GRIB2 per item.

The package wants the AOI as a parameter; the archive keeps it as the first
path segment (``noaa-mrms-pds/CONUS/...``), read off the draft's source
href, CONUS when absent. A ``.grib2.gz`` is decompressed to a sibling by the
package and the item then points at the ``.grib2`` — the href is put back
on the staged ``.gz`` so the merge matches the draft's file. COG generation
is off (metadata only).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import hrefs_of, path_segment, pick

_AOIS = ("CONUS", "HAWAII", "GUAM", "ALASKA", "CARIB")


def aoi_of(hrefs: list[str]) -> str:
    match = path_segment(hrefs, r"/(" + "|".join(_AOIS) + r")/")
    return match.group(1) if match else "CONUS"


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.noaa_mrms_qpe.constants import AOI
    from stactools.noaa_mrms_qpe.stac import create_item

    grib = pick(paths, (".grib2", ".grib2.gz"), "MRMS GRIB2")
    item = create_item(str(grib), AOI(aoi_of(hrefs_of(draft))), nocog=True)
    for asset in item.assets.values():
        if grib.name.endswith(".gz") and asset.href == str(grib)[: -len(".gz")]:
            asset.href = str(grib)
    return item
