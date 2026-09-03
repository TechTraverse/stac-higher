# G-7 · GOES Worked Example + Live-Gated E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The whole GOES loop — anonymous NODD → extractor-fixed source items → GeoColor COG process → tiles → delivery — exists as checked-in, runnable code (`pipeline.demo goes`), as the operator-facing worked example in `docs/processes.md`, and as a Playwright spec that proves it against the live bucket when `E2E_LIVE_NODD=1`.

**Architecture:** Two ordinary process scripts live in `services/pipeline/src/pipeline/demo/goes/` as plain `.py` files with their logic in functions and a `main()` under `__main__`, so pytest can exercise the parsing, footprint and compositing without a container, and the runtime image's entrypoint (which `exec`s the source with `__name__ == "__main__"`) runs them unchanged. Both download their staged netCDF to local disk first — GDAL's netCDF driver cannot read `/vsi` paths in the runtime image (spec §15). A `pipeline.demo goes-seed` subcommand seeds the loop from those files through the same direct-SQL split the demo already uses; the e2e reads the same files and drives everything through the product's API, polling the STAC API, the tile server, the deliveries API and MinIO for the four gate outcomes.

**Tech Stack:** Python 3.12 (rasterio 1.5 / GDAL 3.12 with netCDF+COG drivers, numpy, rio-stac 0.12, boto3 — all already in `stac-higher-process-runtime:local`), pytest + ruff; Playwright (`app/e2e`), `@aws-sdk/client-s3` (already an app dependency); docker-compose `minio-init`.

**Spec:** `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` §2, §9, §10, §15.

## Global Constraints

- Worktree off `ai/main` **after G-6 is merged**: `git worktree add .claude/worktrees/goes-g7 -b ai/goes-g7 ai/main`; `npm install`; `cd services/pipeline && uv sync --extra dev`.
- Gates for the implementer: `npm run verify`, `uv run pytest`, `uv run ruff check .`. No Docker, no dev server, no internet in tests: the unit tests use synthetic rasters; the live spec is skipped without `E2E_LIVE_NODD=1`. The lead runs the live gate (Task 7).
- The scripts are the single source of truth for the GOES code: the demo seed, the e2e and the docs all read/quote `extractor.py` and `geocolor.py`. No second copy anywhere.
- Both scripts obey `docs/processes.md`: outputs only under `STAC_HIGHER_OUTPUT_PREFIX`; the extractor keeps id/collection/asset set/hrefs; the process's output item uses a relative `visual.tif` href and names the output collection through the `__OUTPUT_COLLECTION__` token the seeder substitutes.
- NODD is listed over plain HTTPS in the e2e (`https://noaa-goes19.s3.amazonaws.com/?list-type=2&prefix=…`), never through the app.
- `include` is a full-path glob relative to `source_path` (`**/<filename>`), never the bare filename.
- Commit messages end with the session's attribution trailer.

---

### Task 1: The two GOES scripts, testable without a container

**Files:**
- Create: `services/pipeline/src/pipeline/demo/goes/__init__.py`, `services/pipeline/src/pipeline/demo/goes/extractor.py`, `services/pipeline/src/pipeline/demo/goes/geocolor.py`
- Test: create `services/pipeline/tests/test_demo_goes.py`

**Interfaces:**
- Produces (package `pipeline.demo.goes`): `extractor_code() -> str`, `geocolor_code(output_collection: str) -> str` (token `__OUTPUT_COLLECTION__` substituted), `OUTPUT_COLLECTION_TOKEN = "__OUTPUT_COLLECTION__"`; script-level functions `scan_time(filename) -> datetime`, `footprint(dataset, samples=25) -> (geometry, bbox)` in `extractor.py`; `read_band(path, name) -> (values, crs, transform)`, `compose(c01, c02, c03, c13) -> (rgb_uint8[3,h,w], mask_uint8[h,w])` in `geocolor.py`.

- [ ] **Step 1: Write the failing tests**

`services/pipeline/tests/test_demo_goes.py`:

```python
"""The GOES worked example's scripts (G-7, spec §9), tested as plain modules.

The runtime entrypoint exec()s each script with __name__ == "__main__"; here
they are exec'd with another name so their functions can be called on
synthetic data. No network, no container, no netCDF file: the footprint is
checked on a GeoTIFF in the geostationary CRS, which is the property that
made I-101 — a projection the built-in path cannot footprint.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from pipeline.demo.goes import OUTPUT_COLLECTION_TOKEN, extractor_code, geocolor_code

GEOS_CRS = "+proj=geos +lon_0=-75 +h=35786023 +x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs +sweep=x"


def _load(source: str) -> dict:
    ns: dict = {"__name__": "test_demo_goes"}
    exec(compile(source, "<goes>", "exec"), ns)
    return ns


@pytest.fixture(scope="module")
def extractor():
    return _load(extractor_code())


@pytest.fixture(scope="module")
def geocolor():
    return _load(geocolor_code("goes-geocolor"))


def test_scripts_compile_and_never_open_vsi_paths():
    for source in (extractor_code(), geocolor_code("x")):
        compile(source, "<goes>", "exec")
        # Spec §15: the runtime image's netCDF driver cannot read /vsi paths.
        assert "/vsis3" not in source and "/vsicurl" not in source
    assert OUTPUT_COLLECTION_TOKEN not in geocolor_code("goes-geocolor")
    assert '"goes-geocolor"' in geocolor_code("goes-geocolor")


def test_scan_time_parses_the_filename_token(extractor):
    when = extractor["scan_time"](
        "OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc"
    )
    assert when == dt.datetime(2026, 9, 3, 4, 1, 17, 200_000, tzinfo=dt.UTC)
    with pytest.raises(ValueError):
        extractor["scan_time"]("scene.nc")


def test_footprint_reprojects_a_geostationary_dataset(extractor):
    # A 10x10 tile of 2 km pixels near the sub-satellite point: every corner
    # is on-disk, so the polygon is closed, finite, and around lon -75.
    transform = from_origin(-10_000.0, 10_000.0, 2_000.0, 2_000.0)
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=10, height=10, count=1, dtype="uint16",
                      crs=GEOS_CRS, transform=transform) as ds:
            ds.write(np.zeros((1, 10, 10), dtype="uint16"))
        with mem.open() as ds:
            geometry, bbox = extractor["footprint"](ds)
    ring = geometry["coordinates"][0]
    assert geometry["type"] == "Polygon" and ring[0] == ring[-1] and len(ring) > 20
    assert all(math.isfinite(x) and math.isfinite(y) for x, y in ring)
    assert bbox[0] < -75 < bbox[2] and bbox[1] < 0 < bbox[3]
    assert abs(bbox[0] - bbox[2]) < 1 and abs(bbox[1] - bbox[3]) < 1


def test_compose_daylight_night_and_fill(geocolor):
    # pixel 0: bright day cloud; pixel 1: clear night land (warm); pixel 2:
    # night cold cloud top; pixel 3: fill (off-disk).
    c01 = np.array([[0.8, 0.0, 0.0, np.nan]])
    c02 = np.array([[0.9, 0.0, 0.0, np.nan]])
    c03 = np.array([[0.7, 0.0, 0.0, np.nan]])
    c13 = np.array([[220.0, 300.0, 200.0, np.nan]])
    rgb, mask = geocolor["compose"](c01, c02, c03, c13)
    assert rgb.shape == (3, 1, 4) and rgb.dtype == np.uint8 and mask.dtype == np.uint8
    assert rgb[0, 0, 0] > 200                       # day: red from C02 after gamma
    assert 0 < rgb[0, 0, 1] < 40                    # warm night surface: faint IR
    assert rgb[0, 0, 2] > rgb[0, 0, 1]              # cold cloud brighter than warm land at night
    assert (rgb[:, 0, 3] == 0).all() and mask[0, 3] == 0   # fill: black + masked
    assert (mask[0, :3] == 255).all()


def test_read_band_applies_scale_offset_and_fill(geocolor, tmp_path):
    # A scaled uint16 GeoTIFF stands in for a CMI subdataset.
    transform = from_origin(0, 0, 1, 1)
    path = tmp_path / "band.tif"
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=2, height=1, count=1, dtype="uint16",
                      crs=GEOS_CRS, transform=transform, nodata=65535) as ds:
            ds.write(np.array([[[1000, 65535]]], dtype="uint16"))
            ds.scales = (0.001,)
            ds.offsets = (0.0,)
        path.write_bytes(mem.read())
    values, crs, tr = geocolor["read_band"](str(path), None)
    assert values[0, 0] == pytest.approx(1.0) and np.isnan(values[0, 1])
    assert crs is not None and tr == transform
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_demo_goes.py -q`
Expected: FAIL — `pipeline.demo.goes` does not exist.

