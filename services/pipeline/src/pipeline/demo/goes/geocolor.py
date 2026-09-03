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
    band's CRS and transform. `name=None` opens `path` as a plain raster."""
    import rasterio

    source = path if name is None else f'NETCDF:"{path}":{name}'
    with rasterio.open(source) as src:
        raw = src.read(1).astype("float64")
        scale = src.scales[0] if src.scales and src.scales[0] else 1.0
        offset = src.offsets[0] if src.offsets and src.offsets[0] else 0.0
        values = raw * scale + offset
        if src.nodata is not None:
            values[raw == src.nodata] = np.nan
        return values, src.crs, src.transform


def compose(c01, c02, c03, c13):
    """uint8 RGB [3, h, w] + uint8 mask [h, w] (255 = on-disk)."""
    red = np.clip(np.nan_to_num(c02), 0.0, 1.0)
    blue = np.clip(np.nan_to_num(c01), 0.0, 1.0)
    veggie = np.clip(np.nan_to_num(c03), 0.0, 1.0)
    green = np.clip(0.45 * red + 0.10 * veggie + 0.45 * blue, 0.0, 1.0)
    rgb = np.stack([red, green, blue]) ** (1.0 / GAMMA)
    # Night side: colder (higher cloud) -> brighter. Daylight always wins the max.
    kelvin = np.nan_to_num(c13, nan=IR_WARM_K)
    night = 1.0 - np.clip((kelvin - IR_COLD_K) / (IR_WARM_K - IR_COLD_K), 0.0, 1.0)
    rgb = np.maximum(rgb, night[None, :, :])
    mask = np.isfinite(c02) & np.isfinite(c13)
    out = np.where(mask[None, :, :], rgb * 255.0, 0.0).round().astype("uint8")
    return out, (mask * 255).astype("uint8")


def main():
    import boto3
    import rasterio
    from rio_stac.stac import create_stac_item

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
            when = dt.datetime.fromisoformat(stamp)
            item = create_stac_item(
                source=out_path,
                id=out_id,
                collection=OUTPUT_COLLECTION,
                input_datetime=when,
                asset_name="visual",
                asset_roles=["visual"],
                asset_media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                asset_href=visual,
                with_proj=True,
                with_raster=False,
                properties={
                    "platform": source_item["properties"].get("platform"),
                    "instruments": source_item["properties"].get("instruments"),
                    "goes:derived_from": source_item["id"],
                },
            ).to_dict()
            item["properties"] = {
                k: v for k, v in item["properties"].items() if v is not None
            }
            item["links"] = []

            s3.upload_file(out_path, bucket, f"{prefix}{visual}")
        s3.put_object(
            Bucket=bucket,
            Key=f"{prefix}{out_id}.json",
            Body=json.dumps(item).encode(),
        )
        print(f"  -> {out_id} ({rgb.shape[2]}x{rgb.shape[1]})")


if __name__ == "__main__":
    main()
