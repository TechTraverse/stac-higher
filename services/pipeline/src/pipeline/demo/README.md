# The demo pipeline (`pipeline.demo`)

The smallest thing that exercises the whole G queue end to end, rebuilt from
nothing in about ten seconds. Use it after a `docker compose down -v`, to show
someone what the platform does, or to check a change end to end without the
GOES/NODD preconditions that G-7's e2e spec carries.

What it builds:

```
demo-scenes                    a collection holding a real 1024² RGB COG,
  │                            written to CANONICAL storage with an
  │                            /api/assets/... href
  │  item write wakes the dispatcher (NOTIFY), which queues a run and
  │  dispatches it immediately (G-3)
  ▼
demo-downscale                 an inline_python process, network level
  │                            `isolated`. It reads its INPUT MANIFEST
  │                            (ADR 0018), opens the scene from the bucket and
  │                            key the manifest gives it, and writes a 4×
  │                            downscaled COG plus a STAC item
  ▼
demo-thumbnails                the output collection it publishes into
```

Both collections have OGC serving on, so both the source scene and the
process's own output render as tiles through the derived tile server (G-4),
and both show the raster preview on their item pages (G-5).

## Use

```sh
docker compose up -d --wait          # repo root
cd services/pipeline

uv run python -m pipeline.demo seed
uv run python -m pipeline.demo status
uv run python -m pipeline.demo teardown
```

`seed` is idempotent — run it again to trigger another run, or after a volume
wipe to rebuild everything. `--no-trigger` seeds the fixtures without the scene
item, so nothing runs until you add one yourself.

Flags exist for every endpoint (`--stac-url`, `--s3-endpoint`, `--database-url`,
`--bucket`, `--titiler-url`); the defaults are compose's published ports.

## Preconditions

1. **The stack is up.** `docker compose up -d --wait` from the repo root.
2. **The app has migrated this database.** The APP owns the `stac_higher` DDL
   and runs migrations on its first API request (ADR 0001), so a freshly wiped
   volume needs the app to have started once. `seed` checks and tells you what
   to do rather than failing half-built.
3. **The process runtime image exists** as `stac-higher-process-runtime:local`
   (`docker build -f services/process-runtime/Dockerfile -t
   stac-higher-process-runtime:local services/process-runtime`).

## What to look at afterwards

| Surface | Where |
|---|---|
| The scene, with its tiles under the footprint | `http://localhost:4321/collections/demo-scenes/items/demo-scene-001` → Geometry |
| What the process published | `http://localhost:4321/collections/demo-thumbnails/items` |
| The run, its log, and its timing | `http://localhost:4321/processes` → demo-downscale |
| Tiles, straight from the tiler | `http://localhost:8084/collections/demo-scenes/items/demo-scene-001/WebMercatorQuad/map?assets=visual` |

`status` prints the claim latency of each run — the gap between the run row
being created and the executor claiming it. Since G-3 that is single-digit
milliseconds; before it, a run waited for the next minute tick.

## GOES loop (`goes-seed`)

The same CLI also builds the **GOES worked example** (G-7, spec §9) — the same
shapes, but against the LIVE NOAA NODD bucket instead of a synthetic scene.
This is the manual "full hour" recipe spec §10 names; `app/e2e/goes-loop.spec.ts`
is the automated one-file version of the same loop.

```
goes-abi-mcmipc                an anonymous s3 connection to noaa-goes19,
  │                            REFERENCE mode over ABI-L2-MCMIPC — the last
  │                            hour, at most two new files per poll. Each
  │                            granule's metadata comes from the
  │                            goes-abi-metadata EXTRACTOR (scan time from the
  │                            filename, footprint from the CMI_C02 grid),
  │                            because a GOES netCDF defeats the built-in path
  │  item write wakes the dispatcher, which queues the process run
  ▼
goes-geocolor (process)        a GeoColor-style true-colour COG per granule:
  │                            day = C02/C03/C01, night = inverted C13,
  │                            blended per pixel
  ▼
goes-geocolor (collection)     what it publishes into — tiling through the
                               derived tile server, optionally DELIVERED to a
                               MinIO bucket with `--deliver`
```

```sh
uv run python -m pipeline.demo goes-seed          # add --deliver for the delivery leg
uv run python -m pipeline.demo goes-status
uv run python -m pipeline.demo goes-teardown
```

`goes-seed` takes `--include GLOB` (repeatable — pin one granule), `--window`
(default `-1h`), `--max-files` (default 2) and `--internal-s3-endpoint` (how the
PIPELINE reaches MinIO, default `http://minio:9000`). Like `seed` it is
idempotent: re-running replaces both revisions and leaves one association.

### Preconditions (beyond the three above)

1. **The internet**, and a bucket that is genuinely public: nothing is signed
   against `noaa-goes19`.
2. **Migration `027_extractors`** — an ingest association cannot name an
   extractor before it. `goes-seed` checks and says so.
3. **`CREDENTIALS_MASTER_KEY`** in the environment, for `--deliver` only: the
   delivery connection's MinIO credentials are sealed with it, exactly as
   `pipeline.loadgen` does. It is checked before anything is written.

### It refuses a second ingest source

If `goes-abi-mcmipc` already has an ENABLED ingest association whose connection
is not `goes-nodd`, `goes-seed` exits naming the association and connection ids
rather than seeding beside it — two associations polling the same product into
the same collection ingest every file twice. Disable the other one first.

### `goes-teardown` has no guard

`goes-teardown` deletes `goes-abi-mcmipc` and `goes-geocolor` straight
through the STAC API by id, unconditionally — it does not check whether a
poll is mid-flight, a run is queued, or anyone else is looking at either
collection. Both are fixed, well-known names shared by every `goes-seed`
invocation (the gated e2e spec is unaffected: it creates its own uniquely
named `e2e-goes-*` collections and connections and tears down only those).
Don't run `goes-teardown` while another `goes-seed` session, or a manual
poke at the shared collections, is in progress.

### What to look at afterwards

| Surface | Where |
|---|---|
| Granules catalogued in place | `http://localhost:4321/collections/goes-abi-mcmipc/items` |
| The composites the process published | `http://localhost:4321/collections/goes-geocolor/items` |
| Both processes, their runs and logs | `http://localhost:4321/processes` |
| Tiles, straight from the tiler | `http://localhost:8084/collections/goes-geocolor/WebMercatorQuad/map?assets=visual` |
| Runs + ingest ledger + item counts | `uv run python -m pipeline.demo goes-status` |

## Relationship to the other harnesses

- **`pipeline.loadgen`** measures throughput with synthetic volume through an
  ingest association. This seeds one realistic flow and leaves it in place to
  look at. Different jobs; the demo is not a load test.
- **G-7's e2e spec** drives the same GOES/NODD loop automatically, pinned to a
  single granule. `goes-seed` is its manual, full-hour sibling; plain `seed`
  needs no internet at all.