- [ ] **Step 3: Implement the package and the extractor**

`services/pipeline/src/pipeline/demo/goes/__init__.py`:

```python
"""The GOES worked example (GOES spec §9, G-7): an extractor and a process.

The two scripts next to this file are ORDINARY process code — written
against `docs/processes.md`, nothing privileged — and are the single source
of truth: `pipeline.demo goes-seed` deploys them, `app/e2e/goes-loop.spec.ts`
deploys them, and the docs quote them. Loaded as text here because that is
what a revision carries.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent

#: The geocolor script names its output collection through this token, which
#: the seeder substitutes — a revision's code is a string, not a module.
OUTPUT_COLLECTION_TOKEN = "__OUTPUT_COLLECTION__"


def extractor_code() -> str:
    return (_HERE / "extractor.py").read_text()


def geocolor_code(output_collection: str) -> str:
    return (_HERE / "geocolor.py").read_text().replace(OUTPUT_COLLECTION_TOKEN, output_collection)
```

`services/pipeline/src/pipeline/demo/goes/extractor.py`:

```python
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
    base = dt.datetime.strptime(digits[:13], "%Y%j%H%M%S").replace(tzinfo=dt.timezone.utc)
    return base + dt.timedelta(milliseconds=100 * int(digits[13]))


def footprint(dataset, samples=25):
    """The dataset's bounds as a WGS84 polygon + bbox.

    The edges are densified before reprojecting so the curved limb of a
    geostationary scene is followed rather than cut by four straight lines;
    vertices that fall off the Earth's disk reproject to non-finite values
    and are dropped.
    """
    from rasterio.warp import transform

    left, bottom, right, top = dataset.bounds
    xs = [left + (right - left) * i / samples for i in range(samples + 1)]
    ys = [bottom + (top - bottom) * i / samples for i in range(samples + 1)]
    ring = (
        [(x, bottom) for x in xs]            # south edge, west -> east
        + [(right, y) for y in ys[1:]]       # east edge, south -> north
        + [(x, top) for x in xs[-2::-1]]     # north edge, east -> west
        + [(left, y) for y in ys[-2:0:-1]]   # west edge, north -> south
    )
    lons, lats = transform(dataset.crs, "EPSG:4326", [p[0] for p in ring], [p[1] for p in ring])
    coords = [[x, y] for x, y in zip(lons, lats) if math.isfinite(x) and math.isfinite(y)]
    if len(coords) < 3:
        raise ValueError("footprint has fewer than three on-disk vertices")
    coords.append(list(coords[0]))
    bbox = [
        min(c[0] for c in coords), min(c[1] for c in coords),
        max(c[0] for c in coords), max(c[1] for c in coords),
    ]
    return {"type": "Polygon", "coordinates": [coords]}, bbox


def main():
    import boto3
    import rasterio

    s3 = boto3.client("s3")
    bucket = os.environ["STAC_HIGHER_OUTPUT_BUCKET"]
    prefix = os.environ["STAC_HIGHER_OUTPUT_PREFIX"]
    manifest = json.loads(
        s3.get_object(Bucket=bucket, Key=os.environ["STAC_HIGHER_INPUT_MANIFEST"])["Body"].read()
    )
    print(f"batch {manifest['batch_id']}: {len(manifest['items'])} draft(s), kind={manifest['kind']}")

    for entry in manifest["items"]:
        item = entry["item"]
        asset_key, asset = next(iter(entry["assets"].items()))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "scene.nc")
            s3.download_file(asset["bucket"], asset["key"], path)
            with rasterio.open(path) as container:
                tags = container.tags()
            with rasterio.open(f'NETCDF:"{path}":CMI_C02') as band:
                geometry, bbox = footprint(band)

        when = scan_time(asset_key)
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

        s3.put_object(Bucket=bucket, Key=f"{prefix}{item['id']}.json", Body=json.dumps(item).encode())
        print(f"  {item['id']}: datetime={props['datetime']} bbox={[round(v, 2) for v in bbox]}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement the process**

`services/pipeline/src/pipeline/demo/goes/geocolor.py`:

```python
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
    night = 1.0 - np.clip((np.nan_to_num(c13, nan=IR_WARM_K) - IR_COLD_K) / (IR_WARM_K - IR_COLD_K), 0.0, 1.0)
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
        s3.get_object(Bucket=bucket, Key=os.environ["STAC_HIGHER_INPUT_MANIFEST"])["Body"].read()
    )
    print(f"batch {manifest['batch_id']}: {len(manifest['items'])} item(s)")

    for entry in manifest["items"]:
        source_item = entry["item"]
        asset = next(iter(entry["assets"].values()))
        with tempfile.TemporaryDirectory() as tmp:
            nc_path = os.path.join(tmp, "scene.nc")
            s3.download_file(asset["bucket"], asset["key"], nc_path)
            bands = {name: read_band(nc_path, name) for name in BANDS}
            crs, transform = bands["CMI_C02"][1], bands["CMI_C02"][2]
            rgb, mask = compose(*(bands[name][0] for name in BANDS))

            out_path = os.path.join(tmp, "visual.tif")
            with rasterio.open(
                out_path, "w", driver="COG", width=rgb.shape[2], height=rgb.shape[1], count=3,
                dtype="uint8", crs=crs, transform=transform,
                compress="deflate", blocksize=512, overviews="AUTO",
            ) as dst:
                dst.write(rgb)
                dst.write_mask(mask)

            out_id = f"{source_item['id']}-geocolor"
            when = dt.datetime.fromisoformat(source_item["properties"]["datetime"].replace("Z", "+00:00"))
            item = create_stac_item(
                source=out_path,
                id=out_id,
                collection=OUTPUT_COLLECTION,
                input_datetime=when,
                asset_name="visual",
                asset_roles=["visual"],
                asset_media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                asset_href="visual.tif",
                with_proj=True,
                with_raster=False,
                properties={
                    "platform": source_item["properties"].get("platform"),
                    "instruments": source_item["properties"].get("instruments"),
                    "goes:derived_from": source_item["id"],
                },
            ).to_dict()
            item["properties"] = {k: v for k, v in item["properties"].items() if v is not None}
            item["links"] = []

            s3.upload_file(out_path, bucket, f"{prefix}visual.tif")
        s3.put_object(Bucket=bucket, Key=f"{prefix}{out_id}.json", Body=json.dumps(item).encode())
        print(f"  -> {out_id} ({rgb.shape[2]}x{rgb.shape[1]})")


