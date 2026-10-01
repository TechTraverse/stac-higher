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
   (`docker buildx bake -f services/process-runtime/docker-bake.hcl runtime`
   from the repo root; the bare `docker build -f
   services/process-runtime/Dockerfile -t stac-higher-process-runtime:local
   services/process-runtime` still works for the base alone). The bake file's
   default group also builds `stac-higher-process-runtime-stactools:local`,
   the built-in extractor library's image (X-2), which the demo does not need.

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
PIPELINE reaches MinIO, default `http://minio:9000`); `goes-teardown` takes
`--force`. Like `seed` it is idempotent: re-running replaces both revisions and
leaves one association.

The `goes-geocolor` revision asks for **2048 MB**, which is sized for MCMIP**C**
(CONUS, 1500²). A full-disk product (MCMIPF, 5424²) needs several times that —
raise `memory_mb` on the revision before pointing the association at one.

### Preconditions (beyond the three above)

1. **The internet**, and a bucket that is genuinely public: nothing is signed
   against `noaa-goes19`.
2. **Migration `027_extractors`** — an ingest association cannot name an
   extractor before it. `goes-seed` checks and says so.
3. **`CREDENTIALS_MASTER_KEY`** in the environment, for every `goes-seed`: the
   anonymous NODD connection stores an encrypted EMPTY envelope (the app does
   the same — the pipeline refuses a connection with no envelope at all), and
   `--deliver` seals the delivery connection's MinIO credentials with the same
   key, exactly as `pipeline.loadgen` does. It is checked before anything is
   written. From the repo root: `set -a; source .env; set +a`.

### It refuses a second ingest source

If either GOES collection already has an ENABLED ingest association whose
connection is not `goes-nodd`, `goes-seed` exits naming the association,
collection and connection rather than seeding beside it — two associations
polling the same product into the same collection ingest every file twice.
The check runs BEFORE the first write, so a refused seed has changed nothing:
not the collection documents, not the processes. Disable the other one first.

### `goes-teardown` refuses to delete a collection somebody else uses

`goes-abi-mcmipc` and `goes-geocolor` are fixed, well-known names shared by
every `goes-seed` invocation, so another ingest association may be pointing at
one of them (the W-1 seed association is, on the reference stack). Deleting the
collection out from under it would leave it aimed at nothing — so `goes-teardown`
lists any such association, ENABLED OR NOT, and exits without deleting
anything. Pass `--force` to delete anyway.

It still does not check whether a poll is mid-flight or a run is queued, so
don't run it while another `goes-seed` session, or a manual poke at the shared
collections, is in progress. (The gated e2e spec is unaffected either way: it
creates its own uniquely named `e2e-goes-*` collections and connections and
tears down only those.)

### What to look at afterwards

| Surface | Where |
|---|---|
| Granules catalogued in place | `http://localhost:4321/collections/goes-abi-mcmipc/items` |
| The composites the process published | `http://localhost:4321/collections/goes-geocolor/items` |
| Both processes, their runs and logs | `http://localhost:4321/processes` |
| Tiles, straight from the tiler | `http://localhost:8084/collections/goes-geocolor/WebMercatorQuad/map?assets=visual` |
| Runs + ingest ledger + item counts | `uv run python -m pipeline.demo goes-status` |

## Images loop (`images-seed`)

The C-5 live-gate scenario (container-images spec §15, GitHub #54): the same
CLI adds demo images through the APP's own `/api/images` HTTP surface (the
path an operator uses on the dashboard, not a direct SQL write) and deploys
two kind-2 processes on top of whatever the app hands back.

```
demo images                    ghcr.io/…/stac-higher-process-runtime:latest
  │                             (the platform's own runtime image) and
  │                             python:3.12-slim, added through /api/images
  │  each add opens an admission scan (Syft + Grype, C-2)
  ▼
goes-geocolor-img (process)    a kind-2 TWIN of goes-seed's goes-geocolor:
  │                             the SAME GeoColor code, running on the
  │                             runtime image's APPROVED digest instead of
  │                             the built-in runtime, triggered by
  │                             goes-abi-mcmipc like the original
  ▼
goes-geocolor-img (collection) what it publishes into

images-canary (process)        a second kind-2 process on the SLIM image,
                                no outputs — just enough to prove a small
                                user image also deploys and runs
```

