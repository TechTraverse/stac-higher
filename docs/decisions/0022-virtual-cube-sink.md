# ADR 0022 — Virtual cube sink: a platform-owned rolling Icechunk store per collection

- **Status:** accepted — PR #82 (2026-10-03; proposed the same day; the lead
  confirmed the separate cube collection, the reserved `_cube` item id, the
  Icechunk libraries in the main pipeline image and skip-late in v1;
  implemented by the Z queue, epic #83)
- **Amends:** ADR 0011 — byte deletion only through `asset_gc` gains one
  exception: Icechunk's own GC inside a cube repository's `_cube/` prefix.
  Every other ADR 0011 invariant stands unchanged.
- **Related:** ADR 0011 (retention & GC), ADR 0013 (executor
  isolation — why this is not a process), ADR 0014 (process output path),
  ADR 0018 (process inputs and network profiles — the staging copy this
  avoids), ADR 0005 (canonical key layout), ADR 0006 (pgstac writes from the
  pipeline), ADR 0020 (pgstac session GUCs); GitHub #68 (egress proxy), #34
  (per-collection read visibility, I-1), #48 (object-store decision).
- **Evidence:** [`docs/research/2026-09-30-icechunk-rolling-cube.md`](../research/2026-09-30-icechunk-rolling-cube.md)
  — product, server and platform-gap research, and the probe recipe
  (appendix) behind every measured number below.

## Context