if __name__ == "__main__":
    main()
```

If `rio_stac.stac.create_stac_item` in 0.12 does not accept `asset_href`, set `item["assets"]["visual"]["href"] = "visual.tif"` after `.to_dict()` instead — check `uv run python -c "import inspect, rio_stac.stac as s; print(inspect.signature(s.create_stac_item))"`.

- [ ] **Step 5: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_demo_goes.py -q && uv run ruff check .`
Expected: PASS. (ruff: the scripts are shipped source, so keep them lint-clean; if ruff flags the `exec`-style top-level imports inside `main`, they are deliberate — the runtime image has boto3/rasterio, the pipeline's test env may not need them at import.)

```bash
git add services/pipeline/src/pipeline/demo/goes services/pipeline/tests/test_demo_goes.py
git commit -m "feat(demo): the GOES worked example — goes-abi-metadata extractor and goes-geocolor process (G-7)"
```

---

### Task 2: `pipeline.demo goes-seed | goes-status | goes-teardown`

**Files:**
- Create: `services/pipeline/src/pipeline/demo/platform.py` (generic row writers), `services/pipeline/src/pipeline/demo/goes/seed.py`
- Modify: `services/pipeline/src/pipeline/demo/__main__.py` (`_install_process` delegates; three subcommands), `services/pipeline/src/pipeline/demo/README.md`
- Test: `services/pipeline/tests/test_demo_goes.py` (append)

**Interfaces:**
- Produces (`demo/platform.py`): `install_process(conn, *, process_id, revision_id, name, description, group, created_by, kind, max_runs_per_hour, runtime, code, sources=(), outputs=())`, `remove_process(conn, process_id)`, `upsert_connection(cur_or_conn, *, name, protocol, config, credentials: bytes | None, group, created_by) -> uuid`, `upsert_association(conn, *, collection_id, connection_id, direction, config, created_by) -> uuid`, `enable_serving(conn, collection_id, group)`.
- Produces (`demo/goes/seed.py`): constants `SOURCE_COLLECTION = "goes-abi-mcmipc"`, `OUTPUT_COLLECTION = "goes-geocolor"`, `NODD_BUCKET = "noaa-goes19"`, `PRODUCT_PREFIX = "ABI-L2-MCMIPC/"`, `CONNECTION_NAME = "goes-nodd"`, `DEST_CONNECTION_NAME = "goes-geocolor-dest"`, `DEST_BUCKET = "stac-higher-deliveries"`, `EXTRACTOR_ID = "60e50000-0000-4000-8000-000000000001"`, `EXTRACTOR_REVISION_ID = "…0002"`, `GEOCOLOR_ID = "…0003"`, `GEOCOLOR_REVISION_ID = "…0004"`; `nodd_connection_config()`, `ingest_config(*, include=(), window_begin="-1h", max_files_per_poll=2, extractor_id=EXTRACTOR_ID)`, `deliver_config()`, `collection_documents()`; `seed(args)`, `status(args)`, `teardown(args)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_demo_goes.py`:

```python
def test_goes_ingest_config_round_trips_both_readers():
    from pipeline.demo.goes.seed import EXTRACTOR_ID, ingest_config
    from pipeline.ingest.config import parse_ingest_config
    from pipeline.ingest.extract import parse_metadata

    cfg = ingest_config(include=("**/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172*.nc",))
    parsed = parse_ingest_config(cfg)
    assert parsed.storage_mode == "reference"
    assert parsed.path_template == "{Y}/{j}/{H}/" and parsed.window is not None
    assert parsed.max_files_per_poll == 2
    assert parse_metadata(parsed.metadata).extractor_process_id == EXTRACTOR_ID


def test_goes_connection_config_is_anonymous():
    from pipeline.connections.adapters.s3 import parse_s3_config  # or the module's parser name
    from pipeline.demo.goes.seed import nodd_connection_config

    cfg = nodd_connection_config()
    assert cfg["anonymous"] is True and cfg["bucket"] == "noaa-goes19"
    parse_s3_config(cfg)  # the adapter accepts it without credentials
```

(Find the s3 adapter's config parser name with `grep -n "^def \|^class " services/pipeline/src/pipeline/connections/adapters/s3.py` and use it.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_demo_goes.py -q -k goes_`
Expected: FAIL — no `pipeline.demo.goes.seed`.

- [ ] **Step 3: Implement the generic writers**

`services/pipeline/src/pipeline/demo/platform.py` — lift the SQL out of `demo/__main__.py::_install_process` and `loadgen/__main__.py::_upsert_connection/_upsert_association` into module functions with the signatures above. `install_process` inserts the process row **with `kind`** (`INSERT … (id, name, description, group_id, kind, enabled, max_runs_per_hour, created_by) … ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description, enabled = true, deleted_at = NULL`), nulls `current_revision`, deletes the process's runs and revisions, inserts the revision, repoints, then inserts each `(collection_id, trigger)` in `sources` and each collection in `outputs` idempotently (the existing `WHERE NOT EXISTS` statements). `remove_process` is the existing teardown sequence for one id. `upsert_connection` accepts `credentials=None` (anonymous). `upsert_association` matches on `(collection_id, connection_id, direction)`. Docstring on the module: "The demo and the GOES seed write platform rows directly (the loadgen split: pgstac through the API, `stac_higher` through SQL); the app owns the DDL (ADR 0001) and nothing here creates or alters a table."

In `demo/__main__.py`, `_install_process(conn)` becomes a call to `install_process(conn, process_id=PROCESS_ID, …, kind="transform", sources=((SOURCE_COLLECTION, TRIGGER),), outputs=(OUTPUT_COLLECTION,))`; `teardown` calls `remove_process`; `_enable_serving` delegates to `enable_serving`. The existing demo behaviour must not change.

- [ ] **Step 4: Implement the GOES seed**

`services/pipeline/src/pipeline/demo/goes/seed.py`:

```python
"""`python -m pipeline.demo goes-seed` — the GOES loop on a local stack,
against the LIVE NODD bucket (the manual recipe for spec §10's "full hour").

    goes-abi-mcmipc   <- anonymous s3 connection to noaa-goes19, reference
                          mode, the last hour of ABI-L2-MCMIPC, at most two
                          new files per poll, EXTRACTED by goes-abi-metadata
    goes-geocolor     <- the goes-geocolor process, triggered per source item
                          (optionally delivered to a MinIO bucket, --deliver)

Needs the internet, the process runtime image, and — for --deliver —
CREDENTIALS_MASTER_KEY in the environment (the delivery connection's MinIO
credentials are sealed with it, exactly as pipeline.loadgen does).
"""

from __future__ import annotations

import argparse
import json
import os

import psycopg

from pipeline.connections.envelope import load_master_key, seal
from pipeline.demo.goes import extractor_code, geocolor_code
from pipeline.demo.platform import (
    enable_serving,
    install_process,
    remove_process,
    upsert_association,
    upsert_connection,
)

GROUP = "earth-observation"
CREATED_BY = "goes-seed"
SOURCE_COLLECTION = "goes-abi-mcmipc"
OUTPUT_COLLECTION = "goes-geocolor"
NODD_BUCKET = "noaa-goes19"
PRODUCT_PREFIX = "ABI-L2-MCMIPC/"
CONNECTION_NAME = "goes-nodd"
DEST_CONNECTION_NAME = "goes-geocolor-dest"
DEST_BUCKET = "stac-higher-deliveries"
EXTRACTOR_ID = "60e50000-0000-4000-8000-000000000001"
EXTRACTOR_REVISION_ID = "60e50000-0000-4000-8000-000000000002"
GEOCOLOR_ID = "60e50000-0000-4000-8000-000000000003"
GEOCOLOR_REVISION_ID = "60e50000-0000-4000-8000-000000000004"

EXTRACTOR_RUNTIME = {
    "kind": "inline_python", "image": None, "memory_mb": 1024, "timeout_seconds": 120,
    "retry": {"max_attempts": 2, "backoff": "exponential"},
    "network": {"level": "isolated", "hosts": []},
}
GEOCOLOR_RUNTIME = {
    "kind": "inline_python", "image": None, "memory_mb": 2048, "timeout_seconds": 600,
    "retry": {"max_attempts": 2, "backoff": "exponential"},
    "network": {"level": "isolated", "hosts": []},
}
TRIGGER = {"kind": "item_event", "item_filter": None}


def nodd_connection_config() -> dict:
    return {"bucket": NODD_BUCKET, "region": "us-east-1", "anonymous": True}


def ingest_config(*, include=(), window_begin="-1h", max_files_per_poll=2, extractor_id=EXTRACTOR_ID) -> dict:
    return {
        "source_path": PRODUCT_PREFIX,
        "include": list(include),
        "exclude": [],
        "poll_frequency_seconds": 60,
        "storage_mode": "reference",
        "path_template": "{Y}/{j}/{H}/",
        "window": {"begin": window_begin, "end": None},
        "max_files_per_poll": max_files_per_poll,
        "grouping": {"rule": "none", "timeout_seconds": 900, "on_timeout": "ingest_partial"},
        "metadata": {"strategy": "extractor", "extractor": {"process_id": extractor_id}},
        "post_ingest": "leave",
    }


def deliver_config() -> dict:
    return {
        "path_template": "goes/{item_id}/{filename}",
        "item_filter": None, "asset_keys": None,
        "payload": {"item_json": False, "checksums": None, "completion_marker": False},
        "on_update": "redeliver", "overwrite": "if_newer",
        "retry": {"max_attempts": 5, "backoff": "exponential"},
        "max_concurrent_transfers": 4,
    }


def collection_documents() -> list[dict]:
    def doc(cid, description):
        return {
            "type": "Collection", "stac_version": "1.0.0", "id": cid, "description": description,
            "license": "proprietary",
            "extent": {"spatial": {"bbox": [[-152.0, 14.0, -52.0, 57.0]]},
                       "temporal": {"interval": [["2025-04-07T00:00:00Z", None]]}},
            "links": [],
        }
    return [
        doc(SOURCE_COLLECTION, "GOES-19 ABI L2 CONUS multi-band CMI, catalogued in place from NODD."),
        doc(OUTPUT_COLLECTION, "GeoColor-style true-colour COGs derived by the goes-geocolor process."),
    ]
```

then `seed(args)`: reuse `_check_migrations`, `_put_collection`, `_s3`, `_say` from `demo/__main__.py` (import them; if that creates a cycle, move those four helpers into `demo/platform.py` too). Steps: check migration `027_extractors`; PUT both collections; with one autocommit connection: `enable_serving` on both; `install_process` for the extractor (`kind="extractor"`, 600 runs/h, `extractor_code()`) and the geocolor process (`kind="transform"`, 120 runs/h, `geocolor_code(OUTPUT_COLLECTION)`, `sources=((SOURCE_COLLECTION, TRIGGER),)`, `outputs=(OUTPUT_COLLECTION,)`); refuse (SystemExit with the ids) if another ENABLED ingest association exists on `SOURCE_COLLECTION` whose connection is not `CONNECTION_NAME` — "disable it first, or it will ingest the same files twice"; `upsert_connection` (anonymous, `credentials=None`) and `upsert_association` (ingest, `ingest_config(include=args.include, window_begin=args.window, max_files_per_poll=args.max_files)`); when `args.deliver`: `ensure_bucket` on `DEST_BUCKET` via `_s3(args.s3_endpoint)`, `seal` MinIO credentials with `load_master_key(dict(os.environ))`, `upsert_connection` for the destination (`{bucket: DEST_BUCKET, region: "us-east-1", endpoint: args.internal_s3_endpoint, force_path_style: True}`), `upsert_association` (deliver). Print the UI URLs at the end. `status(args)`: the demo's run table for BOTH process ids plus `ingest_files` counts by status for the association and item counts for both collections. `teardown(args)`: delete `ingest_files` for the association, both associations, both connections, `remove_process` ×2, `collection_settings` rows, both collections through the STAC API, and `assets/goes-geocolor/` objects.

`demo/__main__.py` `main()`: three subparsers `goes-seed` (`--include` repeatable, `--window` default `-1h`, `--max-files` default 2, `--deliver`, `--internal-s3-endpoint` default `http://minio:9000`), `goes-status`, `goes-teardown`, each `set_defaults(func=…)` to the functions in `demo/goes/seed.py`. Update the module docstring's command list.

`demo/README.md`: a "GOES loop (`goes-seed`)" section: what it builds (the diagram above), preconditions (internet, the runtime image, `CREDENTIALS_MASTER_KEY` for `--deliver`, migration 027), the three commands, the refusal about a second ingest association, and where to look (both collections, both processes, the tiler URL, `goes-status`). Note it is the manual "full hour" recipe spec §10 names, and that the e2e is the automated one-file version.

- [ ] **Step 5: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_demo_goes.py tests/test_loadgen.py -q && uv run ruff check . && uv run python -m pipeline.demo --help | grep goes`
Expected: PASS; the three subcommands listed.

```bash
git add services/pipeline/src/pipeline/demo
git commit -m "feat(demo): pipeline.demo goes-seed/status/teardown — the GOES loop against live NODD (G-7)"
```

---

### Task 3: Compose — the delivery bucket

**Files:**
- Modify: `docker-compose.yml:183-195` (`minio-init`)
- Modify: `docs/serving.md` or `docs/push-ingest.md`? — no; `docs/connections.md` gets one line under its MinIO/s3 example (grep `minioadmin`)

- [ ] **Step 1: Implement**

In `minio-init`'s script, after `mc mb --ignore-existing local/stac-higher` add `&&` + `mc mb --ignore-existing local/stac-higher-deliveries`, with a comment above the service: `# stac-higher-deliveries: the destination bucket the GOES e2e and \`pipeline.demo goes-seed --deliver\` deliver into (G-7).`

`docs/connections.md`: under the s3/MinIO example add: "The compose stack also creates `stac-higher-deliveries`, an empty bucket for delivery tests; a delivery connection to it uses endpoint `http://minio:9000` (the pipeline's view), `force_path_style: true`, and the MinIO root credentials."

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml docs/connections.md
git commit -m "chore(compose): stac-higher-deliveries bucket for the GOES delivery leg (G-7)"
```

---

### Task 4: The live-gated e2e — `app/e2e/goes-loop.spec.ts`

**Files:**
- Create: `app/e2e/goes-loop.spec.ts`
- Reference (read first): `app/e2e/data-flow.spec.ts` (fixture-through-API idiom), `app/e2e/collection-settings.spec.ts` (the settings PUT payload), `app/src/lib/associations/deliveries.ts` (the listing shape — `listDeliveries` returns the object the route serves; use its top-level key for the array, likely `deliveries`), `app/src/lib/serving/urls.ts`.

**Interfaces:**
- Consumes: the API routes in AGENTS.md; `pipeline/demo/goes/{extractor,geocolor}.py` read from disk; NODD over HTTPS; the STAC API at `http://localhost:8082`; the tiler at `http://localhost:8084`; MinIO at `http://localhost:9000`.

- [ ] **Step 1: Write the spec**

```ts
import { test, expect, type APIRequestContext } from "@playwright/test";
import { HeadObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * GOES loop, live (G-7, spec §2). Skipped unless E2E_LIVE_NODD=1: it needs
 * the internet (NODD), the full Docker stack with the pipeline and the
 * process runtime image, and ~5 minutes. Everything is created through the
 * product's API with unique names and removed afterwards.
 *
 * Four gate outcomes, polled with a 10-minute bound:
 *   1. the source item exists with the scan time as datetime and goes:* properties
 *   2. the output item exists with a `visual` COG asset
 *   3. a tile for it renders through the tile server
 *   4. the COG is delivered to the second MinIO bucket
 */
const LIVE = process.env.E2E_LIVE_NODD === "1";
const STAC = process.env.E2E_STAC_URL ?? "http://localhost:8082";
const TILER = process.env.E2E_TITILER_URL ?? "http://localhost:8084";
const MINIO = process.env.E2E_S3_ENDPOINT ?? "http://localhost:9000";
const GROUP = "earth-observation";
const RUN = Date.now().toString(36);
const SRC = `e2e-goes-src-${RUN}`;
const OUT = `e2e-goes-out-${RUN}`;
const NODD_CONN = `e2e-goes-nodd-${RUN}`;
const DEST_CONN = `e2e-goes-dest-${RUN}`;
const DEST_BUCKET = "stac-higher-deliveries";
const GATE_MS = 10 * 60_000;

const GOES_DIR = resolve(__dirname, "../../services/pipeline/src/pipeline/demo/goes");
const EXTRACTOR_CODE = readFileSync(resolve(GOES_DIR, "extractor.py"), "utf8");
const GEOCOLOR_CODE = readFileSync(resolve(GOES_DIR, "geocolor.py"), "utf8").replaceAll(
  "__OUTPUT_COLLECTION__",
  OUT,
);

const collection = (id: string, description: string) => ({
  type: "Collection", stac_version: "1.0.0", id, description, license: "proprietary",
  extent: { spatial: { bbox: [[-152, 14, -52, 57]] }, temporal: { interval: [["2025-04-07T00:00:00Z", null]] } },
  links: [],
});

const runtime = (timeout_seconds: number, memory_mb: number) => ({
  kind: "inline_python", image: null, memory_mb, timeout_seconds,
  retry: { max_attempts: 2, backoff: "exponential" },
  network: { level: "isolated", hosts: [] },
});

/** The newest MCMIPC key in the current UTC hour (or the previous one when
 * the hour has just begun), listed over plain HTTPS — never through the app. */
async function newestMcmipcKey(request: APIRequestContext): Promise<string> {
  const now = new Date();
  for (const back of [0, 1]) {
    const t = new Date(now.getTime() - back * 3_600_000);
    const start = Date.UTC(t.getUTCFullYear(), 0, 0);
    const doy = String(Math.floor((t.getTime() - start) / 86_400_000)).padStart(3, "0");
    const prefix = `ABI-L2-MCMIPC/${t.getUTCFullYear()}/${doy}/${String(t.getUTCHours()).padStart(2, "0")}/`;
    const res = await request.get(
      `https://noaa-goes19.s3.amazonaws.com/?list-type=2&prefix=${encodeURIComponent(prefix)}`,
    );
    expect(res.ok()).toBeTruthy();
    const keys = [...(await res.text()).matchAll(/<Key>([^<]+)<\/Key>/g)].map((m) => m[1]);
    if (keys.length) return keys[keys.length - 1];
  }
  throw new Error("NODD listed no MCMIPC keys for the current or previous hour");
}

