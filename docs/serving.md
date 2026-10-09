# OGC API serving (titiler-pgstac + tipg)

Pre-M5 hardening item (pulled forward from Phase 8's stretch, sequenced
2026-08-27; implemented 2026-08-29). Two off-the-shelf services join the
compose stack and give the platform an OGC-conformance story beyond the STAC
API itself:

| Service | Image | Port | Serves |
|---|---|---|---|
| `titiler` | `stac-higher-titiler:local` — derived from `ghcr.io/stac-utils/titiler-pgstac:1.7.2` (`infra/titiler/`) | :8084 | OGC API Tiles for **rasters**, per STAC collection/search, straight off pgstac |
| `tipg` | `ghcr.io/developmentseed/tipg:1.0.1` | :8085 | OGC API Features + Tiles for **vector tables** in the shared PostGIS database |

Version pins follow eoAPI's tested combination for pgstac 0.9.x.

## The design pass, in short

- **Toggle semantics — link-level only.** `collection_settings.serving_enabled`
  (migration 019, app-only column) controls whether the collection page's
  Settings tab **advertises** the serving endpoints. Nothing gates requests to
  the services themselves: until per-collection read visibility (I-1) lands,
  anything either service can see is effectively public. The UI copy says so,
  and the toggle exists now so the semantics (and audit trail — the Settings
  PUT is guard-audited like every other settings change) are already in place
  when I-1 makes it enforceable.
- **What the collection page links to.** The serving panel (visible when the
  toggle is on) links:
  - *Raster tiles* → the titiler-pgstac **collection** endpoints
    (`{PUBLIC_TITILER_URL}/collections/{collection_id}/info` as the entry
    point; tilejson/tile/viewer routes hang off the same prefix). These are
    true per-STAC-collection endpoints — titiler queries pgstac directly.
  - *Vector features/tiles* → the tipg **landing page**
    (`{PUBLIC_TIPG_URL}/`). tipg serves database tables and functions, not
    STAC collections, so this is a stack-wide surface, linked for the OGC
    Features/Tiles API story rather than per-collection data.
- **Item preview (G-5).** When a collection has serving on and an item carries
  a `visual`-role (or GeoTIFF/COG-typed) asset, the product item page's
  Geometry tab overlays that item's tiles beneath its footprint, taken from
  the tile server's item TileJSON
  (`{PUBLIC_TITILER_URL}/collections/{c}/items/{i}/WebMercatorQuad/tilejson.json?assets={asset}`),
  with a link to titiler's own viewer. The read-only catalog browser never
  does this. Every failure is silent — no tile server, no serving, no
  previewable asset, or a tiler that cannot open the asset all mean "no
  layer", never an error on the item page. URL builders and the asset picker
  live in `app/src/lib/serving/`; the overlay is the shared
  `RasterTileLayer`.
- **Collection preview.** With serving on, a product page gains a **Preview**
  tab: an animated raster map of the product, one frame per timestep. Frames
  are the collection mosaic endpoint with `datetime` pinned —
  `{PUBLIC_TITILER_URL}/collections/{c}/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?assets={a}&datetime={t}`
  — so a timestep tiled across several items composites for free, and no
  search registration, POST or CORS handling is involved. Three things about
  it are load-bearing:
  - **The `datetime` is the catalog's own string, unmodified.** The filter
    matches the instant exactly: `2026-09-04T06:11:17.300000Z` renders and the
    same instant written `…17Z` returns an empty tile.
  - **Playback waits for tiles.** A tick advances only when
    `map.areTilesLoaded()` (capped at 10 s), so the first pass runs at the tile
    server's pace and replays run at the full rate off the browser cache. The
    outgoing frame stays painted beneath the incoming one, so a step never
    flashes an empty map. Only ONE frame is warmed ahead: every mounted source
    competes for the same few connections to the tiler, and a deeper window
    starves the frame on screen.
  - **The zoom range comes from the newest item's TileJSON.** The collection
    mosaic advertises 0–24 over the whole extent, which would have maplibre
    asking the tiler to oversample a five-level pyramid.

  The tab is hidden unless serving is on and the recent items carry a
  tileable asset. Frame count (25/50/100/200) and — when a product publishes
  more than one rendering — the asset are pickable. Code:
  `app/src/lib/serving/frames.ts`, `CollectionPreviewTab`, and the shared
  `TimeSlider`.