### Preconditions (beyond the three at the top of this file)

1. **`goes-seed` already run** — `images-seed` checks for its source
   collection (`goes-abi-mcmipc`) and stops, naming the command, if it is
   missing.
2. **The scanner image built**: `docker buildx bake -f
   services/process-runtime/docker-bake.hcl image-scanner` from the repo
   root (`stac-higher-image-scanner:local`). Without it every add stalls in
   `scanning`/`pending` until `--scan-timeout`.
3. **The dev server running as an ADMIN identity** — granting an exception
   is an admin-only route. From `app/`, with the repo's `.env` sourced:

   ```sh
   cd app
   set -a; source ../.env; set +a
   DEV_AUTH_IDENTITY='{"roles":["admin"]}' ASTRO_DEV_BACKGROUND=0 npm run dev
   ```

   (Astro 7 daemonizes `astro dev` under an AI agent unless
   `ASTRO_DEV_BACKGROUND=0` is set.) If :4321 is already taken by another
   session's server, run it on :4399 instead and point `images-seed`/
   `images-status` at it:

   ```sh
   DEV_AUTH_IDENTITY='{"roles":["admin"]}' ASTRO_DEV_BACKGROUND=0 npm run dev -- --port 4399
   uv run python -m pipeline.demo images-seed --app-url http://127.0.0.1:4399 ...
   ```

### Use

```sh
uv run python -m pipeline.demo images-seed --exception-days 14 --with-kev
uv run python -m pipeline.demo images-status
uv run python -m pipeline.demo images-teardown        # add --images to LIST the demo rows
```

`images-seed` is idempotent: re-running it finds each demo image already
registered (by normalized reference + tag) instead of re-adding it, and
reinstalls both process revisions. Do not re-seed while a demo run is queued
or running: `install_process` replaces the demo processes' revisions and
their run history out from under it. `--with-large` also adds `python:3.12`
(~1 GB, to see a bigger image scan); `--with-kev` also adds
`vulnerables/cve-2014-6271`, which the policy rejects on KEV membership.
`--exception-days N` (1-90) grants the runtime image a time-boxed exception
if the scan rejects or flags it. Since GitHub #60 the runtime is built on
Debian trixie and passes the default policy, so no exception is needed; the
flag stays for a future failing digest. After a rebuild pushes a new
`:latest` digest, add the image again on `/images` (a new digest is a new
row), then re-run `images-seed`; it picks the newest non-revoked row for the
tag. Revoke the old row on `/images` once nothing uses it. `--no-wait` returns immediately instead of polling for scan verdicts —
it only registers the images; nothing installs until a later run finds both
scans settled `approved` (`--scan-timeout`, default 1800s, bounds the poll
when waiting).

`images-teardown --images` matches rows by `reference:tag`, so it also lists
a row the seed merely found already registered — a user's own
`python:3.12-slim`, or the runtime row carrying its admin exception — not
only rows `images-seed` itself created. On its own it deletes nothing: it
lists each matching row not in use (reference:tag, id, status) and each it
would skip (still in use by a process revision), then says "re-run with
--yes to delete these rows". `--images --yes` deletes the listed rows and
prints each one deleted. Deleting bypasses the app entirely and writes no
audit row.

### The manual C-5 walk-through in the UI

`images-seed` drives the API path; the gate itself (spec §15) is walked by
hand, one control at a time, in `/images` and `/processes`:

1. **Add image** (`/images` → Add image) `python:3.12-slim`. Re-adding an
   already-registered reference folds into the existing row by digest
   instead of creating a second one — watch it land `approved` with a scan
   history entry.
2. **Grant exception** (`/images` → the runtime image's detail sheet →
   Grant exception, admin only) on the GHCR runtime image once its scan
   lands `rejected` (only needed if a future digest fails the policy). Without this
   step or `--exception-days`, the seed stops before installing
   `goes-geocolor-img` and says why.
