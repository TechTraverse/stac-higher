"""stactools-noaa-hrrr: the GRIB2 plus its ``.idx`` sidecar (``supports:
grouped``, spec §14).

The package has no href-driven door — ``create_item`` takes (region,
product, cloud provider, reference datetime, forecast hour) and FETCHES the
``.idx`` over HTTP, which an isolated run cannot do. ``create_item_from_idx_df``
is the file-driven one: the staged ``.idx`` is parsed locally and the five
parameters come from the GRIB filename (``hrrr.t12z.wrfsfcf06[.ak].grib2``)
and the ``hrrr.YYYYMMDD/`` segment of the draft's source href. The item the
package builds points its assets at the archive's URLs; the merge matches
them to the staged files by basename.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.adapters._files import AdapterInputError, hrefs_of, path_segment, pick

_NAME = re.compile(
    r"^hrrr\.t(?P<hour>\d{2})z\.wrf(?P<product>[a-z]+)f(?P<fxx>\d{2})(?P<ak>\.ak)?\.grib2$"
)
_DATE = re.compile(r"hrrr\.(?P<date>\d{8})/")


def parse_parameters(grib_name: str, hrefs: list[str]) -> dict[str, Any]:
    match = _NAME.match(grib_name)
    if not match:
        raise AdapterInputError(f"{grib_name!r} is not an HRRR GRIB2 filename")
    date = path_segment(hrefs, _DATE.pattern)
    if date is None:
        raise AdapterInputError("no hrrr.YYYYMMDD/ segment in the draft's hrefs to date the cycle")
    reference = dt.datetime.strptime(date.group("date") + match.group("hour"), "%Y%m%d%H")
    return {
        "region": "alaska" if match.group("ak") else "conus",
        "product": match.group("product"),
        "reference_datetime": reference,
        "forecast_hour": int(match.group("fxx")),
    }


def cloud_provider(hrefs: list[str]) -> str:
    joined = " ".join(hrefs)
    if "blob.core.windows.net" in joined:
        return "azure"
    if "storage.googleapis.com" in joined:
        return "google"
    return "aws"


def create(paths: Mapping[str, Path], draft: Mapping[str, Any]) -> Any:
    from stactools.noaa_hrrr.inventory import read_idx
    from stactools.noaa_hrrr.metadata import CloudProvider, Product, Region
    from stactools.noaa_hrrr.stac import create_item_from_idx_df

    grib = pick(paths, (".grib2",), "HRRR GRIB2")
    idx = pick(paths, (".idx",), "HRRR .idx sidecar")
    hrefs = hrefs_of(draft)
    params = parse_parameters(grib.name, hrefs)
    return create_item_from_idx_df(
        idx_df=read_idx(str(idx)),
        region=Region(params["region"]),
        product=Product(params["product"]),
        cloud_provider=CloudProvider(cloud_provider(hrefs)),
        reference_datetime=params["reference_datetime"],
        forecast_hour=params["forecast_hour"],
    )
