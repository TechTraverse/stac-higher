# Virtual cube sink — design

**Date:** 2026-10-03
**Status:** **approved 2026-10-05** by the lead. Drafted by the Z-1 spike session from ADR 0022 and the spike results; the decisions in §14 are approved as written.
**Tracking:** epic #83.
**Scope source:** [ADR 0022](../../decisions/0022-virtual-cube-sink.md)
(accepted; the decisions this spec implements),
[the 2026-09-30 research](../../research/2026-09-30-icechunk-rolling-cube.md)
and [the Z-1 spike results](../../research/2026-10-03-virtual-cube-spike.md),
which supply every measured number below, including the 24 h soak (Q4) and
the late-file count (Q8). Icechunk's own bookkeeping grows without bound; that
is accepted for v1 and recorded as I-143 (§10).

## 1. Problem

The GOES loop turns each NODD file into a COG item. ADR 0022 adds a second
shape: each new file is appended as **virtual chunk references** to an
Icechunk repository along its time dimension. The repository keeps a rolling
window and is published as one collection-level asset, served as map tiles and
an OGC EDR API. No NODD bytes are copied. The ADR settles *where* this lives (a
platform job, not a process), *who writes* (one writer per repository) and
*what may be deleted* (Icechunk GC inside `_cube/` only). This spec settles
*how*: tables, contracts, jobs, the server, the UI and the slice order.

The spike changed four things the ADR did not foresee, all below the decision
level:

1. xpublish-edr returns **empty 200s** on scan-angle (radian) coordinates. The
   cube server must present `x`/`y` in metres (§8.2).
2. Tile responses carry no cache headers and nothing caches virtual chunks. The
   cube server sets `Cache-Control` and the UI bounds and preloads frames
   (§8.3, §9).
3. One missing or changed source fails **every EDR series that spans it**, not
   just one step. The server reads series per step (§8.4).
4. The queue abstraction has no `lock` / `queueing_lock` seam yet (§5.1).

## 2. Decisions already taken (ADR 0022 and the lead, 2026-10-03)

- A platform-owned **cube sink**, not a user process. Strictly virtual.
- A **separate cube collection** owns the repository. The source collection
  keeps one reference item per NODD file.
- Repository at **`assets/{cube_collection}/_cube/`**. `_cube` is a reserved item
  id in a cube collection.
- **One writer** (`pipeline.cube_append`) per sink, serialized by Procrastinate
  `lock` + `queueing_lock` = `cube:{sink_id}`. Idempotent on the time coordinate.
  It never uses a conflict solver.
- **Late files are skipped** in v1 (ledger `skipped`, reason `late`). The soak
  saw no late file and no missing step in a day of `noaa-goes19` CMIPC.
- Icechunk libraries go **in the main pipeline image**.
- **Icechunk GC inside `_cube/`** is the one byte-deletion path outside
  `asset_gc` (amends ADR 0011), only on sinks with a window.
- Reader: a **`cube-server`** (xpublish + xpublish-tiles + xpublish-edr). It opens
  only registered repositories and never takes a store URL from the client. Its
  auth posture is titiler's until #34.
- Demo product: GOES-19 `ABI-L2-CMIPC` band 13; MCMIPC is the stretch.

## 3. Contracts

### 3.1 `cube-sink-config.json` (new fixture)

The ADR's shape, unchanged except for `window.max_age` (now optional) and a
documented `parser` enum. Format per `tests/contract-fixtures/README.md`:
`minimal`, `defaults`, `cases[]` with `app` / `pipeline` verdicts. The writer is
strict (Zod `cubeSinkConfigSchema` in `app/src/lib/cubes/schemas.ts`). The
reader is lenient (`pipeline/cubes/config.py::parse_cube_sink_config`).

```json
{
  "parser": "hdf5",
  "append_dim": "t",
  "variables": ["CMI", "DQF"],
  "loadable_variables": ["t", "x", "y", "goes_imager_projection"],
  "asset_key": "cube",
  "window": { "max_steps": 288, "max_age": "24h" },
  "on_late": "skip"
}
```

