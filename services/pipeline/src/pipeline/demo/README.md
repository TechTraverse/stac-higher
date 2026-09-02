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

## Relationship to the other harnesses

- **`pipeline.loadgen`** measures throughput with synthetic volume through an
  ingest association. This seeds one realistic flow and leaves it in place to
  look at. Different jobs; the demo is not a load test.
- **G-7's e2e spec** will drive the real GOES/NODD loop and needs the internet,
  a live NOAA bucket, and Docker. This needs only the local stack.
