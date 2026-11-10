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
| `PUBLIC_CUBE_SERVER_URL` | `http://localhost:8086` | Browser-facing cube-server base the cube Preview reads tiles and EDR from |
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
that reached the repository (`services/pipeline/src/pipeline/cubes/collection.py`).
Z-6's hourly maintenance republishes through the same writer for sinks with a
`window`. The `cube-server` serves it as tiles and EDR (next section).

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
  `extent.temporal`, `cube:dimensions`, `cube:variables` and the Datacube entry of
  `stac_extensions`, and rewrites them on every publish; every other key is
  the user's. A hand edit to those five lasts until the next commit, and a
  deleted asset comes back. Any other asset carrying a
  `stac_higher:cube_sink_id` is stale (a renamed `asset_key`, or a deleted
  sink's asset; a collection has at most one sink) and is removed.
- **Collection-form saves:** an edit re-reads the stored document and
  three-way merges it (`app/src/lib/stac-api/collectionEdit.ts`, #102). Keys
  the form doesn't build (`cube:dimensions`, `item_assets`, ...) pass through,
  a field the user didn't change keeps the stored value (so a form opened
  before a publish can't roll back `extent.temporal` or the Datacube entry),
  and an asset carrying `stac_higher:cube_sink_id` always keeps the stored
  copy: the form can neither edit nor delete it. A publish that lands between
  the re-read and the `PUT` (milliseconds) can still be overwritten; the next
  commit or the hourly maintenance republish (Z-6) repairs it.
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

## Virtual cubes: the cube-server (:8086)

Locally it is opt-in: `docker compose --profile cube up -d` (compose profile
`cube`; see [`backend.md`](backend.md)).

`infra/cube-server` (Z-7, spec §8): xpublish 0.5.2 + xpublish-tiles 0.9.2 +
xpublish-edr 0.11.1 over every **enabled** cube sink's Icechunk repository.
Datasets are keyed by **cube collection id**:

```
GET /datasets/{cube_collection}/tiles/WebMercatorQuad/{z}/{y}/{x}
      ?variables=CMI&t=<exact time value>&style=raster/gray
      &colorscalerange=190,310&width=256&height=256&f=png
GET /datasets/{cube_collection}/edr/position
      ?coords=POINT(-97 38.5)&parameter-name=CMI&f=csv
GET /health
```

- **Registry, not URLs.** Every `CUBE_POLL_SECONDS` (15) the server reads the
  enabled sinks (`id, cube_collection_id, config, source_prefixes` through the
  read-only role of migration 033) and `lookup_branch("main")` of each
  repository at `s3://{PLATFORM_ASSET_BUCKET}/assets/{cube}/_cube`. A cube is
  reopened only when its snapshot, variables, prefixes or append dimension
  change; a disabled or deleted sink 404s from the next poll. The server never
  takes a store URL, bucket or prefix from a request.
- **Sources:** exactly `cube_sinks.source_prefixes`, anonymous `s3://<bucket>/`
  only (v1; a bare `s3://` is refused). Any other prefix makes that sink an
  error, not an open. A cube none of whose configured `variables` exist in the
  repository is an error too (`ValueError`), not an empty healthy dataset.
  Repository handles are cached per collection, checked with
  `Repository.exists` on every poll and dropped when the sink goes. The server
  reads NODD directly: network-level egress control rides #68.
- **Geostationary rescale:** `x`/`y` in `rad` are served in metres
  (× `perspective_point_height`), matching `cube:dimensions` on the asset. EDR
  returns empty results on radians (spike Q2).
- **Not served:** a sink whose repository does not exist yet or has no
  recorded `source_prefixes` (`pending` in `/health`; the prefixes are empty
  during a sink's first append and on a provisional redo, and without them no
  chunk could be read), or whose repository holds zero steps (`empty`).
- **Time selection:** the selector is named after the sink's `append_dim`
  (`t` for GOES; `time=…` for a sink with `append_dim: "time"`). Use the
  asset's `stac_higher:time_values` strings
  verbatim (or `nearest::<value>`). A `t` the server's snapshot lacks — the
  asset can be one poll ahead, or the step was just trimmed — is a 422.
- **Caching** (no server-side chunk cache; Icechunk's did not help virtual
  chunks; "`t`" below is the dataset's own append dimension, looked up by the
  path's collection id): a 200 tile with an exact `t` is
  `Cache-Control: public, max-age=31536000, immutable`; any other 200 tile
  (no `t`, or a `nearest::` selector) is `public, max-age=60`; a non-200 tile
  is `no-store`, so a not-yet-loaded `t` is retried, and so is every tile
  requested with a `render_errors` other than an explicit falsy value
  (`false`, `0`, `off`, `f`, `n`, `no`; it draws an error as a 200 image).
  Other routes are left alone. A tile also answers `HEAD` (as the GET, without
  the body). After every (re)open the server renders one z0 tile of the newest
  step (pinned with the append dimension) to absorb the Numba JIT (2.4–2.6 s
  cold vs 0.44 s warm in the spike). Warm-ups run detached (two at a time, 90 s
  each) so a slow one never delays the next poll.
- **`edr/position` is per-step tolerant and GET-only.** The selection loads
  once; only when that raises `StorageError` does each step load separately. A step whose source file is gone (Icechunk `StorageError`) is an
  empty CSV field / JSON `null`, and `x-cube-missing-steps` carries the count
  (`0` when none; exposed through CORS). `POST` is not offered.
  `edr/area` and `edr/cube` are xpublish-edr's own and **fail** on a missing
  step. `position` accepts only `method=nearest` (the default): `linear`
  interpolates before the tolerant load, so it is a 422. A time selector on the
  append dimension (`t=…`) may be the asset's `…Z` string verbatim.
- **`/health`** (always 200): `datasets` (snapshot, steps, opened_at),
  `pending`, `empty`, `errors` and `last_reload_error`. Errors show the
  exception **type** only, since the endpoint is anonymous; the text is in the
  service log. A DB or per-sink failure keeps serving what is already open.
  `errors` is keyed by cube collection id and also holds a sink whose config is
  rejected (`InvalidSinkConfig`; its open dataset stays, so a sink whose config
  stops parsing keeps serving its last good dataset) and a cube whose
  warm-up tile was not a 200 (`WarmUpFailed(<status>)`, `None` when the request
  failed), cleared by the next successful warm-up or when the cube goes.
- **Stale snapshot risk:** if reloads keep failing for longer than Z-6's
  `CUBE_SNAPSHOT_RETENTION_SECONDS` (1 h), the open snapshot can be expired and
  its manifests garbage-collected under the server. `last_reload_error` is the
  signal.
- **Auth posture:** anonymous reads, CORS `*` (the preflight allows GET and
  HEAD, and a tile answers both), the same as
  titiler until #34. Read-only on both sides: the DB login sees five
  `cube_sinks` columns and nothing else, and the Silo user
  (`infra/cube-server/silo-cube-read-policy.json`) can only read and list
  `assets/*/_cube/*`.

### The Preview's use of it (Z-8)

The collection Preview renders a cube with a fixed style, `CUBE_DEFAULT_STYLE`
= `raster/Greys` over 190-310 K (GOES C13 brightness temperature; `Greys` maps
low to white, so cold cloud tops are white), always sent as `colorscalerange`
because real CMI has no `valid_min`/`valid_max` and the renderer would
otherwise auto-range per tile. xpublish-tiles 0.9.2 validates the style variant
against its own list, which has no matplotlib `_r` reversals: `gray_r` is a 422.
A tile 422 whose body is `{"detail": "Invalid indexer: …"}` (a `t` the server's
snapshot lacks: the asset is up to one poll, 15 s, ahead) is retried by
remounting the frame after 5 s, up to 3 times, never by changing the URL (an
unknown query parameter is a dimension selector); it never marks the frame
failed. Any other 422 (a bad style, range or variable) is not retried. The variable is the first `cube:variables` entry of type `data`, else
`CMI` (the Z-5 writer does not emit `cube:variables` yet: #170). A click calls
`edr/position` and charts the CSV; `x-cube-missing-steps` is shown as a count.
