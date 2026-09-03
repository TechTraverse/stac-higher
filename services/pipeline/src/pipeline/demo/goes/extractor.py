"""goes-abi-metadata — the GOES worked example's EXTRACTOR.

Runs inside the process runtime with the extractor contract from
docs/processes.md: for every draft item in the input manifest it sets what
the platform could not infer from a GOES ABI netCDF —

- `datetime`: the scan START from the filename's `_sYYYYDDDHHMMSSt` token
  (the ledger's modified time is minutes later, I-100);
- `platform`, `instruments` and `goes:*` properties from the netCDF's global
  attributes;
- the footprint from the `CMI_C02` subdataset's bounds, reprojected from the
  geostationary CRS to WGS84 — the container dataset carries no
  georeferencing, which is why the built-in path found none (I-101).

It reads the file from LOCAL DISK: GDAL's netCDF driver cannot open /vsi
paths without Linux userfaultfd, which the runtime image does not have.
"""

import datetime as dt
import json
import math
import os
import re
import tempfile

SCAN_TOKEN = re.compile(r"_s(\d{14})")
#: netCDF global attribute -> STAC property.
ABI_ATTRIBUTES = {
    "scene_id": "goes:scene_id",
    "timeline_id": "goes:timeline_id",
    "instrument_type": "goes:instrument_type",
}


def scan_time(filename):
    """`_s20262460401172` -> 2026-09-03T04:01:17.2Z (YYYY DDD HH MM SS + tenths)."""
    match = SCAN_TOKEN.search(filename)
    if not match:
        raise ValueError(f"no _sYYYYDDDHHMMSSt token in {filename!r}")
    digits = match.group(1)
    base = dt.datetime.strptime(digits[:13], "%Y%j%H%M%S").replace(tzinfo=dt.UTC)
    return base + dt.timedelta(milliseconds=100 * int(digits[13]))


def footprint(dataset, samples=25):
    """The dataset's bounds as a WGS84 polygon + bbox.

    The edges are densified before reprojecting so the curved limb of a
    geostationary scene is followed rather than cut by four straight lines;
    vertices that fall off the Earth's disk are dropped. GDAL fails the
    WHOLE call when any point is off-limb rather than returning infinities,
    so the bulk transform is retried point by point when it raises.
    """
    from rasterio.warp import transform

    def project(points):
        try:
            xs_out, ys_out = transform(
                dataset.crs, "EPSG:4326", [p[0] for p in points], [p[1] for p in points]
            )
        except Exception:
            if len(points) == 1:
                return []
            half = len(points) // 2
            return project(points[:half]) + project(points[half:])
        return [
            [x, y]
            for x, y in zip(xs_out, ys_out, strict=True)
            if math.isfinite(x) and math.isfinite(y)
        ]

    left, bottom, right, top = dataset.bounds
    xs = [left + (right - left) * i / samples for i in range(samples + 1)]
    ys = [bottom + (top - bottom) * i / samples for i in range(samples + 1)]
    ring = (
        [(x, bottom) for x in xs]  # south edge, west -> east
        + [(right, y) for y in ys[1:]]  # east edge, south -> north
        + [(x, top) for x in xs[-2::-1]]  # north edge, east -> west
        + [(left, y) for y in ys[-2:0:-1]]  # west edge, north -> south
    )
    coords = project(ring)
    if len(coords) < 3:
        raise ValueError("footprint has fewer than three on-disk vertices")
    coords.append(list(coords[0]))
    bbox = [
        min(c[0] for c in coords),
        min(c[1] for c in coords),
        max(c[0] for c in coords),
        max(c[1] for c in coords),
    ]
    return {"type": "Polygon", "coordinates": [coords]}, bbox


def main():
    import boto3
    import rasterio

    s3 = boto3.client("s3")
    bucket = os.environ["STAC_HIGHER_OUTPUT_BUCKET"]
    prefix = os.environ["STAC_HIGHER_OUTPUT_PREFIX"]
    manifest = json.loads(
        s3.get_object(Bucket=bucket, Key=os.environ["STAC_HIGHER_INPUT_MANIFEST"])[
            "Body"
        ].read()
    )
    print(
        f"batch {manifest['batch_id']}: "
        f"{len(manifest['items'])} draft(s), kind={manifest['kind']}"
    )

    for entry in manifest["items"]:
        item = entry["item"]
        asset_key, asset = next(iter(entry["assets"].items()))
        # Download the STAGED COPY (bucket + key), not the href: the href
        # points back at the origin the pipeline already fetched from.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "scene.nc")
            s3.download_file(asset["bucket"], asset["key"], path)
            with rasterio.open(path) as container:
                tags = container.tags()
            with rasterio.open(f'NETCDF:"{path}":CMI_C02') as band:
                geometry, bbox = footprint(band)

        # The NAME, though, comes from the href — its tail is the original
        # filename, which is what the scan-time contract is written against.
        # (The draft's asset KEY is only the filename stem.)
        href = asset.get("href") or ""
        filename = href.split("?", 1)[0].rsplit("/", 1)[-1] or asset_key
        when = scan_time(filename)
        props = item["properties"]
        props["datetime"] = when.isoformat().replace("+00:00", "Z")
        platform_id = tags.get("NC_GLOBAL#platform_ID", "")
        if platform_id.startswith("G"):
            props["platform"] = f"goes-{platform_id[1:]}"
        props["instruments"] = ["abi"]
        for attribute, prop in ABI_ATTRIBUTES.items():
            value = tags.get(f"NC_GLOBAL#{attribute}")
            if value:
                props[prop] = value
        item["geometry"] = geometry
        item["bbox"] = bbox
        item["assets"][asset_key]["type"] = "application/x-netcdf"
        item["assets"][asset_key].setdefault("roles", ["data"])

        s3.put_object(
            Bucket=bucket,
            Key=f"{prefix}{item['id']}.json",
            Body=json.dumps(item).encode(),
        )
        print(
            f"  {item['id']}: datetime={props['datetime']} "
            f"bbox={[round(v, 2) for v in bbox]}"
        )


if __name__ == "__main__":
    main()