The GOES loop turns each NODD file into a COG item and animates items through
titiler-pgstac. The next demo keeps the files where they are: each new file is
**appended as virtual chunk references** (VirtualiZarr) to an
[Icechunk](https://icechunk.io) repository along a time dimension, the
repository keeps a **rolling window** (last N steps or last T hours), and it is
published as **one collection-level asset** that a Zarr tile/EDR server reads.
No NODD bytes are copied into platform storage, and none are copied in transit.

A probe on 2026-09-30 ([recipe](../research/2026-09-30-icechunk-rolling-cube.md#appendix-reproducing-the-probe); icechunk 2.2.2, virtualizarr 2.7.3, zarr 3.4.0, the
`pgsty/silo` digest pinned in `docker-compose.yml`, real GOES-19
`ABI-L2-CMIPC` files) showed the storage layer works on the local stack with
default settings:

- Silo enforces `If-None-Match: *` and `If-Match` on PutObject (412 on
  failure), which is what makes Icechunk commits atomic.
- Appends committed in 1.2–1.8 s each. `shift_array` + `resize` dropped the
  oldest step in the same commit without copying data, and the virtual data
  still read correctly afterwards. `expire_snapshots` + `garbage_collect`
  removed only objects under the repo prefix.
- **Two concurrent appends along the same dimension always conflict and can't
  be rebased** (`RebaseFailedError`). Each repo needs exactly one writer.
- Every reader must call `authorize_virtual_chunk_access` for each source
  prefix, or reads fail.

The platform side does not fit a user process today:

1. **Inputs are copied.** Runs are `isolated` (ADR 0018); reference-mode
   inputs are staged as full copies into `staging/runs/{run_id}/inputs/`.
   VirtualiZarr needs a few KB of header per file, but would receive the whole
   file. Network levels above `isolated` wait for the egress proxy (#68).
2. **No persistent output.** Run credentials write only
   `staging/runs/{run_id}/` (ADR 0014); a repository must persist across runs.
3. **No single writer.** `claim_due_runs` has no per-process exclusion, the
   worker runs up to 12 jobs, and a run may execute twice.
4. **Outputs are items.** Finalize publishes items; nothing a process emits can
   update a collection's assets, extent or `cube:dimensions`.
5. **Byte deletion** happens only through `asset_gc` (ADR 0011); Icechunk GC
   deletes objects itself.

Making appends a user process would need all of (1)–(5) solved first. An
append is also not user logic: given a parser, a variable list and a window,
the steps are identical for every product. That makes it a platform function,
like delivery.

## Decision

### A cube sink is a platform job, configured like an association

A **cube sink** binds one **source collection** (fed by a reference-mode
ingest association) to one **cube collection**, which owns the repository. It
is stored in a new `stac_higher.cube_sinks` table (app-owned DDL, ADR 0001;
migration number reserved on the issue), with a group owner, `enabled`, and a
`config` validated by both runtimes against a new contract fixture
`tests/contract-fixtures/cube-sink-config.json`:

```json
{
  "parser": "hdf5",
  "append_dim": "t",
  "variables": ["CMI", "DQF"],
  "loadable_variables": ["t", "x", "y", "goes_imager_projection"],
  "asset_key": "data",
  "window": { "max_steps": 288, "max_age": "24h" },
  "on_late": "skip"
}
```

`parser` is `hdf5` only in v1 (NetCDF4/HDF5); `grib` (VirtualiZarr's
`GribberishParser`) is a later, additive value. A cube sink is created and
edited through operator+ API routes, audited like associations; the app never
calls the pipeline (ADR 0004). It reads the row.

### Triggering and the single writer

The outbox dispatcher already matches item events to deliver associations; it
also matches `insert` events on a source collection to its enabled cube sinks,
records one `stac_higher.cube_appends` ledger row per item (`pending`), and
defers `pipeline.cube_append` with:

- `lock = "cube:{cube_sink_id}"` — Procrastinate never runs two jobs holding
  the same lock at once, so **each repository has exactly one writer**, across
  workers;
- `queueing_lock = "cube:{cube_sink_id}"` — at most one job waits per sink; a
  burst of arrivals coalesces into the next run.

One job drains every `pending` ledger row for its sink in **item datetime
order**, appends them in one session, applies the window and commits once.
`update` and `delete` events are ignored (a NODD file does not change; a
deleted source item leaves its step until the window rolls past it).

**Idempotency.** Icechunk is the source of truth for what was appended: before
appending, the job reads the tip of the time coordinate and skips any item
whose time is already present. A job that dies after commit but before
updating the ledger repeats nothing. On `ConflictError` (someone committed
first despite the lock — e.g. a manual repair) the job reopens from the branch
tip and redoes its work; it never uses a conflict solver.

**Late and out-of-order files.** An item older than the tip is not appended
(`on_late: "skip"` → ledger `skipped`, reason `late`). Gaps are allowed; the
time coordinate records the steps that exist. Filling slots in place
(region writes) is a later option.

### Reading NODD: the source association's connection, egress-checked

The job never opens the network to arbitrary URLs. For each item it resolves
the asset `href` to the **reference-mode ingest association that produced
it** — the same rule ADR 0018 uses for staging — and builds the obstore /
Icechunk S3 configuration (endpoint, region, anonymous or credentials) from
that association's connection. Items no association claims are skipped
(`no_source_connection`). Only S3 connections are supported in v1.

Icechunk's and obstore's HTTP clients do not go through
`connections/egress.py::resolve_pinned`. The job therefore runs
`resolve_pinned` on the connection's endpoint host **before** handing it to
either library, and refuses private, loopback, link-local and metadata
addresses exactly as an adapter would. This is a pre-check, not a pinned
socket: DNS rebinding between the check and the library's own resolution is a
known gap, closed by network-level egress control when #68 lands. Only
platform-validated connections reach this path; no URL comes from user input.

The writer declares one `VirtualChunkContainer` per source **bucket prefix**
(`s3://noaa-goes19/`, trailing slash), never `s3://`. The same prefixes are
recorded on the cube asset (below) so readers authorize exactly those.

### Where the repository lives, and who may write it

The repository lives at **`assets/{cube_collection}/_cube/`** in the platform
bucket. Placing it under the collection's canonical prefix means a collection
delete already marks it for collection by `asset_gc`, with no new deletion
path. `_cube` is a reserved item id in a cube collection; the BFF refuses an
item with that id there.

Only `pipeline.cube_append` and `pipeline.cube_maintain` write it, with
platform credentials. Readers (the cube server, below) get **read-only**
credentials scoped to `assets/*/_cube/*`. No process run receives any grant
to it.

### Rolling window inside the commit

After appending, if the time dimension exceeds `window.max_steps`, or its
oldest step is older than `now − window.max_age`, the job calls
`session.shift_array(path, (-k, …))` and `resize` on **every** array along
`append_dim` (including the coordinate), in the same commit. That requires a
time chunk of 1 on every time-dimensioned array; the sink creates the repo
that way and refuses a source whose layout would not allow it.

### Byte deletion: store-managed GC inside `_cube/` (amends ADR 0011)

A trimmed window leaves unreferenced manifests and snapshots.
`pipeline.cube_maintain` (hourly cron) runs `expire_snapshots(older_than =
now − cube_snapshot_retention)` then `garbage_collect` on each enabled sink.

This is a **second byte-deletion path**, deliberately narrow:

- it deletes only under `assets/{collection}/_cube/`, a prefix only the
  pipeline writes, and only objects Icechunk's own GC finds unreferenced;
- it never deletes source data (NODD bytes are not platform bytes);
- it runs only on a sink whose `window` is set — an unconfigured sink never
  deletes, matching ADR 0011's safety rule;
- whole-repository deletion still goes through `asset_gc`, via the
  collection's prefix mark.

ADR 0011's invariant becomes: *byte deletion happens only through `asset_gc`,
**except** Icechunk's own garbage collection inside a cube repository's
`_cube/` prefix, run by `pipeline.cube_maintain`.*

### The collection asset

Each successful commit updates the cube collection in one transaction
(`SELECT … FOR UPDATE` on the collection row, then `pgstac.update_collection`,
with the ADR 0020 writer GUCs). The job owns only these keys, and leaves the
rest of the document as the user left it:

- `assets.cube` — `href`: the cube server's dataset URL;
  `type: "application/vnd.zarr+icechunk"` (not yet standardized; follows NASA
  VEDA); `roles: ["data", "references", "virtual", "latest-version"]`;
  `version`: the snapshot id; `stac_higher:repo`: the `s3://` repository
  location; `stac_higher:virtual_chunk_containers`: the authorized source
  prefixes.
- `extent.temporal` — first and last step in the window.
- `cube:dimensions.{append_dim}` — the step `values` in the window (the
  preview's frame list).

A `links` entry with `rel: "store"` and `type: "application/vnd.zarr;
version=3"` points at the same dataset, per the STAC Zarr best practices.

### Readers: a platform cube server, registered datasets only

A new compose service, `cube-server`, is a small FastAPI app around
`xpublish.Rest` with the xpublish-tiles and xpublish-edr plugins. It opens
each enabled sink's repository with the Silo endpoint and read-only
credentials, authorizes **only** the sink's recorded source prefixes
(anonymous), and serves it under `/datasets/{cube_collection}/`. A background
task reopens `main` when the snapshot changes and sets the dataset's
`_xpublish_id` to the snapshot id so caches invalidate.

The client never supplies a repository URL or bucket. Its auth posture is the
same as titiler's today: unauthenticated, advisory serving toggle, until
per-collection read visibility (#34) lands. It fetches source chunks from
NODD itself, like titiler on reference-mode items does now; its egress is
brought under #68 with the rest.

### Dependencies

zarr, icechunk, virtualizarr and obstore join the pipeline image (pinned like
the rest; scanned per the C-queue policy). The cube server is a new derived
image under `infra/cube-server` (repo-root context; `.dockerignore` must
re-include its path).

## Consequences

- The demo keeps the strict-virtual promise without waiting for #68: headers
  are read in place, and no file is staged.
- Throughput per cube is serial by design: one commit per job, ~1–2 s from a
  laptop. Coalescing keeps that well under a 5-minute product cadence; a cube
  fed faster than its commit time batches rather than falls behind.
- The pipeline image grows (Rust Icechunk core, obstore). Workers that never
  run cube jobs carry it anyway.
- A cube can only be built from what the platform's parsers handle. A user
  wanting custom cube logic must wait for Option A (below).
- Readers depend on NODD staying available and unchanged. Sources are kept
  for years today, but NODD publishes no retention SLA; a missing source
  object makes that step unreadable, not the cube. Whether VirtualiZarr
  records a checksum that would catch an object replaced in place is
  unverified.
- Strictly virtual means no overviews. Low-zoom tiles read every chunk of a
  frame (≈29 strips of 52 × 2500 for GOES CONUS). Full-disk products will be
  slow at low zoom; this is accepted for the demo and measured in the spike.
- The `application/vnd.zarr+icechunk` media type and the `stac_higher:*`
  fields are provisional; changing them later is an additive asset rewrite.
- Collection-document writes by the pipeline are new. The row lock keeps them
  from losing a concurrent user edit; a user removing `assets.cube` sees it
  restored on the next commit.

## Alternatives considered

- **Option A — a user process that appends** (deferred, not rejected). It
  needs #68 (`inputs` network level), a persistent store-scoped STS grant,
  one-run-at-a-time per process, and a collection-updating output kind. Worth
  building when users need cube logic the sink's config can't express; the
  repository layout and asset shape here are meant to carry over.
- **A process that parses a staged copy** — works today, but copies each file
  in transit, which the demo exists to avoid.
- **Concurrent writers with Icechunk's conflict solver** — the probe showed
  appends along one dimension never rebase; serialization is required.
- **Icechunk `local_filesystem_storage` on a shared volume** — unnecessary on
  Silo; its multi-writer safety is undocumented, and it does not carry to a
  cloud deployment.
- **titiler-multidim as the reader** — documented Icechunk virtual-chunk
  support, but no custom S3 endpoint, `main` only, a client-supplied dataset
  URL (an open-proxy risk behind our BFF), and no geostationary handling.
  Kept as a tiles-only fallback.
- **Earthmover Flux** — requires the repository in Arraylake, not local
  storage.
- **Materialized (re-chunked) Zarr** — faster tiles and overviews, but copies
  every byte; out of scope by requirement.

## Revisit

- When #68 lands: replace the `resolve_pinned` pre-check with network-level
  egress for the pipeline's cube jobs and the cube server, and reconsider
  Option A.
- When #34 lands: put the cube server behind the same read-visibility check as
  titiler.
- If a product needs GRIB, add `parser: "grib"` after testing gribberish on it
  (its complex-packing support is disputed between sources).
- If low-zoom latency is unacceptable on the chosen product, decide between a
  materialized overview level (relaxing strict-virtual for overviews only) and
  a smaller source sector.

## Invariants added to `docs/decisions/README.md`

- Each cube repository has exactly **one writer**: `pipeline.cube_append`,
  serialized by a per-sink Procrastinate `lock`. No process run is granted
  access to a cube repository.
- A cube repository lives only under `assets/{collection}/_cube/`. Icechunk
  GC there is the **only** byte deletion outside `asset_gc`, and runs only on
  a sink with a configured window.
- The cube writer reads sources only through the connection of the
  reference-mode association that produced the item, after a `resolve_pinned`
  check on its endpoint. Virtual chunk containers are bucket prefixes, never
  `s3://`.
- Cube readers open only repositories registered as enabled sinks, authorize
  only the prefixes recorded on the sink, and never take a repository location
  from the client.