3. **Custom image + your code** (`/processes` → the deploy form's Runtime
   chooser) redeploy `goes-geocolor` as `goes-geocolor-img` on the
   now-approved runtime digest, same code unchanged. Watch the next
   triggered run publish a `visual` COG.
4. **Add image** the KEV reference (`vulnerables/cve-2014-6271`, `--with-kev`)
   and watch it land `rejected` naming `kev:CVE-2014-6271` and its sibling
   CVEs.
5. **Rescan now** (`/images` → `python:3.12-slim`'s detail sheet, the
   canary's image) after switching to the strict policy below: watch it go
   `flagged` in about two minutes, a `process_image_flagged` alert fire
   within a minute ("new deploys are refused, runs continue"), the process
   read Degraded, and a UI deploy attempt refused (409 `only an approved
   image can be deployed`) while the next triggered run still launches and
   succeeds. Switch back to the default policy and **Rescan now** again:
   back to `approved`, the alert auto-resolves.
6. **Revoke image** (`/images` → detail sheet → admin) the KEV row — a
   revoked row is terminal, so a later `images-seed --with-kev` adds a
   fresh row rather than reusing it. That fresh row can itself land
   `scan_failed` (`existing_image_revoked`) if the drain resolves it to the
   same digest as the row you just revoked; it is harmless (the KEV
   reference is never installed on a process either way) — **Rescan now**
   on its detail sheet clears it, or omit `--with-kev` on the next
   `images-seed` to skip it entirely.

### The strict-policy toggle

`infra/image-policy/strict-demo.json` is the default policy plus
`block.high_unfixed: true` — strict enough to flag an image the default
policy passes, for step 5 above.

```sh
# pipeline: strict policy
docker compose -f docker-compose.yml -f infra/compose.strict-image-policy.yml up -d pipeline
# pipeline: back to the default policy (repeat any other -f overlays you
# normally run with too, e.g. the auth-enforced one, or this drops them)
docker compose up -d pipeline
```

The app reads the same policy file for its deploy gate and verdict display
(`app/src/lib/images/policy.ts` caches it in memory per path, so a switch
needs a dev-server restart either way). Stop the running `npm run dev`
(`astro dev stop` or Ctrl-C) and restart it pointed at the strict file, then
restart again without the variable to restore the default — both restarts
in the same shell as precondition 3's `.env` source (or repeat
`set -a; source ../.env; set +a` first):

```sh
# strict
PROCESS_IMAGE_POLICY_FILE="$(pwd)/../infra/image-policy/strict-demo.json" \
  DEV_AUTH_IDENTITY='{"roles":["admin"]}' ASTRO_DEV_BACKGROUND=0 npm run dev
# default (unset the override)
DEV_AUTH_IDENTITY='{"roles":["admin"]}' ASTRO_DEV_BACKGROUND=0 npm run dev
```

### Sampling scan memory

Each admission/rescan runs as a container named `stac-scan-*`; sample its
memory while one is in flight:

```sh
while true; do
  docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}' | grep stac-scan-
  sleep 1
done
```

### What to look at afterwards

| Surface | Where |
|---|---|
| Every demo image, its status and its scan history | `http://localhost:4321/images` |
| Both processes, their runs and logs | `http://localhost:4321/processes` |
| What `goes-geocolor-img` published | `http://localhost:4321/collections/goes-geocolor-img/items` |
| Runs + alerts + item count | `uv run python -m pipeline.demo images-status` |
| Only digest-pinned pulls reach the daemon | `docker image ls --digests` |

## Relationship to the other harnesses

- **`pipeline.loadgen`** measures throughput with synthetic volume through an
  ingest association. This seeds one realistic flow and leaves it in place to
  look at. Different jobs; the demo is not a load test.
- **G-7's e2e spec** drives the same GOES/NODD loop automatically, pinned to a
  single granule. `goes-seed` is its manual, full-hour sibling; plain `seed`
  needs no internet at all.