function scanTimeOf(filename: string): string {
  const d = /_s(\d{14})/.exec(filename)![1];
  const base = new Date(Date.UTC(Number(d.slice(0, 4)), 0, 1));
  base.setUTCDate(Number(d.slice(4, 7)));
  base.setUTCHours(Number(d.slice(7, 9)), Number(d.slice(9, 11)), Number(d.slice(11, 13)), Number(d[13]) * 100);
  return base.toISOString().replace(".000Z", "Z").replace(/\.(\d)00Z$/, ".$1Z");
}

async function poll<T>(label: string, fn: () => Promise<T | null>, everyMs = 10_000): Promise<T> {
  const deadline = Date.now() + GATE_MS;
  while (Date.now() < deadline) {
    const v = await fn();
    if (v !== null) return v;
    await new Promise((r) => setTimeout(r, everyMs));
  }
  throw new Error(`timed out after ${GATE_MS / 1000}s waiting for: ${label}`);
}

async function json<T = any>(res: { ok(): boolean; json(): Promise<any>; status(): number; text(): Promise<string> }, what: string): Promise<T> {
  if (!res.ok()) throw new Error(`${what}: ${res.status()} ${await res.text()}`);
  return res.json();
}

test.describe("GOES loop against live NODD", () => {
  test.skip(!LIVE, "set E2E_LIVE_NODD=1 (needs internet, Docker stack, pipeline, runtime image)");
  test.describe.configure({ timeout: GATE_MS + 5 * 60_000 });

  const ids = { extractor: "", geocolor: "", nodd: "", dest: "", ingest: "", deliver: "" };

  test.afterAll(async ({ request }) => {
    // Order matters: an extractor named by an association refuses deletion.
    if (ids.ingest) await request.delete(`/api/collections/${SRC}/connections/${ids.ingest}`);
    if (ids.deliver) await request.delete(`/api/collections/${OUT}/connections/${ids.deliver}`);
    for (const p of [ids.geocolor, ids.extractor]) if (p) await request.delete(`/api/processes/${p}`);
    for (const c of [ids.nodd, ids.dest]) if (c) await request.delete(`/api/connections/${c}`);
    for (const c of [OUT, SRC]) await request.delete(`/api/catalog/collections/${c}`);
  });

  test("ingests one file, extracts, composes, tiles and delivers", async ({ request }) => {
    const key = await newestMcmipcKey(request);
    const filename = key.split("/").pop()!;
    const expectedDatetime = scanTimeOf(filename);

    // --- wiring, all through the product API -------------------------------
    for (const [id, d] of [[SRC, "GOES e2e source"], [OUT, "GOES e2e output"]] as const) {
      await json(await request.post("/api/catalog/collections", { data: collection(id, d) }), `collection ${id}`);
    }
    ids.nodd = (await json(await request.post("/api/connections", { data: {
      name: NODD_CONN, protocol: "s3", group_id: GROUP,
      config: { bucket: "noaa-goes19", region: "us-east-1", anonymous: true },
    } }), "nodd connection")).id;

    ids.extractor = (await json(await request.post("/api/processes", { data: {
      name: `goes-abi-metadata-${RUN}`, description: "GOES e2e extractor", group_id: GROUP,
      kind: "extractor", enabled: true, max_runs_per_hour: 600,
    } }), "extractor")).id;
    await json(await request.post(`/api/processes/${ids.extractor}/revisions`, { data: {
      runtime: runtime(120, 1024), code: EXTRACTOR_CODE, env: [],
    } }), "extractor deploy");

    ids.geocolor = (await json(await request.post("/api/processes", { data: {
      name: `goes-geocolor-${RUN}`, description: "GOES e2e process", group_id: GROUP,
      kind: "transform", enabled: true, max_runs_per_hour: 120,
    } }), "geocolor")).id;
    await json(await request.post(`/api/processes/${ids.geocolor}/revisions`, { data: {
      runtime: runtime(600, 2048), code: GEOCOLOR_CODE, env: [],
    } }), "geocolor deploy");
    await json(await request.post(`/api/processes/${ids.geocolor}/sources`, { data: {
      collection_id: SRC, trigger: { kind: "item_event", item_filter: null }, expectation: null, enabled: true,
    } }), "geocolor source");
    await json(await request.post(`/api/processes/${ids.geocolor}/outputs`, { data: { collection_id: OUT } }), "geocolor output");

    // Serving on the output collection (the settings PUT carries the whole row —
    // copy the payload shape collection-settings.spec.ts resets with).
    await json(await request.put(`/api/collections/${OUT}/settings`, { data: {
      group_id: GROUP, externally_writable: false, retention_days: null, retention_max_items: null,
      gc_grace_days: 30, archived: false, serving_enabled: true,
    } }), "serving");

    ids.dest = (await json(await request.post("/api/connections", { data: {
      name: DEST_CONN, protocol: "s3", group_id: GROUP,
      config: { bucket: DEST_BUCKET, region: "us-east-1", endpoint: "http://minio:9000", force_path_style: true },
      credentials: { access_key_id: "minioadmin", secret_access_key: "minioadmin" },
    } }), "dest connection")).id;
    ids.deliver = (await json(await request.post(`/api/collections/${OUT}/connections`, { data: {
      connection_id: ids.dest, direction: "deliver", enabled: true, expectation: null,
      config: {
        path_template: "goes/{item_id}/{filename}", item_filter: null, asset_keys: null,
        payload: { item_json: false, checksums: null, completion_marker: false },
        on_update: "redeliver", overwrite: "if_newer",
        retry: { max_attempts: 5, backoff: "exponential" }, max_concurrent_transfers: 4,
      },
    } }), "deliver association")).id;

    // The ingest association goes LAST so the first poll finds everything wired.
    ids.ingest = (await json(await request.post(`/api/collections/${SRC}/connections`, { data: {
      connection_id: ids.nodd, direction: "ingest", enabled: true, expectation: null,
      config: {
        source_path: "ABI-L2-MCMIPC/", include: [`**/${filename}`], exclude: [],
        poll_frequency_seconds: 60, storage_mode: "reference",
        path_template: "{Y}/{j}/{H}/", window: { begin: "-2h", end: null }, max_files_per_poll: 1,
        grouping: { rule: "none", timeout_seconds: 900, on_timeout: "ingest_partial" },
        metadata: { strategy: "extractor", extractor: { process_id: ids.extractor } },
        post_ingest: "leave",
      },
    } }), "ingest association")).id;

    // --- gate 1: the extracted source item --------------------------------
    const source = await poll("source item", async () => {
      const res = await request.get(`${STAC}/collections/${SRC}/items?limit=10`);
      if (!res.ok()) return null;
      const f = (await res.json()).features?.find((x: any) =>
        Object.values(x.assets ?? {}).some((a: any) => String(a.href).includes(filename)));
      return f ?? null;
    });
    expect(source.properties.datetime).toBe(expectedDatetime);
    expect(source.properties.platform).toBe("goes-19");
    expect(source.properties["goes:scene_id"]).toBeTruthy();
    expect(source.geometry?.type).toBe("Polygon");
    expect(source.properties["stac_higher:geometry_source"]).toBeUndefined();

    // --- gate 2: the GeoColor output item ---------------------------------
    const outId = `${source.id}-geocolor`;
    const output = await poll("output item", async () => {
      const res = await request.get(`${STAC}/collections/${OUT}/items/${encodeURIComponent(outId)}`);
      return res.ok() ? res.json() : null;
    });
    expect(output.assets.visual.href).toBe(`/api/assets/${OUT}/${outId}/visual.tif`);
    expect(output.assets.visual.roles).toContain("visual");
    expect(output.properties["proj:code"] ?? output.properties["proj:wkt2"] ?? output.properties["proj:projjson"]).toBeTruthy();

    // --- gate 3: a tile renders --------------------------------------------
    const tilejson = await json(await request.get(
      `${TILER}/collections/${OUT}/items/${encodeURIComponent(outId)}/WebMercatorQuad/tilejson.json?assets=visual`,
    ), "tilejson");
    const [lon, lat, z] = tilejson.center as [number, number, number];
    const n = 2 ** z;
    const x = Math.floor(((lon + 180) / 360) * n);
    const y = Math.floor(((1 - Math.log(Math.tan((lat * Math.PI) / 180) + 1 / Math.cos((lat * Math.PI) / 180)) / Math.PI) / 2) * n);
    const tileUrl = String(tilejson.tiles[0]).replace("{z}", String(z)).replace("{x}", String(x)).replace("{y}", String(y));
    const tile = await request.get(tileUrl);
    expect(tile.ok()).toBeTruthy();
    expect(tile.headers()["content-type"]).toMatch(/^image\//);

    // --- gate 4: delivered ---------------------------------------------------
    await poll("delivery", async () => {
      const body = await json(await request.get(`/api/collections/${OUT}/connections/${ids.deliver}/deliveries`), "deliveries");
      const rows: any[] = body.deliveries ?? body.rows ?? [];
      return rows.find((r) => r.item_id === outId && r.status === "delivered") ?? null;
    });
    const s3 = new S3Client({
      endpoint: MINIO, region: "us-east-1", forcePathStyle: true,
      credentials: { accessKeyId: "minioadmin", secretAccessKey: "minioadmin" },
    });
    const head = await s3.send(new HeadObjectCommand({ Bucket: DEST_BUCKET, Key: `goes/${outId}/visual.tif` }));
    expect(head.ContentLength ?? 0).toBeGreaterThan(100_000);
  });
});
```

Adjust two things against the real code before finishing: the settings PUT body (copy the exact field list from `collection-settings.spec.ts`), and the deliveries listing key (read `listDeliveries` in `app/src/lib/associations/deliveries.ts` and use its actual property name instead of the `deliveries ?? rows` guess). The `proj:*` assertion should match what rio-stac 0.12 emits for a CRS without an EPSG code (`proj:wkt2` or `proj:projjson`; `proj:epsg` is null) — check with `uv run python -c "import rio_stac; print(rio_stac.__version__)"` and rio-stac's changelog if unsure, and pin the assertion to one key.

- [ ] **Step 2: Typecheck and the skip path**

Run: `cd app && npx tsc --noEmit -p e2e 2>/dev/null || npx playwright test goes-loop --reporter=list`
Expected: with `E2E_LIVE_NODD` unset the spec reports `1 skipped`, no server needed beyond what the config starts (or pass `E2E_PORT` per the run-e2e skill if :4321 is busy). Do NOT run it with the flag set — that is Task 7.

- [ ] **Step 3: Commit**

```bash
git add app/e2e/goes-loop.spec.ts
git commit -m "test(e2e): live-gated GOES loop spec — extract, compose, tile, deliver (G-7)"
```

---

### Task 5: `run-e2e` skill — the flag and the preconditions

**Files:**
- Modify: `.agents/skills/run-e2e/SKILL.md`

- [ ] **Step 1: Edit**

- Preconditions: add item 4 — "**Live-data specs**: `goes-loop.spec.ts` runs only with `E2E_LIVE_NODD=1`. It needs the internet, the FULL stack including the pipeline (`docker compose up -d --wait`), the process runtime image (`stac-higher-process-runtime:local`, see `services/pipeline/src/pipeline/demo/README.md`), migration 027 applied (load any app page), the `stac-higher-deliveries` bucket (`minio-init` creates it), and `CREDENTIALS_MASTER_KEY` in the dev server env (the delivery connection). Budget ~5 minutes; the bound is 10. Run it alone: `E2E_LIVE_NODD=1 npm run test:e2e:ci -- goes-loop`."
- Run section: the spec list becomes `assets`, `catalogs`, `collection-settings`, `connections`, `data-flow`, `extension-forms`, `extensions`, `goes-loop` (gated), `monitoring`, `processes`, `proxy`.
- New `## Environment flags` section after Run: a table — `E2E_PORT` (dev server port), `E2E_LIVE_NODD` (`1` enables the live GOES spec), `E2E_STAC_URL` / `E2E_TITILER_URL` / `E2E_S3_ENDPOINT` (defaults `:8082` / `:8084` / `:9000`, for a stack on other ports).
- Gotchas: add "**The live GOES spec depends on NOAA.** A NODD outage or an hour with no CONUS scans (rare, e.g. during ABI maintenance) fails it in `newestMcmipcKey`, which is a precondition failure, not a regression. It leaves nothing behind on success; on a mid-run failure, `afterAll` still removes its `e2e-goes-*` rows — check `/processes` and `/connections` if a run was killed."

- [ ] **Step 2: Commit**

```bash
git add .agents/skills/run-e2e/SKILL.md
git commit -m "docs(run-e2e): E2E_LIVE_NODD flag, live-spec preconditions, spec list (G-7)"
```

---

### Task 6: Docs — the worked example, FEATURES, spec, TODO; full gates

**Files:**
- Modify: `docs/processes.md` (new "## The GOES worked example" after "## Extractors"), `docs/FEATURES.md` (G-7 row; flip the GOES section's 🔄 to ✅ if every G row is done), `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` (Progress line), `docs/ISSUES.md` (a new 🟡 entry: "Live-data e2e depends on NOAA availability — seeded offline variant if it proves flaky", per spec §14), `TODO.md` (tick G-7; note the K queue is now unblocked; follow-ups)

- [ ] **Step 1: `docs/processes.md` — the worked example**

```markdown
## The GOES worked example

The loop the platform was built to run: GOES-19 ABI CONUS files from NOAA's
open bucket, catalogued in place, fixed up by an extractor, turned into a
true-colour COG by a process, served as tiles, delivered onward. The code is
checked in and runnable:

| Piece | Where |
|---|---|
| `goes-abi-metadata` (extractor) | `services/pipeline/src/pipeline/demo/goes/extractor.py` |
| `goes-geocolor` (process) | `services/pipeline/src/pipeline/demo/goes/geocolor.py` |
| Seed the whole loop on the local stack | `uv run python -m pipeline.demo goes-seed` (`services/pipeline/src/pipeline/demo/README.md`) |
| The automated one-file proof | `app/e2e/goes-loop.spec.ts` with `E2E_LIVE_NODD=1` |

**The source.** An anonymous s3 connection to `noaa-goes19`; a reference-mode
ingest association over `ABI-L2-MCMIPC/` with `path_template {Y}/{j}/{H}/`,
a `-1h` window and a per-poll cap, so a 250,000-object product is listed two
prefixes at a time; `metadata.strategy: extractor` naming `goes-abi-metadata`.

**The extractor** sets the three things the platform cannot infer from a
GOES netCDF. The scan start is in the filename, not in any modified time:

```python
SCAN_TOKEN = re.compile(r"_s(\d{14})")          # _sYYYYDDDHHMMSSt

def scan_time(filename):
    digits = SCAN_TOKEN.search(filename).group(1)
    base = dt.datetime.strptime(digits[:13], "%Y%j%H%M%S").replace(tzinfo=dt.timezone.utc)
    return base + dt.timedelta(milliseconds=100 * int(digits[13]))
```

The footprint comes from a band subdataset — the file itself carries no
georeferencing, only `NETCDF:"file":CMI_C02` does — reprojected from the
geostationary CRS with densified edges so the limb curves. And the file is
downloaded to local disk first: **GDAL's netCDF driver cannot read `/vsis3`
paths in the runtime image** (it needs Linux userfaultfd), so
`s3.download_file(asset["bucket"], asset["key"], path)` then
`rasterio.open(f'NETCDF:"{path}":CMI_C02')`. Everything else — platform,
instruments, `goes:*` — is read off the container's global attributes
(`dataset.tags()["NC_GLOBAL#scene_id"]`).

**The process** is about sixty lines: read C01/C02/C03/C13 as reflectance
and brightness temperature (`values = raw * scale + offset`, fill → NaN),
compose, write, publish.

```python
def compose(c01, c02, c03, c13):
    red, blue, veggie = (np.clip(np.nan_to_num(b), 0, 1) for b in (c02, c01, c03))
    green = np.clip(0.45 * red + 0.10 * veggie + 0.45 * blue, 0, 1)   # synthetic green
    rgb = np.stack([red, green, blue]) ** (1 / 2.2)                     # gamma
    night = 1 - np.clip((np.nan_to_num(c13, nan=313.0) - 90.0) / (313.0 - 90.0), 0, 1)
    rgb = np.maximum(rgb, night[None])                                  # night IR by max
    mask = np.isfinite(c02) & np.isfinite(c13)
    return np.where(mask[None], rgb * 255, 0).round().astype("uint8"), (mask * 255).astype("uint8")
```

The COG is written in the file's native geostationary CRS (`driver="COG"`,
deflate, 512 blocks, `overviews="AUTO"`, an internal mask for off-disk
pixels — the tile server reprojects), and the item comes from rio-stac with
`with_proj=True` so `proj:*` carries the WKT2 of a CRS that has no EPSG
code. Its id is the source id plus `-geocolor`, its one asset is `visual`
with the relative href `visual.tif`, and its collection is whatever the
seeder substituted for `__OUTPUT_COLLECTION__`.

This is the CIMSS/goes2go true-colour recipe, labelled "GeoColor-style":
no Rayleigh correction, no city lights. CIRA's GeoColor proper needs lookup
tables that are not published; a Satpy runtime image is the documented next
step.
```

Keep the minimal `mask.tif` snippet in "How to publish an item" (it is the minimal shape; the spec's "replace" is satisfied by the worked example section — say so in the commit message).

- [ ] **Step 2: FEATURES / spec / ISSUES / TODO**

`docs/FEATURES.md`: a `G-7 · GOES worked example + live e2e | ✅ |` row: the two scripts, the demo subcommands, the delivery bucket, the gated spec and its four gates, the run-e2e flag; note the three spec §15 corrections that made it work (local disk, extractor footprint, `include` glob). If every G row is now ✅, change the section heading's 🔄 to ✅.

Spec `Progress:` line: "G-7 merged <date>; live gate <met / owed> (Task 7)".

`docs/ISSUES.md` under the GOES section: `### I-102 · The live GOES e2e depends on NOAA availability 🟡` — the §14 risk: it is gated and out of CI's default run; if it proves flaky, add a seeded offline variant driven by `pipeline.demo goes-seed --include` against a copied file in MinIO. (Use the next free I-number if 102 is taken.)

`TODO.md`: tick G-7 (plan path); update the G queue header line ("G-1…G-7 done"); in the K queue header note G-6/G-7 are done so K-1 is unblocked once the K spec is approved; append a `G-7 landed` follow-ups entry.

- [ ] **Step 3: Full gates and commit**

Run: `npm run verify` and `cd services/pipeline && uv run pytest -q && uv run ruff check .`
Expected: green. Report counts.

```bash
git add docs TODO.md
git commit -m "docs(goes): the worked example in processes.md, FEATURES G-7, I-102, tick G-7"
```

Merge per AGENTS.md (`git checkout ai/main && git merge ai/goes-g7 --no-ff`), re-run both gates on `ai/main`, remove the worktree.

---

### Task 7 (LEAD ONLY, Docker + internet): the live gate

1. Stack: `docker compose build pipeline && docker compose up -d --wait` (picks up the `minio-init` bucket and the G-6 pipeline); `docker build -f services/process-runtime/Dockerfile -t stac-higher-process-runtime:local services/process-runtime` if the image predates rio-stac 0.12; load any app page (migration 027).
2. Delete (or re-point) the W-1 seed association on `goes-abi-mcmipc` — `goes-seed` refuses to sit beside another enabled ingest association on the collection, and `goes-teardown` refuses (without `--force`) while any other association targets it. (The e2e uses its own collections and is unaffected.)
3. From `app/` with the dev server env sourced (`set -a && source ../.env && set +a`): `E2E_LIVE_NODD=1 npm run test:e2e:ci -- goes-loop`. Expected: 1 passed in under 10 minutes. On a failure, read which gate timed out, then `docker compose logs pipeline --since 15m | grep -E "extract|geocolor|finalize|deliver"` and the run log link on `/processes/<id>`.
4. Manual recipe once: `cd services/pipeline && CREDENTIALS_MASTER_KEY=… uv run python -m pipeline.demo goes-seed --deliver`, wait two polls, `goes-status`, open `http://localhost:4321/collections/goes-geocolor/items` and the tiler map link it prints; then `goes-teardown`.
5. Record M-gate style in `ROADMAP.md` §9 if the roadmap tracks G gates there (check), the spec's Progress line, and `TODO.md`'s follow-ups: measured end-to-end latency (file listed → tile served), the two staging copies per file, and anything the scripts needed changed (e.g. the `write_mask` / COG-driver interaction, rio-stac's `asset_href`).

---

## Self-review (done while writing)

- **Spec coverage:** §2 gate steps 1–4 (Task 4, one per gate); §9 extractor + process (Task 1; §15 corrections applied: local disk, extractor footprint); §10 e2e + `run-e2e` update (Tasks 4, 5), docs (Task 6), a manual recipe (Task 2); §14 live-data risk (I-102, Task 6); §15 "one home for the GOES code" (Tasks 1, 2, 4, 6 all read the same files).
- **Placeholders:** none. Two spots ask the implementer to confirm a shape against a named file (the settings PUT payload; the deliveries listing key; rio-stac's `asset_href` kwarg) rather than guess.
- **Type consistency:** `extractor_code()` / `geocolor_code(output_collection)` / `OUTPUT_COLLECTION_TOKEN` used identically in Tasks 1, 2, 4; `ingest_config(...)` keyword names match between Task 2's seed and its test; the e2e's ingest config mirrors `ingest_config()` field for field; `EXTRACTOR_ID`/`GEOCOLOR_ID` only in Task 2.
