"""goes-geocolor — the GOES worked example's PROCESS.

A "GeoColor-style" true-colour composite from ABI L2 MCMIPC (docs/processes.md
worked example; GOES spec §9): red = C02, blue = C01, a synthetic green from
C02/C03/C01 (the CIMSS recipe — no Rayleigh correction, no city lights), a
gamma of 2.2, and on the night side the C13 brightness temperature inverted
and blended in by per-pixel maximum, so cold cloud tops glow where there is no
sunlight. Written as a 3-band uint8 COG with an internal mask, in the file's
own geostationary CRS; the tile server reprojects on the fly.

Reads the staged netCDF from LOCAL DISK (the runtime image's netCDF driver
cannot open /vsi paths) and publishes one item with a `visual` asset through
the normal process output path.
"""

import datetime as dt
import json
import os
import tempfile

import numpy as np

OUTPUT_COLLECTION = "__OUTPUT_COLLECTION__"
BANDS = ("CMI_C01", "CMI_C02", "CMI_C03", "CMI_C13")
GAMMA = 2.2
IR_COLD_K, IR_WARM_K = 90.0, 313.0


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


def compose(c01, c02, c03, c13):
    """uint8 RGB [3, h, w] + uint8 mask [h, w] (255 = on-disk).

    Held in float32 throughout — the explicit casts keep a Python-float
    constant from silently promoting a full-disk grid back to float64.
    """
    red = np.clip(np.nan_to_num(c02), 0.0, 1.0).astype("float32")
    blue = np.clip(np.nan_to_num(c01), 0.0, 1.0).astype("float32")
    veggie = np.clip(np.nan_to_num(c03), 0.0, 1.0).astype("float32")
    green = np.clip(0.45 * red + 0.10 * veggie + 0.45 * blue, 0.0, 1.0).astype("float32")
    rgb = (np.stack([red, green, blue]) ** (1.0 / GAMMA)).astype("float32")
    # Night side: colder (higher cloud) -> brighter. Daylight always wins the max.
    kelvin = np.nan_to_num(c13, nan=IR_WARM_K)
    night = (
        1.0 - np.clip((kelvin - IR_COLD_K) / (IR_WARM_K - IR_COLD_K), 0.0, 1.0)
    ).astype("float32")
    rgb = np.maximum(rgb, night[None, :, :])
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
        with tempfile.TemporaryDirectory() as tmp:
            nc_path = os.path.join(tmp, "scene.nc")
            s3.download_file(asset["bucket"], asset["key"], nc_path)
            bands = {name: read_band(nc_path, name) for name in BANDS}
            crs, transform = bands["CMI_C02"][1], bands["CMI_C02"][2]
            rgb, mask = compose(*(bands[name][0] for name in BANDS))

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

            stamp = source_item["properties"]["datetime"].replace("Z", "+00:00")
            item = build_output_item(
                out_path,
                out_id=out_id,
                source_item=source_item,
                when=dt.datetime.fromisoformat(stamp),
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
