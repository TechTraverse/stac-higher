"""goes-geocolor — the GOES worked example's PROCESS.

A "GeoColor-style" true-colour composite from ABI L2 MCMIPC (docs/processes.md
worked example; GOES spec §9): red = C02, blue = C01, a synthetic green from
C02/C03/C01 (the CIMSS recipe — no Rayleigh correction, no city lights), a
gamma of 2.2, and on the night side the C13 brightness temperature inverted
and rendered through a NOAA-style ramp — warm surface deep blue, cold cloud
tops white — so cold cloud tops glow where there is no sunlight. Day and night
are blended per pixel by SOLAR ZENITH ANGLE across a twilight band, so dusk
fades instead of flipping (G-8). Written as a 3-band uint8 COG with an
internal mask, in the file's own geostationary CRS; the tile server reprojects
on the fly.

Reads the staged netCDF from LOCAL DISK (the runtime image's netCDF driver
cannot open /vsi paths) and publishes one item with a `visual` asset through
the normal process output path.
"""

import datetime as dt
import json
import math
import os
import tempfile

import numpy as np

OUTPUT_COLLECTION = "__OUTPUT_COLLECTION__"
BANDS = ("CMI_C01", "CMI_C02", "CMI_C03", "CMI_C13")
GAMMA = 2.2
IR_COLD_K, IR_WARM_K = 90.0, 313.0
# G-8 night ramp: the inverted C13 layer is coloured, not grey. Its warm end
# (clear ground, low cloud) is a deep blue and its cold end (high cloud tops)
# is white, linear in the inverted brightness temperature — the NOAA look.
NIGHT_WARM_RGB = (0.02, 0.05, 0.18)
NIGHT_COLD_RGB = (1.00, 1.00, 1.00)
# The twilight band, in degrees of solar zenith: full day at or below the
# first, full night at or above the second, linear between. 96 deg is a little
# past civil twilight, where the visible bands have nothing left in them.
TWILIGHT_DAY_DEG, TWILIGHT_NIGHT_DEG = 80.0, 96.0
#: Pixels between solar-zenith samples (see solar_zenith).
ZENITH_STEP = 64


def read_band(path, name):
    """Physical values (reflectance, or K for C13) with fill -> NaN, plus the
    band's CRS and transform. `name=None` opens `path` as a plain raster.

    float32, not float64: four bands of a full-disk MCMIPF grid (5424^2) are
    ~470 MB in float32 and ~940 MB in float64, and the intermediates in
    compose() multiply that — more than the revision's declared memory. Every
    uint16 CMI value is exact in float32, so nothing is lost.
    """
    import rasterio

    source = path if name is None else f'NETCDF:"{path}":{name}'
    with rasterio.open(source) as src:
        raw = src.read(1).astype("float32")
        scale = src.scales[0] if src.scales and src.scales[0] else 1.0
        offset = src.offsets[0] if src.offsets and src.offsets[0] else 0.0
        values = (raw * scale + offset).astype("float32")
        if src.nodata is not None:
            values[raw == src.nodata] = np.nan
        return values, src.crs, src.transform


def sun_position(when):
    """Solar declination (radians) and the equation of time (minutes).

    The NOAA low-precision formulae: a few hundredths of a degree of error,
    two orders of magnitude inside the twilight band they feed, and no new
    dependency for the runtime image (spec §9).
    """
    when = when.replace(tzinfo=dt.UTC) if when.tzinfo is None else when.astimezone(dt.UTC)
    day = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60.0 + when.second / 3600.0
    gamma = 2.0 * math.pi / 365.0 * (day - 1 + (hour - 12.0) / 24.0)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.001480 * math.sin(3 * gamma)
    )
    return declination, eqtime


def zenith_degrees(lat, lon, when):
    """Solar zenith angle in degrees for arrays of latitude and longitude.

    NaN where the position is not finite — off the Earth's limb a
    geostationary grid does not reproject at all.
    """
    declination, eqtime = sun_position(when)
    utc = when.replace(tzinfo=dt.UTC) if when.tzinfo is None else when.astimezone(dt.UTC)
    minutes = utc.hour * 60.0 + utc.minute + utc.second / 60.0
    finite = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90.0)
    lat_r = np.radians(np.where(finite, lat, 0.0))
    hour_angle = np.radians(
        (minutes + eqtime + 4.0 * np.where(finite, lon, 0.0)) / 4.0 - 180.0
    )
    cos_zenith = np.sin(lat_r) * math.sin(declination) + np.cos(lat_r) * math.cos(
        declination
    ) * np.cos(hour_angle)
    return np.where(finite, np.degrees(np.arccos(np.clip(cos_zenith, -1.0, 1.0))), np.nan)