| Field | Rule |
|---|---|
| `parser` | `"hdf5"` only (v1). `"grib"` is reserved: the writer rejects it; the reader rejects unknown values. |
| `append_dim` | required, non-empty; must be a dimension the parser yields (checked at first append, not at write) |
| `variables` | 1–64 names; each becomes a time-chunk-1 array |
| `loadable_variables` | must include `append_dim`; read in full and stored natively |
| `asset_key` | default `"cube"`; `[a-z0-9_-]{1,32}` |
| `window` | optional. Absent: no trim and **no GC** (ADR 0011 safety). `max_steps` 1–10,000; `max_age` a duration `^\d+[mhd]$`, ≤ 30 d. At least one is required when `window` is present. |
| `on_late` | `"skip"` only in v1 |

The fixture's cases cover: a minimal config, a full config, `grib` rejected,
`append_dim` missing from `loadable_variables`, an empty `window`, a
`max_steps` of 0, an unknown key (writer rejects, reader accepts), and a
`window` absent (accepted on both sides; GC never runs).

### 3.2 `cube-append-status.json` (new fixture)

Ledger statuses `pending | appended | skipped | failed`. Skip reasons: `late`,
`duplicate`, `no_source_connection`, `unsupported_layout`, `source_missing`.
Both runtimes import the list; the UI labels it.

### 3.3 The collection asset (documented shape, not a fixture)

The pipeline writes `assets.{asset_key}` on the **cube collection**:

```json
{
  "href": "s3://{platform_bucket}/assets/{cube_collection}/_cube/",
  "type": "application/vnd.zarr+icechunk",
  "roles": ["data", "references", "virtual", "latest-version"],
  "title": "Virtual cube",
  "version": "<icechunk snapshot id>",
  "stac_higher:virtual_chunk_prefixes": ["s3://noaa-goes19/"],
  "stac_higher:cube_sink_id": "<uuid>",
  "stac_higher:time_values": ["2026-10-03T17:02:36.714359936Z", "…"]
}
```