- **Canonical hrefs are mapped in the tile server.** Items keep their
  app-relative `/api/assets/{collection}/{item}/{filename}` hrefs (ADR 0005 —
  bytes are only reachable through the app); the derived image rewrites them
  to `s3://{PLATFORM_ASSET_BUCKET}/assets/{collection}/{item}/{filename}`
  inside both readers (`infra/titiler/stac_higher_titiler/hrefmap.py` — the
  `PgSTACReader` item endpoints and the `SimpleSTACReader` collection/search
  mosaics), so platform assets tile with no change to the catalog. The
  compose service carries `AWS_S3_ENDPOINT=minio:9000` credentials for the
  platform bucket. Reference-mode absolute URLs and explicit `s3://` hrefs
  pass through as before. This is the *local* answer to I-68 (GOES spec
  §7.1, G-4); the cloud deployment chooses between the same mapping (with
  the deployment's bucket) and presign integration.

The `/map` page (V-4) is the third consumer of the tilers: its Add-layer picker
lists `GET {PUBLIC_TIPG_URL}/collections` (only while the picker is open, no
credentials) and draws a chosen collection from
`{PUBLIC_TIPG_URL}/collections/{id}/tiles/WebMercatorQuad/tilejson.json` — a
TileJSON 3.0 document whose `vector_layers[0].id` is `default`, the MVT
source-layer name every tipg tile carries (verified against tipg 1.0.1 on
2026-09-09). The local stack's tipg exposes only PostGIS function collections
(`public.st_hexagongrid`, …) until a demo table is seeded (I-126).

## Env

| Var | Default | Meaning |
|---|---|---|
| `PUBLIC_TITILER_URL` | `http://localhost:8084` | Browser-facing titiler base the UI links to |
| `PUBLIC_TIPG_URL` | `http://localhost:8085` | Browser-facing tipg base the UI links to |
| `PLATFORM_ASSET_BUCKET` | `${STAGING_BUCKET:-stac-higher}` | (titiler) The platform bucket canonical hrefs map into — mirrors the pipeline's `STAGING_BUCKET`. Required; the derived image refuses to start without it |
| `ASSET_HREF_BASE` | `/api/assets` | (titiler) The href base the pipeline writes — mirrors the pipeline's `ASSET_HREF_BASE` |

## Trying it

```bash
docker compose build titiler          # the derived image (infra/titiler/)
docker compose up -d titiler tipg
curl http://localhost:8084/healthz
curl http://localhost:8085/healthz
# per-collection raster metadata (any pgstac collection id):
curl http://localhost:8084/collections/<collection-id>/info
# a tile from a canonical (/api/assets) asset — the href mapper at work:
curl -o tile.png "http://localhost:8084/collections/<coll>/items/<item>/tiles/WebMercatorQuad/0/0/0.png?assets=visual"
# OGC API landing pages / conformance:
curl http://localhost:8084/api.html   # titiler OpenAPI
curl http://localhost:8085/           # tipg landing (HTML/JSON)
```

Then toggle **OGC serving** on a collection's Settings tab (operator+) and the
links appear on the page.

## Bumping titiler-pgstac

The pin lives in three places that move together: `infra/titiler/Dockerfile`
(`FROM`), `infra/titiler/pyproject.toml` (the `titiler` test extra) and the
image table above. The href mapper wraps rio-tiler 7's `_get_asset_info`;
titiler-pgstac 3.x (rio-tiler 9) changed that method's return shape — run
`cd infra/titiler && uv run --extra dev --extra titiler pytest` against the
candidate version before moving the pin, and adjust `main.py` if the `url`
key moved. Details: `infra/titiler/README.md`.

## Virtual cubes: the collection asset

A cube sink (ADR 0022; spec `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md`)
publishes its Icechunk repository on the **cube collection** after every batch
that reached the repository, and after Z-6's maintenance trim
(`services/pipeline/src/pipeline/cubes/collection.py`). The `cube-server` that
serves it as tiles and EDR is Z-7.

```jsonc
"assets": {
  "cube": {
    "href": "s3://{platform bucket}/assets/{cube collection}/_cube/",
    "type": "application/vnd.zarr+icechunk",
    "roles": ["data", "references", "virtual", "latest-version"],
    "title": "Virtual cube",
    "version": "<icechunk snapshot id of main>",
    "stac_higher:virtual_chunk_prefixes": ["s3://noaa-goes19/"],
    "stac_higher:cube_sink_id": "<uuid>",
    "stac_higher:time_values": ["2026-10-03T17:02:36.714359936Z", "…"]
  }
},
"extent": { "temporal": { "interval": [["<first t>", "<last t>"]] } },
"cube:dimensions": {
  // x/y: GOES-East CONUS, approximately
  "t": { "type": "temporal", "extent": ["<first t>", "<last t>"] },
  "x": { "type": "spatial", "axis": "x", "extent": [-3626269.5, 1381770.0], "reference_system": { "…PROJJSON" } },
  "y": { "type": "spatial", "axis": "y", "extent": [1584175.1, 4588198.0], "reference_system": { "…PROJJSON" } }
},
"stac_extensions": ["…", "https://stac-extensions.github.io/datacube/v2.2.0/schema.json"]
```

- **Ownership:** the pipeline owns `assets.{asset_key}` (default `cube`),
  `extent.temporal`, `cube:dimensions` and the Datacube entry of
  `stac_extensions`, and rewrites them on every publish; every other key is
  the user's. A hand edit to those four lasts until the next commit, and a
  deleted asset comes back. Renaming the sink's `asset_key` moves the asset:
  the sink's asset under the old key is removed.
- **Collection-form saves:** the UI's collection form rebuilds the document
  from its fields (`CollectionForm.tsx::formToStacCollection`), so every save
  drops `cube:dimensions`. A save from a copy loaded before a publish also
  writes that copy's older asset back. The next publish repairs both: the next
  commit (≤ 5 min at the GOES cadence), or the hourly maintenance run for a
  source that has stopped.
- **`version`** is the snapshot the sink recorded
  (`cube_sinks.last_snapshot_id`). Only that tip is published, under a row lock
  on the collection (`FOR NO KEY UPDATE`, 10 s `lock_timeout`), so a double run
  never publishes an older snapshot.
- **`stac_higher:time_values`** holds the exact nanosecond `t` values. Build
  frames from this list and select `t` with these strings verbatim (or
  `nearest::`). JavaScript's `Date` truncates them to milliseconds, so never
  round-trip them through it. Never take `t` from `extent.temporal` either: a
  user's PUT through stac-fastapi-pgstac truncates it to microseconds, while
  `time_values` and `cube:dimensions.t` keep full precision.
- **`x`/`y` extents are projected metres:** a geostationary grid's scan angle
  times `perspective_point_height`, the same rescale the cube-server applies.
  The writer assumes the stored `x`/`y` are radians, as CF requires of a
  geostationary grid (it cannot read their `units`; the cube-server checks
  `units == "rad"`). `reference_system` is the grid mapping's PROJJSON, for
  `geostationary` only in v1; any other grid omits it.
- **An empty cube** (trimmed to zero steps) has `time_values: []` and
  `[null, null]` intervals.
- **Size:** each step adds about 33 bytes to the collection document: 288
  steps ≈ 10 KB, and the 10,000-step maximum ≈ 330 KB, carried by every
  `/collections` response.
- **No audit row per publish** (spec §14.3). Each publish logs one
  `cube collection asset` line with `outcome`: `published`, `unchanged`,
  `superseded` or `missing_collection`.