def _nearest_fill(values):
    """Replace NaNs with the nearest finite value along each axis.

    Samples beyond the Earth's limb come back as infinities from PROJ; left
    in, the upsample below would smear a meaningless angle across the on-disk
    pixels sharing their coarse cell.
    """
    out = np.asarray(values, dtype="float32")

    def forward(a, axis):
        shape = [1] * a.ndim
        shape[axis] = a.shape[axis]
        index = np.where(np.isnan(a), 0, np.arange(a.shape[axis]).reshape(shape))
        np.maximum.accumulate(index, axis=axis, out=index)
        return np.take_along_axis(a, index, axis=axis)

    for axis in (0, 1):
        out = forward(out, axis)
        out = np.flip(forward(np.flip(out, axis=axis), axis), axis=axis)
    return np.nan_to_num(out, nan=0.0)


def _project(crs, xs, ys):
    """WGS84 lon/lat for a list of points, NaN where a point does not project.

    GDAL fails the WHOLE call when any point is off the Earth's disk rather
    than returning infinities — the behaviour the extractor's footprint()
    also works around — so a failing batch is bisected down to the individual
    points that cannot be projected.
    """
    from rasterio.warp import transform as warp_transform

    try:
        lon, lat = warp_transform(crs, "EPSG:4326", xs, ys)
    except Exception:
        if len(xs) <= 1:
            return [math.nan] * len(xs), [math.nan] * len(ys)
        half = len(xs) // 2
        lon_a, lat_a = _project(crs, xs[:half], ys[:half])
        lon_b, lat_b = _project(crs, xs[half:], ys[half:])
        return lon_a + lon_b, lat_a + lat_b
    return list(lon), list(lat)


def solar_zenith(crs, transform, shape, when, step=ZENITH_STEP):
    """Per-pixel solar zenith angle in degrees for a raster grid, float32.

    Sampled every `step` pixels and bilinearly upsampled: the angle moves
    about one degree per 110 km, so a 64-pixel cell (128 km at ABI's 2 km) is
    two orders of magnitude finer than the twilight band it feeds, and the
    reprojection costs a few hundred points rather than one per pixel.
    """
    import rasterio.transform

    height, width = shape
    rows = np.unique(np.clip(np.arange(0, height + step, step), 0, height - 1))
    cols = np.unique(np.clip(np.arange(0, width + step, step), 0, width - 1))
    grid_rows, grid_cols = np.meshgrid(rows, cols, indexing="ij")
    xs, ys = rasterio.transform.xy(transform, grid_rows.ravel(), grid_cols.ravel())
    lon, lat = _project(crs, xs, ys)
    reshape = grid_rows.shape
    coarse = _nearest_fill(
        zenith_degrees(
            np.asarray(lat, dtype="float64").reshape(reshape),
            np.asarray(lon, dtype="float64").reshape(reshape),
            when,
        )
    )

    # Bilinear upsample, rows then columns, so the only full-size array is the
    # result itself.
    row_pos = np.interp(np.arange(height), rows, np.arange(rows.size))
    col_pos = np.interp(np.arange(width), cols, np.arange(cols.size))
    r0 = np.floor(row_pos).astype("int32")
    r1 = np.minimum(r0 + 1, rows.size - 1)
    c0 = np.floor(col_pos).astype("int32")
    c1 = np.minimum(c0 + 1, cols.size - 1)
    wr = (row_pos - r0).astype("float32")[:, None]
    wc = (col_pos - c0).astype("float32")[None, :]
    by_row = coarse[r0] * (np.float32(1.0) - wr) + coarse[r1] * wr
    return (by_row[:, c0] * (np.float32(1.0) - wc) + by_row[:, c1] * wc).astype("float32")


def compose(c01, c02, c03, c13, zenith):
    """uint8 RGB [3, h, w] + uint8 mask [h, w] (255 = on-disk).

    `zenith` is the per-pixel solar zenith angle in degrees (`solar_zenith()`);
    a scalar works too. G-8 blends the day and night layers by it across the
    twilight band instead of taking a per-pixel maximum, so dusk fades rather
    than flipping, and renders the night layer through the NOAA-style ramp
    (deep blue warm surface, white cold cloud tops) instead of grey. The
    signature grew by that one argument because a solar zenith cannot be
    derived from the radiances alone.

    Held in float32 throughout — the explicit casts keep a Python-float
    constant from silently promoting a full-disk grid back to float64.
    """
    red = np.clip(np.nan_to_num(c02), 0.0, 1.0).astype("float32")
    blue = np.clip(np.nan_to_num(c01), 0.0, 1.0).astype("float32")
    veggie = np.clip(np.nan_to_num(c03), 0.0, 1.0).astype("float32")
    green = np.clip(0.45 * red + 0.10 * veggie + 0.45 * blue, 0.0, 1.0).astype("float32")
    rgb = (np.stack([red, green, blue]) ** (1.0 / GAMMA)).astype("float32")
    # Night side: colder (higher cloud) -> whiter, warmer -> deep blue.
    kelvin = np.nan_to_num(c13, nan=IR_WARM_K)
    night = (
        1.0 - np.clip((kelvin - IR_COLD_K) / (IR_WARM_K - IR_COLD_K), 0.0, 1.0)
    ).astype("float32")
    # 0 = full day, 1 = full night, linear across the twilight band.
    alpha = np.clip(
        (np.asarray(zenith, dtype="float32") - np.float32(TWILIGHT_DAY_DEG))
        / np.float32(TWILIGHT_NIGHT_DEG - TWILIGHT_DAY_DEG),
        0.0,
        1.0,
    ).astype("float32")
    for band, (warm, cold) in enumerate(zip(NIGHT_WARM_RGB, NIGHT_COLD_RGB, strict=True)):
        tinted = (np.float32(warm) + night * np.float32(cold - warm)).astype("float32")
        rgb[band] = rgb[band] * (np.float32(1.0) - alpha) + tinted * alpha
    mask = np.isfinite(c02) & np.isfinite(c13)
    out = np.where(mask[None, :, :], rgb * np.float32(255.0), np.float32(0.0))
    return out.round().astype("uint8"), (mask * 255).astype("uint8")