plus `extent.temporal` = `[[first t, last t]]` and `cube:dimensions` (Datacube
extension: `t` temporal with `extent`; `x`/`y` spatial with `extent` in the
projected metres the server presents and `reference_system` = the grid
mapping's PROJJSON). The media type and the `stac_higher:*` fields are
provisional (ADR 0022). `stac_higher:time_values` carries the **exact**
nanosecond `t` values, because the tile server needs exact or `nearest::` time
selectors (spike Q1). The UI builds frames from that list, not from a parsed
label.

## 4. Data model — migration 032

The number is **proposed**: 029 is K-3's (#11), 030 is C-1's, and 031 is
reserved for K-4 (#12). Reserve 032 on the Z-2 issue.

### 4.1 `stac_higher.cube_sinks`

```sql
CREATE TABLE stac_higher.cube_sinks (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_collection_id text NOT NULL,
  cube_collection_id   text NOT NULL UNIQUE,      -- one sink per cube collection
  enabled             boolean NOT NULL DEFAULT true,
  config              jsonb NOT NULL,             -- cube-sink-config.json
  source_prefixes     text[] NOT NULL DEFAULT '{}', -- set by the pipeline at repo create
  last_snapshot_id    text,
  last_appended_at    timestamptz,
  last_maintained_at  timestamptz,
  last_maintenance    jsonb,                      -- expiry + GC summary
  last_error          text,
  created_by          text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (source_collection_id <> cube_collection_id)
);
CREATE INDEX cube_sinks_source_enabled ON stac_higher.cube_sinks (source_collection_id) WHERE enabled;
```

Ownership follows the **cube collection's** group, exactly as associations
follow their collection's (§14.1). Collections live in pgstac, so there is no
FK. The catalog BFF's collection-delete path disables and deletes sinks naming
the deleted collection as source or cube (§7).

### 4.2 `stac_higher.cube_appends` — the ledger

```sql
CREATE TABLE stac_higher.cube_appends (
  id            bigserial PRIMARY KEY,
  cube_sink_id  uuid NOT NULL REFERENCES stac_higher.cube_sinks(id) ON DELETE CASCADE,
  item_id       text NOT NULL,
  item_datetime timestamptz NOT NULL,
  status        text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','appended','skipped','failed')),
  reason        text,
  snapshot_id   text,
  attempts      int NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (cube_sink_id, item_id)
);
CREATE INDEX cube_appends_pending ON stac_higher.cube_appends (cube_sink_id, item_datetime) WHERE status = 'pending';
```

`UNIQUE (cube_sink_id, item_id)` makes the dispatcher's insert idempotent
(`ON CONFLICT DO NOTHING`). That matters because a transaction-API PUT
arrives as delete + insert (ADR 0007). Terminal rows older than 7 days are
pruned by `cube_maintain`.

## 5. Pipeline: triggering

### 5.1 Queue seam: `lock` and `queueing_lock` on enqueue

`QueueBackend.enqueue(name, payload, *, lock=None, queueing_lock=None)`
(`queue/interface.py`). The Procrastinate backend calls
`task.configure(lock=…, queueing_lock=…).defer_async(**payload)`; the memory
backend honours both (a test double that refuses a second queued job with the
same `queueing_lock` and runs same-`lock` jobs one at a time).
`procrastinate.exceptions.AlreadyEnqueued` is caught **in the backend** and
returned as `Enqueued.coalesced`, not raised. The dispatcher drains the event
either way; the waiting job will pick up the new ledger row. `enqueue_batch` is
unchanged; sinks enqueue one job per sink.

The spike measured this on Procrastinate 3.9.0 across two worker processes:
**0 overlaps** per lock, same-lock jobs alternated between processes, and a burst
of 5 with one `queueing_lock` accepted 1. Same-lock jobs start about 3 s apart
(a lock release waits for the next fetch poll). Coalescing makes that
irrelevant.

### 5.2 Dispatcher matching

In `dispatcher/loop.py::dispatch_once`, after process-source and deliver
matching, an **`insert`** event (only) on a collection with enabled sinks
(`PgCubeRepo.enabled_sinks_for_source(collection_id)`, cached per collection
per batch like `match_process_sources`) does two things:

1. `INSERT INTO cube_appends (cube_sink_id, item_id, item_datetime) … ON CONFLICT DO NOTHING`,
   taking `item_datetime` from the item (`datetime`, else `start_datetime`). Items
   with neither are inserted `skipped` with reason `no_datetime`.
2. Enqueue `pipeline.cube_append {cube_sink_id}` with `lock` and
   `queueing_lock` = `cube:{id}`, before the event is marked processed (the
   existing enqueue-before-drain order).

`update` and `delete` events never match (ADR 0022).

### 5.3 Backstop

`pipeline.cube_kick` (`*/5 * * * *`) enqueues `cube_append` for every enabled
sink with `pending` rows older than 2 minutes. This recovers a job lost to a
crash between ledger insert and enqueue. With `queueing_lock`, a kick against
an already-waiting job coalesces.

## 6. Pipeline: `pipeline.cube_append`

Module `pipeline/cubes/` (`config.py`, `repo.py`, `source.py`, `append.py`,
`collection.py`, `maintain.py`). Registered on the **default** queue: it reads
headers, not bytes. Retry: 3 attempts, exponential.

### 6.1 Sources: connection, endpoint, egress

For each pending row, the job resolves the item's asset href to its **NODD
source href** with `PgProcessRepo.reference_source_hrefs(collection_id, item_id)`
and to the reference-mode ingest association that produced it (the ADR 0018
rule, lifted out of `process/staging.py::build_remote_fetcher` into a shared
`connections/sources.py::association_for_href`). An item no association claims
becomes `skipped: no_source_connection`. Only S3 connections are supported.

The job then builds **both** library configurations from the connection with
`cubes/source.py::libs_from_connection` (spike Q7 code). It **always passes an
explicit endpoint**: the connection's `endpoint`, or
`https://s3.{region}.amazonaws.com` with path style. Without that, obstore
dials the virtual-hosted `{bucket}.s3.{region}.amazonaws.com` while
`_endpoint_host` vets `s3.{region}.amazonaws.com`. Before handing the endpoint
to either library it calls `resolve_pinned(host, settings.egress_allow_hosts)`
and treats `EgressBlocked` as `failed` with the message. Compose-internal Silo
endpoints need `EGRESS_ALLOW_HOSTS`, as the S3 adapter already does.

### 6.2 The append

1. Lock: Procrastinate's `cube:{id}` lock is held for the job's life.
2. Open the repo at `assets/{cube}/_cube/` with platform credentials and
   `num_updates_per_repo_info_file = 100` (§10). If it doesn't exist, create it with one `VirtualChunkContainer` per source
   **bucket prefix** (`s3://{bucket}/`, never `s3://`), and record the prefixes
   on `cube_sinks.source_prefixes`.
3. Read the tip of `append_dim` (native array, one small read).
4. Take up to **50** pending rows in `item_datetime` order. Parse their headers
   **concurrently** (4 at a time): `open_virtual_dataset(..., HDFParser(),
   loadable_variables=…)`, select `variables + loadable_variables`,
   `expand_dims(append_dim)` on the variables, and keep scalar
   `loadable_variables` (the grid mapping) un-expanded. The spike measured about
   1.45 s per header from a laptop and about 0.04 s per commit, so parsing is the
   cost to parallelise.
5. For each parsed item in time order:
   - `t` already present → `appended` (duplicate, no-op).
   - `t` ≤ tip → `skipped: late`.
   - Layout mismatch (variable missing, chunk shape along `append_dim` ≠ 1, a
     different non-time shape) → `skipped: unsupported_layout`.
   - Otherwise `to_icechunk(session.store, append_dim=…,
     last_updated_at=<source LastModified>)`. LastModified comes from the
     ingest listing when `ingest_files` records it, otherwise from one HEAD.
     **Always pass it**: the default (write time + 1 s) leaves a rewrite between
     parse and commit undetected (spike Q5).
6. **Window**, in the same session: `k = max(len − max_steps, steps older than
   now − max_age)`. If `k > 0`, call `shift_array(path, (−k, 0, …))` on every
   array along `append_dim`, then `resize`. The spike measured 7 ms.
7. `commit("append N items: first…last")`. On `ConflictError`, reopen from the
   branch tip and redo steps 3–7 once, then fail the job (retry). Never rebase.
8. Ledger rows → `appended` with the snapshot id, or their skip reason.
   `cube_sinks.last_snapshot_id` and `last_appended_at` are updated.
9. Collection asset writer (§6.3).

If more pending rows remain, the job re-enqueues itself (same locks) instead of
looping, so one job's duration stays bounded.

### 6.3 The collection asset writer

After the commit, in one transaction on a plain pipeline connection (not the
item writer pool; ADR 0020's pairing concerns item loads):

```sql
SELECT content FROM pgstac.collections WHERE id = $cube FOR UPDATE;
-- merge assets.{asset_key}, extent.temporal, cube:dimensions, stac_extensions
SELECT pgstac.update_collection($merged);
```

The row lock serializes against a concurrent BFF edit, which also goes through
pgstac. The writer touches only those three keys and `stac_extensions`. If a
user removed `assets.cube`, it comes back on the next commit (ADR 0022). This is
the pipeline's **first production collection write**. It gets its own test
against a real pgstac (DB-gated) and its own `extra={…}` log line. It writes no
audit row per commit (§14.3).

## 7. App: API, BFF guard, collection lifecycle

- **Routes** (operator+, registered in `lib/authz/permissions.ts`, one
  `audit_log` row each; the group check is inside the route, and a sink outside
  the caller's groups is a 404):
  - `GET /api/collections/[id]/cube-sink` returns the sink whose cube
    collection is `[id]`, plus a ledger summary (counts by status, last 20 rows).
  - `PUT` creates or replaces config and `source_collection_id`; validates both
    collections exist and that the source has an enabled **reference-mode**
    ingest association.
  - `PATCH` toggles `enabled`.
  - `DELETE` removes the sink row. The repository is left in place until the
    collection is deleted (`asset_gc`), so a delete is reversible by re-creating
    the sink while the window holds.
  - `docs/backend.md` route table rows.
- **BFF guard:** `/api/catalog/*` refuses an item with id `_cube` in a
  collection that is the cube side of any sink (422, reason `reserved_item_id`).
- **Collection delete:** the existing delete marks `assets/{c}/` for `asset_gc`,
  which already covers `_cube/`. The route additionally deletes sink rows where
  `[c]` is the source or the cube, inside the same request.
- Writing `assets.{asset_key}` by hand on a cube collection is not blocked. The
  next commit overwrites it.

## 8. `cube-server`

### 8.1 Image and service

- A new derived image at `infra/cube-server/`: its own `pyproject.toml` and
  `uv.lock` (icechunk 2.2.2, zarr 3, xarray, xpublish 0.5.2, xpublish-tiles
  0.9.2, xpublish-edr 0.11.1, psycopg 3, uvicorn), a package
  `stac_higher_cube_server`, and tests.
- The repo-root `.dockerignore` re-includes exactly
  `!infra/cube-server/stac_higher_cube_server` (the `infra/titiler` pattern).
  The base image is digest-pinned and non-root, and the image is scanned per
  the C-queue policy.
- Compose service `cube-server` on **:8086**. Env: `CUBE_DB_DSN` (a read-only
  role with `SELECT` on `stac_higher.cube_sinks` only); `CUBE_S3_ENDPOINT`,
  `CUBE_S3_ACCESS_KEY_ID` and `CUBE_S3_SECRET_ACCESS_KEY` (a Silo user whose
  policy is read-only on `assets/*/_cube/*`, created by `minio-init`);
  `PLATFORM_ASSET_BUCKET`; `CUBE_POLL_SECONDS=15`.
- `GET /health` reports sinks loaded and the last reload error.

### 8.2 Registry and datasets

The server never takes a store URL. Every `CUBE_POLL_SECONDS` it reads the
enabled sinks and, for each one, compares `lookup_branch("main")` with its open
snapshot. On a change it reopens the dataset:

- `authorize_virtual_chunk_access` gets exactly `cube_sinks.source_prefixes`,
  each anonymous or signed. v1 supports anonymous prefixes only; a signed source
  needs credentials the server doesn't hold (§13).
- `xr.open_zarr(session.store, zarr_format=3, consolidated=False, chunks=None, decode_coords="all")`,
  restricted to `config.variables` and the grid mapping.
- **Geostationary rescale** (spike Q2): if the grid mapping is `geostationary`
  and `x.units` is `rad`, replace `x`/`y` with `x·h`, `y·h`
  (`h = perspective_point_height`, units `m`). Tiles were identical either way
  in the spike, and EDR is exact only this way.
- `attrs["_xpublish_id"] = snapshot_id`, so xpublish-tiles' grid cache
  invalidates.

Datasets are served through an xpublish **dataset-provider plugin**
(`get_datasets` / `get_dataset` hooks reading the in-memory registry), keyed by
cube collection id: `/datasets/{cube_collection}/tiles/…`,
`/datasets/{cube_collection}/edr/…`.

### 8.3 Caching and warm-up (spike Q3)

- A middleware sets `Cache-Control: public, max-age=31536000, immutable` on
  tile responses whose query carries an exact `t`, and `max-age=60` on
  unpinned ones (which render the latest step).
- At startup and after each reload, the server renders one low-zoom tile per
  dataset to absorb the Numba JIT. The spike measured 2.4–2.6 s cold against
  0.44 s warm.
- No server-side chunk cache in v1. Icechunk's chunk cache did not help on
  virtual chunks. A decoded-frame LRU is a later option, and whether an
  in-memory reader cache counts as "copying" is a lead call (§13).

### 8.4 Per-step tolerant time series (spike Q5)

The server overrides `GET /datasets/{id}/edr/position` with a wrapper. It
reuses xpublish-edr's selection (`select_by_position`, `project_dataset`), then
loads **each `t` separately**, catches Icechunk `StorageError` per step and
returns `null` for that step plus an `x-cube-missing-steps` header. `area` and
`cube` stay xpublish-edr's own (they fail on a missing step) and are documented
as such. The UI uses only `position`.

## 9. UI

- `PUBLIC_CUBE_SERVER_URL` (default `http://localhost:8086`); builders in
  `lib/serving/urls.ts`: `cubeTileUrlTemplate(collectionId, variable, t,
  style)` and `cubeEdrPositionUrl(collectionId, lon, lat, variable)`.
- **Preview tab** (`CollectionPreviewTab.tsx`): a collection with an asset of
  type `application/vnd.zarr+icechunk` takes a **cube branch** before the item
  branch. That branch:
  - builds frames from `stac_higher:time_values` (new
    `lib/serving/cube-frames.ts::buildCubeFrames`; label via `formatFrameLabel`);
  - defaults to the **last 24 steps**, with a "show full window" toggle;
  - reuses `RasterFrameStack` and `TimeSlider` unchanged, passing
    `tiles: [cubeTileUrlTemplate(…, t)]`;
  - shows a preload progress indicator. At z4 over CONUS the spike measured
    ~0.6 s per frame from a laptop, about 15 s for 24 frames.
- **EDR chart:** a map click in the cube branch fetches `cubeEdrPositionUrl`
  (CSV) and renders a small SVG line chart, `TimeSeriesChart` in
  `packages/shared` with a story. There is no new chart dependency. Null steps
  render as gaps.
- **Sink form:** on the cube collection's Settings tab, a "Virtual cube" card
  (operator+). It has a source collection picker (only collections with a
  reference-mode ingest association), variables, window, and enable/disable. It
  also shows the ledger summary: counts, last error, last snapshot, and the last
  maintenance summary.

## 10. `pipeline.cube_maintain`

- Cron `pipeline.cube_maintain` (`23 * * * *`) enqueues one
  `pipeline.cube_maintain_sink {cube_sink_id}` per enabled sink **that has a
  `window`**. It uses the same `lock = cube:{id}` (no `queueing_lock`), so
  maintenance never runs alongside that sink's appends.
- `cube_maintain_sink`:
  1. **Age trim without new data.** If the oldest step is older than
     `window.max_age` and no append is pending, apply §6.2 step 6 and commit.
     Without this, a source that stops leaves a stale window forever.
  2. `expire_snapshots(older_than = now − CUBE_SNAPSHOT_RETENTION)` (default
     1 h), then `garbage_collect(same cutoff)`.
  3. Prune terminal ledger rows older than 7 days.
  4. Record `last_maintained_at` and `last_maintenance`: counts and bytes from
     `GCSummary`, durations, and per-kind object counts and bytes (below).
- **Bookkeeping growth (I-143, accepted 2026-10-04).** Expiry + GC keep
  snapshots, manifests and chunks flat, but never delete transaction logs or
  the `overwritten/` backups of the `repo` object (one of each per commit).
  The writer therefore creates and opens every cube repository with
  `RepositoryConfig.num_updates_per_repo_info_file = 100` (default 1,000), which
  keeps the `repo` object near 10 KB. Projected growth is then about 8 MB per
  day per cube at a 5-minute cadence. `cube_maintain` lists the repository
  prefix and records object counts and bytes **per kind** (`transactions`,
  `overwritten`, `manifests`, `snapshots`, `chunks`) in `last_maintenance`. It
  logs a WARNING and raises the sink's status to "attention" above
  `CUBE_REPO_WARN_BYTES` (default 1 GiB). There is no platform-side deletion of
  Icechunk internals and no periodic rebuild in v1; both are I-143's revisit
  options.
- Invariants (`docs/decisions/README.md`, already written by ADR 0022): GC only
  under `assets/{c}/_cube/`, only on sinks with a window. `docs/monitoring.md`
  (where `asset_gc` is documented) gains a "cube repositories" note.

## 11. Dependencies

- **Pipeline** (`services/pipeline/pyproject.toml`): `icechunk>=2.2.2,<3`,
  `virtualizarr[hdf]>=2.7.3,<3`, `zarr>=3.4,<4`, `obstore`, `h5py`, `xarray`.
  The image grows; the size delta is recorded on the Z-4 PR. The image is
  scanned per the C-queue policy.
- **cube-server:** §8.1.
- **App:** none.

## 12. Live gate (Z-9, lead-only)

On the full stack, alongside the standing GOES demo and without disturbing it:

1. Create a source collection `goes19-cmipc-c13` with a reference-mode ingest
   association on the existing anonymous NODD S3 connection: prefix
   `ABI-L2-CMIPC/{Y}/{j}/{H}/`, include `M6C13`, window `-6h`.
2. Create the cube collection `goes19-c13-cube` and a sink with
   `window.max_steps = 72` through the UI. The first append creates the
   repository.
3. After 1 h, check:
   - 12 steps, with the asset `version` advancing each commit;
   - Preview animating 12 frames;
   - an EDR click on Kansas charting 12 values that match a direct read (spike
     Q2 script);
   - `aws s3 ls --recursive …/_cube/ --summarize` far below one NODD file per
     step, so no source bytes are copied.
4. Kill the pipeline worker mid-append and restart it. The ledger converges, and
   there are no duplicates in `t`.
5. After 7 h, check that the window holds at 72, and that `last_maintenance`
   shows deletions and a flat repository size.
6. Record append latency (dispatch → commit), tile latency in-region-equivalent
   (Docker on the laptop), and image sizes in this spec's addendum.

## 13. What this spec explicitly does NOT do

- **GRIB** parsing, **region writes** for late files, **MCMIPC RGB** rendering
  (the spike showed the data layer works; it needs a custom render endpoint).
  These are follow-up slices.
- **Signed (private) virtual sources** in the cube server. v1 authorizes
  anonymous prefixes only, and a sink whose source connection is signed is
  refused at `PUT` (422 `signed_source_unsupported`).
- **Server-side frame caches** or overviews. Either would raise the
  strict-virtual question for the lead and, for overviews, a new ADR.
- **Read visibility** on the cube server (rides #34), and network-level egress
  (rides #68).
- **User processes writing cubes** (ADR 0022 Option A).

## 14. Decisions taken by the agent (stand unless overturned)

1. Sink ownership follows the cube collection's group, like associations
   (§4.1), rather than a separate `group_id` column. The ADR says "with a group
   owner"; this reads that as "owned by a group", which the collection already
   provides.
2. `AlreadyEnqueued` is converted to `Enqueued.coalesced` inside the queue
   backend (§5.1), so no caller can turn coalescing into a retry loop.
3. No `audit_log` row per commit (§6.3): 288 rows a day per sink would bury the
   human actions. The sink's API mutations are audited. Commits are visible
   through the ledger and the asset `version`.
4. `cube_maintain` takes the sink's `lock` (§10). GC and expiry probably don't
   conflict with a commit, but the age trim does, and one rule is simpler.
5. `stac_higher:time_values` on the asset (§3.3) rather than the UI asking the
   cube server. The collection document stays self-describing, and the UI keeps
   one data source.
6. The cube server rescales geostationary coordinates (§8.2) instead of the
   writer storing metres. The repository stays a faithful virtual copy of the
   source.
7. A self-re-enqueue at 50 rows (§6.2) instead of an unbounded drain, so a
   backlog after downtime can't hold the lock for an hour.

## 15. Proposed slices — the Z queue

| Slice | Scope | Depends on | Migration | Lead-only |
|---|---|---|---|---|
| **Z-2 · Contracts, migration 032, sink API** | §3 fixtures (both runtimes' tests), §4, §7 routes + permissions + audit, BFF `_cube` guard, collection-delete cleanup, `docs/backend.md` | — | **032** (reserve on the issue) | no |
| **Z-3 · Queue lock seam + dispatcher matching** | §5: `enqueue(lock, queueing_lock)` on both backends, `Enqueued.coalesced`, dispatcher insert-only matching + ledger insert, `cube_kick` backstop; `cube_append` registered as a stub that marks rows `failed: not_implemented` | Z-2 | no | no |
| **Z-4 · `cube_append` + pipeline deps** | §6.1–6.2, §11 pipeline deps, `connections/sources.py` extraction, unit tests with a fake source and repository, plus one Silo/NODD integration test skipped unless an env flag is set | Z-3 | no | no |
| **Z-5 · Collection asset writer** | §6.3, §3.3 asset shape, DB-gated pgstac test | Z-4 | no | no |
| **Z-6 · `cube_maintain`** | §10 (history limit 100, per-kind size readout + warning, I-143), invariants and `docs/monitoring.md` note | Z-4 | no | no |
| **Z-7 · `cube-server` image + compose** | §8; `infra/cube-server`, `.dockerignore`, `minio-init` read-only user + policy, read-only DB role (in Z-2's migration if possible, else a `migration`-labelled follow-up), CI build + scan | Z-2 (table), Z-5 (asset shape) | maybe (DB role) | no (CI builds it); the compose bring-up check is lead-only |
| **Z-8 · UI: Preview cube branch, EDR chart, sink card** | §9; `TimeSeriesChart` story; Vitest for `buildCubeFrames` and URL builders | Z-2, Z-5, Z-7 (URL shape) | no | no (e2e lead-only) |
| **Z-9 · Live gate** | §12 | Z-2…Z-8 | no | **yes** |

Later, not v1: **Z-10** MCMIPC RGB render endpoint; **Z-11** late-file region
writes (only if Q8 says gaps are common); **Z-12** GRIB parser.

ADR: none new (0022 covers it). Migration: **032**, proposed. Docs:
`docs/backend.md` (routes, env, the `cube-server` service), `docs/serving.md`
(the cube server and its posture), `docs/monitoring.md` (cube GC and the sink status), and
`FEATURES.md` rows on merge.