def build_output_item(out_path, *, out_id, source_item, when, visual_filename):
    """The output item for `out_path`, footprinted from the SOURCE item.

    rio-stac reprojects the raster's bounds to WGS84 to make the geometry, and
    on the real geostationary grid GDAL refuses the whole call rather than
    dropping the corners that fall off the Earth's limb ("Full reprojection
    failed, but partial is possible if you define
    OGR_ENABLE_PARTIAL_REPROJECTION") — the failure the extractor's footprint()
    already works around. Two guards: that config option is set so a GDAL build
    which honours it drops the off-limb vertices instead of raising, and one
    that still refuses is retried against the file's OWN crs, an identity
    transform that cannot fail. Either way the geometry and bbox are replaced
    with the source item's — same pixel grid, already reprojected correctly by
    the extractor — so only `proj:*` and the asset survive from rio-stac.
    """
    import rasterio
    from rio_stac.stac import create_stac_item

    def stac_item(dataset, **extra):
        return create_stac_item(
            source=dataset,
            id=out_id,
            collection=OUTPUT_COLLECTION,
            input_datetime=when,
            asset_name="visual",
            asset_roles=["visual"],
            asset_media_type="image/tiff; application=geotiff; profile=cloud-optimized",
            asset_href=visual_filename,
            with_proj=True,
            with_raster=False,
            properties={
                "platform": source_item["properties"].get("platform"),
                "instruments": source_item["properties"].get("instruments"),
                "goes:derived_from": source_item["id"],
            },
            **extra,
        ).to_dict()

    geometry, bbox = source_item.get("geometry"), source_item.get("bbox")
    with rasterio.Env(OGR_ENABLE_PARTIAL_REPROJECTION=True), rasterio.open(out_path) as dataset:
        try:
            item = stac_item(dataset)
        except Exception:
            # Without a source footprint to substitute there is nothing to
            # fall back ON, so let the run fail rather than publish the
            # file's own metres as if they were degrees.
            if not (geometry and bbox):
                raise
            item = stac_item(dataset, geographic_crs=dataset.crs)

    item["properties"] = {k: v for k, v in item["properties"].items() if v is not None}
    if geometry:
        item["geometry"] = geometry
        if bbox:
            item["bbox"] = bbox
    item["links"] = []
    return item


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
    print(f"batch {manifest['batch_id']}: {len(manifest['items'])} item(s)")

    for entry in manifest["items"]:
        source_item = entry["item"]
        asset = next(iter(entry["assets"].values()))
        out_id = f"{source_item['id']}-geocolor"
        # One filename per output item: hrefs are plain siblings in the run
        # prefix, so a batch of more than one would overwrite a shared name.
        visual = f"{out_id}.tif"
        stamp = source_item["properties"]["datetime"].replace("Z", "+00:00")
        when = dt.datetime.fromisoformat(stamp)
        with tempfile.TemporaryDirectory() as tmp:
            nc_path = os.path.join(tmp, "scene.nc")
            s3.download_file(asset["bucket"], asset["key"], nc_path)
            bands = {name: read_band(nc_path, name) for name in BANDS}
            crs, transform = bands["CMI_C02"][1], bands["CMI_C02"][2]
            # The scan time, not "now": a run may be minutes behind the granule.
            zenith = solar_zenith(crs, transform, bands["CMI_C02"][0].shape, when)
            rgb, mask = compose(*(bands[name][0] for name in BANDS), zenith)

            out_path = os.path.join(tmp, visual)
            with rasterio.open(
                out_path,
                "w",
                driver="COG",
                width=rgb.shape[2],
                height=rgb.shape[1],
                count=3,
                dtype="uint8",
                crs=crs,
                transform=transform,
                compress="deflate",
                blocksize=512,
                overviews="AUTO",
            ) as dst:
                dst.write(rgb)
                dst.write_mask(mask)

            item = build_output_item(
                out_path,
                out_id=out_id,
                source_item=source_item,
                when=when,
                visual_filename=visual,
            )

            s3.upload_file(out_path, bucket, f"{prefix}{visual}")
        s3.put_object(
            Bucket=bucket,
            Key=f"{prefix}{out_id}.json",
            Body=json.dumps(item).encode(),
        )
        print(f"  -> {out_id} ({rgb.shape[2]}x{rgb.shape[1]})")


if __name__ == "__main__":
    main()
