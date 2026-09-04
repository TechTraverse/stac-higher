# GOES GeoColor loop — process inputs, extractors, tiling — design

**Date:** 2026-09-01
**Status:** **approved** (lead sign-off 2026-09-01; runs in PARALLEL with the M3
queue — the lead's call). Implementation queue: `TODO.md` "G queue"; per-slice
plans in `docs/superpowers/plans/2026-09-0{1,2}-goes-*.md`.
**Progress:** G-1, G-2, G-4 merged 2026-09-01; **live-checked 2026-09-02** on the
compose stack: the derived tile server renders item, mosaic and preview tiles
for a canonical-href COG (after a `.dockerignore` fix — infra/ was excluded
from the root build context); a manifest-echo process received its manifest,
read a canonical asset through the source-collection grant, had a 3.8 MB NODD
mesoscale file staged into `inputs/`, and was denied a non-source collection.
Finding for §5: the item-write → dispatcher hop is ALREADY event-driven
(outbox `pg_notify` → `dispatcher/listener.py`); two events 30 ms apart became
two runs, which G-3's queued-run coalescing addresses. G-6 merged 2026-09-03.
G-7 merged 2026-09-03; live gate MET 2026-09-03 (see TODO.md follow-ups for the three live-only defects).
**Scope source:** the lead's request for a real end-to-end use case: consume
GOES ABI data from the NOAA Open Data Dissemination (NODD) buckets, produce a
GeoColor-style Cloud-Optimized GeoTIFF (COG) with an operator-authored
process, deliver it to a destination, and serve it as map tiles through the
tile server that already runs in compose (titiler-pgstac). The use case is
the vehicle; the deliverable is the missing platform capability it exposes.
**Related:** Phase 9 spec (`2026-08-29-phase9-processes-design.md`), ADR 0013
(process isolation), ADR 0014 (process output path), ADR 0005 (asset
service), M3 spec (`2026-09-01-m3-noaa-scale-design.md`) for the two
touchpoints named in §13.
**Issues addressed:** I-68 (tile server cannot open canonical asset hrefs —
answered for the local stack). **Issues to open:** see §14.
**ADR to write with slice G-2:** ADR 0018 — the process input contract and
network profiles (a hard-to-reverse interface that user code will depend on).

---

## 1. Why, in one page

The platform can already do every stage of the loop as a mechanism, and the
full chain is blocked at exactly three points. Everything else in this spec
is consequence.

| Stage | Exists | Blocked by |
|---|---|---|
| Ingest from an S3 bucket, wrap raw `.nc` files as items, reference-mode hrefs | yes | the S3 connection **requires access keys**; NODD is anonymous |
| A process triggered by item arrival, batched per dispatch tick, sandboxed, rate-limited | yes | the triggering items **never reach the container**, and the container's credentials and network cannot reach their bytes |
| Publish outputs (validate → checksum → move → upsert), delivery composes off the outbox for free | yes | — |
| titiler-pgstac + tipg running against pgstac | yes | canonical `/api/assets/...` hrefs are **not openable by GDAL** (I-68) |

Two further facts shape the design:

- **Metadata extraction is product-specific.** GOES filenames carry the scan
  time (`_s20262431201…`), the netCDF carries platform/scene attributes, and
  nothing built-in parses either. The lead wants users to be able to write
  reusable extraction logic rather than the platform growing a strategy per
  product. This spec makes an *extractor* a kind of process, so the sandbox,
  limits, logs and UI already exist for it.
- **Latency matters for some datasets.** A new file currently reaches a
  process through four one-minute polling hops (§5). The lead's direction:
  every step as fast as possible, and choose the shape that survives the move
  to the cloud (where a container cold-start is 30–45 s, not 0.5 s).

Everything in this spec is local-first (compose + MinIO + Docker executor).
Cloud-only items are named in §11 so they are not forgotten, not built.

## 2. Gate (done-when)

On the local compose stack, with the pipeline and the Docker executor running,
a gated Playwright spec (`E2E_LIVE_NODD=1`) does the following through the
product's own API and UI and passes:

1. Creates an **anonymous** S3 connection to `noaa-goes19`.
2. Creates a source collection with a **reference-mode** ingest association
   scoped to one recent `ABI-L2-MCMIPC` file, using an **extractor process**
   for metadata.
3. Creates an output collection, a **GeoColor process** with that source and
   output, and a **delivery** association from the output collection to a
   second MinIO bucket.
4. Observes, within a bounded wait: the source item exists with the scan
   time as its `datetime` and the netCDF attributes in its properties; the
   output item exists with a `visual` COG asset; `GET` of a tile for that
   item from the tile server returns an image; the COG object appears in the
   destination bucket.

Plus: `npm run verify`, `uv run pytest`, `uv run ruff check .` green, the
contract fixtures updated for every new cross-runtime shape, and the docs in
§12 updated.

## 3. Process inputs

### 3.1 What the run receives

Before the container starts, the pipeline prepares an `inputs/` area inside
the run's own prefix and describes it in a manifest. The run's environment
gains two variables:

| Variable | Meaning |
|---|---|
| `STAC_HIGHER_INPUT_PREFIX` | `staging/runs/{run_id}/inputs/` |
| `STAC_HIGHER_INPUT_MANIFEST` | key of this batch's manifest, `staging/runs/{run_id}/inputs/{batch_id}/manifest.json` |

The manifest (cross-runtime contract; fixture `process-input-manifest.json`):

```json
{
  "version": 1,
  "run_id": "…", "process_id": "…", "batch_id": "…",
  "kind": "transform",
  "items": [
    {
      "collection": "goes19-abi-l2-mcmipc",
      "op": "insert",
      "item": { "…full STAC item as stored in pgstac…" },
      "assets": {
        "data": {
          "bucket": "stac-higher",
          "key": "staging/runs/{run_id}/inputs/{batch_id}/OR_ABI-L2-MCMIPC_…nc",
          "staged": true,
          "href": "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-MCMIPC/…nc"
        }
      }
    }
  ]
}
```

`items[].assets` is keyed exactly like `item.assets` and gives, for every
asset, a `bucket` and `key` the run can read with its own credentials. That
is the whole author-facing interface: loop over items, open each asset by
bucket and key (boto3, or rasterio via `/vsis3/{bucket}/{key}`). No branching
on where the file came from. `href` is the catalog's href, kept for
provenance and for slice 2's direct-access levels. `op` is the outbox
operation that triggered the run (`insert` | `update`).

`batch_id` exists so a run can receive more than one batch over its life
(warm runs, §11). Slice 1 always emits exactly one batch per run.

### 3.2 How the bytes get there

Two cases, decided per asset at launch:

- **Platform-held bytes** (the asset href is a canonical `/api/assets/{c}/{i}/{f}`
  — copy-mode ingest output, or another process's output): **no copy**. The
  key is the canonical `assets/{c}/{i}/{f}`, and the run's STS session policy
  gains a read-only statement for each *source collection's* prefix
  (`assets/{collection}/*`, `s3:GetObject`; `ListBucket` conditioned on the
  same prefixes). Granting the collection rather than the individual items
  keeps the inline policy small (real STS caps it at 2048 characters) and
  matches the relationship an operator created by wiring the collection as a
  source.
- **Remote bytes** (any absolute href — reference-mode ingest, the NODD
  case): the pipeline fetches the object **through the association's
  connection adapter** (so the connection's credentials/anonymity and the
  existing egress policy apply) into
  `inputs/{batch_id}/{item_id}/{filename}`, and the manifest marks it
  `staged: true`. This is the only place bytes are copied, and it is what
  slice 2's `inputs` network level removes. The item id in the key keeps two
  items with the same filename apart; code never constructs keys itself, it
  reads them from the manifest.

Staging runs **between minting credentials and launching the container**
(`execute_run`), so a staging failure means the run never starts — the same
ordering the executor already applies to credential failures. A staging
failure is a run failure with the usual retry, and it is recorded on the run
row (`error` names the item and asset).

### 3.3 Consequences for publishing

- The finalize resolver lists every key under the run prefix and treats every
  `.json` as an output item. It must **skip `inputs/`**. The manifest and
  any staged item documents are never candidates.
- Output items may not reference input files as their own assets. The
  existing rule already refuses any relative href containing `/`, so
  `inputs/…` is refused rather than silently dropped; the contract doc says
  so explicitly. An output that needs the input bytes copies them.
- Staged inputs age out with the run prefix under the existing staging TTL;
  a successful finalize deletes the run prefix as it does today.

### 3.4 Bounds

- **Batch size.** A run's input list is whatever one dispatch produced.
  Staging N remote files serially is the slow step at NOAA volumes; slice 1
  stages concurrently up to a small bound (`PROCESS_INPUT_STAGE_CONCURRENCY`,
  default 4) and otherwise accepts the cost. The real fix is the `inputs`
  level (§4, slice 2).
- **Size.** No new limit; the run prefix already has none. A 320 MB full-disk
  file is fine on MinIO.
- **Cron runs** have an empty manifest (`items: []`) and no staging.

## 4. Network profile

A new `network` block on the revision's runtime settings (app schema, Python
mirror, `process-runtime.json` fixture):

```json
"network": { "level": "isolated", "hosts": [] }
```

| Level | Meaning | Slice |
|---|---|---|
| `isolated` | Platform storage only (the compose `process-runs` internal network, MinIO attached). Inputs are staged in. Today's behaviour and the default. | 1 |
| `inputs` | `isolated` plus egress to exactly the hosts appearing in the input items' asset hrefs (derived at launch). Remote inputs are no longer staged; the manifest gives `href` and `staged: false`. | 2 |
| `hosts` | `isolated` plus the operator-typed `hosts` list. | 2 |
| `open` | Unrestricted egress. | 2 |

`hosts` is required non-empty for `hosts`, must be empty otherwise. Hostnames
only (no schemes, no ports, no wildcards in slice 1's schema; slice 2 decides
wildcards when it builds the proxy).

**Deployment cap.** `PROCESS_NETWORK_MAX` (pipeline env; default `isolated`
in slice 1, `hosts` once slice 2 lands) is the highest level a deployment
permits. The app reads the same value (`PUBLIC_PROCESS_NETWORK_MAX` mirrors
it for the UI) and disables higher options in the deploy card with a note. The
pipeline enforces it independently at launch: a revision above the cap fails
the run with a ledger error naming the level and the cap, never launches at a
lower level silently. Slice 1's write gate accepts only `isolated`, so the
field, the fixture, the UI control and the cap are all in place before the
proxy exists, and no revision written in slice 1 changes meaning in slice 2.

`PROCESS_NETWORK` (the compose network name) stays: it is *how* `isolated` is
realised on Docker, not a policy knob. ADR 0013's invariant — user code egress
obeys the connections egress policy — is preserved: `isolated` has none, and
slice 2's proxy is the "resolve_pinned analog at the network boundary" the ADR
anticipates.

## 5. Latency posture

Today a file reaches a process through four one-minute clocks. The change is
the same at each hop: enqueue the next job immediately, keep the periodic job
as a recovery sweep.

| Hop | Today | Change |
|---|---|---|
| DISCOVER finds the file | `ingest_poll` cron (1 min) + a second poll to confirm the object stopped changing | **Done (G-3).** `settle: auto \| two_polls \| immediate` on the ingest config, default `auto`, resolved against the connection protocol by `effective_settle`: immediate for s3, two polls for ftp/sftp. The settled-then-changed guard still catches a key overwritten before FETCH. |
| Item write → dispatcher | — | **Already event-driven; no change needed** (found in the 2026-09-02 live check). The outbox trigger's payload-less `pg_notify('item_events')` wakes `dispatcher/listener.py`, which drains until empty; the minute poll is the fallback for a dropped notification or a dead listener. Two items inserted at 06:19:12 had run rows in the same second. |
| Dispatcher → run claim | `process_run_tick` cron (1 min) | **Done (G-3).** `trigger_run` enqueues `process_run_now(run_id)` when the run is not rate-deferred; the job claims that row by id through the same atomic UPDATE the tick uses, so the two race safely and the tick becomes the recovery sweep. |
| Extractor run (new) | — | Same immediate path. Queued-run coalescing (§6.4) landed with G-3: migration 025 widens the key from rate-deferred runs to EVERY queued run per (process, source), which the live check showed was needed — two events 30 ms apart had become two runs. |

Floor after the change, locally: ingest poll interval (≥ 60 s, operator-set)
+ fetch + one container start (~0.5 s). Everything after DISCOVER is
event-driven. In the cloud, DISCOVER itself becomes event-driven (NODD SNS →
SQS, §11) and the container cold start is addressed by warm runs (§11); neither
changes the contract defined here.

The immediate enqueue must not double-run periodic jobs: Procrastinate
periodic defers are deduplicated per schedule tick, and an ad-hoc enqueue of
the same task with no `queueing_lock` is a separate job — both idempotent by
construction. The plan verifies this with the existing loadgen harness at
M3-D's concurrency setting.

## 6. Extractors

### 6.1 What an extractor is

A process with `kind: "extractor"` (new column on `processes`, default
`"transform"`, immutable after create). It has revisions, env, limits, runs,
logs and rate ceiling exactly like a transform. It differs in three ways:

- It is **selected on an ingest association**, not wired to source/output
  collections: `metadata.strategy: "extractor"` with
  `metadata.extractor: { process_id }`. The process must be owned by the
  association's group. A `kind: extractor` process refuses `process_sources`
  and `process_outputs` writes (409 naming the kind), and a transform process
  cannot be named by an association.
- Its input manifest has `kind: "extract"`, and each item entry carries the
  **draft** item the built-in extraction produced (id, collection, best-effort
  geometry/datetime, the asset entries with hrefs) plus the asset locations.
- Its output is **one `{item_id}.json` per input item**, which the platform
  hands back to ingest instead of publishing. Id and collection must be
  unchanged; assets may gain metadata but may not change href; geometry,
  bbox, datetime and properties are the extractor's to set.

The draft item is the one `build_item` produces today, so an extractor
starts from everything the platform can already infer and only fixes what it
knows better. That keeps extractors small (GOES's is ~25 lines, §9).

### 6.2 Where it sits in ingest

ITEMIZE today: `build_item` → `validate_item` → write. With an extractor the
stage splits at exactly that seam:

1. `build_item` produces the draft. If `strategy == "extractor"`, ITEMIZE
   sets the ledger rows to a new status **`extracting`** (between `stored`
   and `itemized`), and calls `trigger_run` for the extractor process with
   `input_items = [{collection_id, item_id, ledger_ids, draft, assets}]`.
   The draft rides in `input_items` (jsonb; small). ITEMIZE returns.
2. The run launches through the normal path (§3 staging: copy-mode files are
   already canonical and are granted by prefix; reference-mode files are
   staged), runs, and on success enqueues finalize as today.
3. Finalize sees `kind == "extractor"` on the process and takes the
   **extract branch**: for each `{item_id}.json` it checks id/collection
   unchanged and asset hrefs unchanged, then calls the ITEMIZE continuation
   (`validate_item` → write → ledger `itemized`, `flow_stats` bump) — the
   same function the non-extractor path calls, so validation and writing
   have one implementation. An input item with no output document, or an
   output that fails the checks or validation, is marked `failed` on its
   ledger rows with the reason.
4. A run that fails, dies, or times out marks every ledger row in its batch
   `failed` with the run's error (the lead's decision: never fall back to the
   draft). The rows are retried on the next poll like any failed file. The
   existing `process_failed` alert covers the run; the ingest failure is
   visible where ingest failures already are.

Reference-mode files must be staged for an extractor to read them, so
reference-mode + extractor pays one copy per file. Accepted for GOES.

### 6.3 Why through the run ledger, not inline

Inline (launch a container from inside ITEMIZE and wait) is the least code
and the same latency locally, but it blocks an ingest worker per file and
means one container launch per file, which does not survive M3-D's
concurrency or NOAA rates. Through the ledger, batches form naturally under
load (§6.4), every extraction is visible in run history, limits/retries/logs
come for free, and the container-per-run cost is paid once per batch. With
the immediate dispatch of §5 the single-file latency is the same as inline.

### 6.4 Coalescing for queued runs

Today's coalescing only merges into a **rate-deferred** run. Extractors (and
ordinary transforms) also merge into a run that is **queued and not yet
claimed**: the existing partial unique index (deferred queued runs per
source) widens to **all** queued runs per `(process_id, source_id)`, and
`enqueue_run` upserts against it, appending `input_items`. A burst of files
inside one container start becomes one batch; a lone file goes straight
through. The claim statement moves the row to `running`, which takes it out
of the index atomically, so a late append after claim inserts a new run
instead — no lost items, no double-processing.

### 6.5 Defaults that differ for extractors

- `max_runs_per_hour` default **600** (a 1-minute mesoscale product at one
  file per run is 60/h; coalescing absorbs bursts above that).
- `timeout_seconds` default **120** (an extractor that needs longer is
  configured, not assumed).
- Same memory default.

### 6.6 Graph

An extractor is drawn as a display-only edge `extractor` from the connection
to the collection alongside the `ingest` edge, **not traversable** by the
cycle check (the same treatment `ingest`/`deliver` already get). It produces
no `process_output` edge, so it cannot participate in a collection↔process
cycle.

## 7. Serving

### 7.1 Tile server href mapping (I-68, local answer)

A derived image `infra/titiler/` (`FROM ghcr.io/stac-utils/titiler-pgstac:1.7.2`, the pin compose uses today)
carrying one module that overrides `_get_asset_info` on **both** readers —
`PgSTACReader` (item endpoints) and `SimpleSTACReader` (mosaic/collection
endpoints) — to map

`/api/assets/{collection}/{item}/{filename}` → `s3://{PLATFORM_ASSET_BUCKET}/assets/{collection}/{item}/{filename}`

and leave every other href untouched. The mapping is a string substitution
because the canonical key layout (ADR 0005 §5.3) is deterministic. The image
is otherwise the upstream one: same env, same command, same version pin as
compose uses today. Nothing changes in what the catalog stores, so the
cloud-time options I-68 lists (absolute hrefs, presign integration) remain
open; this closes the *local* half and the ISSUES entry says so.

The module is written against the pinned rio-tiler 7 signature and noted as
the one place to touch when titiler-pgstac is bumped to 3.x (rio-tiler 9
changed the return shape).

### 7.2 Raster preview on the item page

When the collection's `serving_enabled` toggle is on and the tile server's
item `info` call succeeds, the item page's map adds a raster layer from the
tile server's TileJSON for the item (`assets=visual` when the item has a
`visual` asset; otherwise the first raster asset, with the server's default
rendering). Absent or failing tile server → no layer, no error banner (the
Settings tab already explains serving). Uses the existing `StacMap`; the
layer is a shared component so it is reusable on collection pages later.

## 8. Anonymous S3 connections

`s3ConfigSchema` gains `anonymous?: boolean`. When true, credentials are
optional (the credentials envelope may be empty), the UI hides the key
fields, and the pipeline's `S3Adapter` builds its client with
`Config(signature_version=UNSIGNED)`. `reference` mode's public URL
generation is unchanged. `resolve_pinned` still applies (NODD resolves to
public addresses). Contract fixture: the s3 connection config fixture gains
an anonymous case.

## 9. The GOES worked example

Checked into `docs/processes.md` as the worked example (replacing the
placeholder mask example) and into the e2e spec as fixtures. Both run on the
**current** runtime image: rasterio's bundled GDAL reads netCDF, numpy comes
with rasterio, and GDAL's COG driver writes the output. No image change.

**Product:** `ABI-L2-MCMIPC` from `noaa-goes19` (GOES-East since 2025-04-07):
one ~50 MB file every 5 minutes, all 16 bands at 2 km, 2500×1500.

**Extractor (`goes-abi-metadata`):** for each item, parse
`_s(\d{14})` from the filename (`YYYYDDDHHMMSS` + tenths) into `datetime`,
and read the netCDF global attributes through rasterio's dataset tags
(`platform_ID`, `scene_id`, `timeline_id`, `instrument_type`) into
`properties.platform`, `properties.instruments`, and `goes:*` fields. Leave
geometry as drafted (the built-in netCDF path already derives it).

**Process (`goes-geocolor`):** for each item, open `CMI_C01`, `CMI_C02`,
`CMI_C03`, `CMI_C13` via `NETCDF:"/vsis3/{bucket}/{key}":CMI_Cxx`. Compute
red = C02, blue = C01, green = 0.45·red + 0.10·C03 + 0.45·blue; clip to
[0, 1]; gamma 2.2; night IR = 1 − normalise(C13 brightness temperature, 90 K
to 313 K) blended in by per-pixel maximum; scale to 8-bit; 0 = nodata off the
disk (where CMI is fill). Write a 3-band uint8 COG (deflate, 512 blocks,
overviews, internal mask) in the file's native geostationary CRS and
transform as read by rasterio. Emit the item with `rio-stac`
(`with_proj=True` for `proj:*` including the geostationary WKT2, since it has
no EPSG code), `datetime` from the source item, the source item id plus
`-geocolor`, and one asset `visual` with role `visual`. About 60 lines.

This is the CIMSS/goes2go true-colour recipe, labelled "GeoColor-style" in
the docs: it has no Rayleigh correction and no city lights. CIRA's GeoColor
proper is not fully open (the synthetic-green lookup tables are unpublished);
a Satpy-based runtime image is the documented next step (§11).

## 10. Verification

- **Unit / contract (both runtimes):** the manifest fixture; the runtime
  fixture's `network` block including cap violations; the s3 anonymous
  fixture; `enqueue_run` coalescing on queued rows (race: append after claim
  inserts a new run); the finalize resolver skipping `inputs/`; the extract
  branch of finalize (id/collection/href immutability, missing output →
  failed rows); the `extracting` ledger transitions; the STS session policy
  with source-collection read statements; `_get_asset_info` mapping in the
  titiler module (pytest in `infra/titiler/`, no network).
- **Loadgen:** the existing harness gains an `--extractor` profile (a
  pass-through extractor) so the extractor path is measured at M3-D
  concurrency, and a check that immediate dispatch does not double-run
  periodic jobs.
- **E2E:** `app/e2e/goes-loop.spec.ts`, skipped unless `E2E_LIVE_NODD=1`
  (it needs the internet, Docker, and the pipeline — none of which the suite
  requires today). It lists the public bucket's current-hour prefix over
  plain HTTPS to pick the newest MCMIPC key, scopes the association's
  `include` to that one file, and polls the app's API for the four gate
  outcomes with a generous bound (10 minutes). Documented in the `run-e2e`
  skill. A separate manual recipe covers running it against the full hour.
- **Docs:** `docs/processes.md` (inputs contract, network profile,
  extractors, the worked example), `docs/connections.md` (anonymous),
  `docs/serving.md` (mapping, preview layer), `docs/FEATURES.md`,
  `docs/ISSUES.md` (I-68 local half closed; new issues §14), ADR 0018.

## 11. What this spec explicitly does NOT do

- **The egress proxy** that realises `inputs`, `hosts`, `open`: a forward
  proxy sidecar on the internal network with per-run credentials mapping to
  a per-run allowlist, runs pointed at it through the standard proxy env
  (boto3, GDAL and requests all honour it; the internal network makes a
  non-honouring client fail rather than bypass). Slice 2, its own spec,
  validated by the VirtualiZarr use case (which also needs a runtime image
  with xarray/virtualizarr and a second serving component, titiler-xarray).
- **Warm runs:** a run that stays alive and drains further batches for the
  same process until idle. The `batch_id` layout is the only provision made.
  Needed when the executor is Fargate (Phase 8).
- **Event-driven DISCOVER** from NODD's SNS topics via SQS. Cloud.
- **A Satpy runtime image** (Rayleigh correction, `geo_color` night layer).
  Follow-up once the loop is proven.
- **Rate-ceiling exemptions**, per-run CPU limits, a run cancel verb (I-81).
- **`container` runtime kind.** Still refused (ADR 0013 slice-1 scope).

## 12. Decisions — settled with the lead 2026-09-01

- Inputs: staged manifest, always a list; platform-held bytes granted by
  source-collection prefix, remote bytes staged in. Option C overall: staged
  now, direct access as an opt-in level later.
- Network profile with four levels and a deployment maximum
  (`PROCESS_NETWORK_MAX`); slice 1 ships the field with `isolated` only.
- Extractors are processes (`kind: extractor`) selected on the ingest
  association; wired through the run ledger with immediate dispatch and
  queued-run coalescing; failure fails the file, never falls back.
- Latency posture: every hop after DISCOVER enqueues the next immediately;
  S3 sources skip the second settle poll.
- Tiling: derived tile-server image with an href mapping; preview layer on
  the item page in slice 1.
- Use case: `ABI-L2-MCMIPC` from `noaa-goes19`, numpy true-colour + night IR
  on the current runtime image, extractor supplies the datetime.
- E2E against the **live** NODD bucket, gated by an env flag.

## 13. Slices (implementation queue draft for TODO.md)

Dependency order; G-1/G-4/G-5 are independent of each other and of the rest.

| # | Slice | Depends on |
|---|---|---|
| G-1 | Anonymous S3 connections (schema, UI, adapter, fixture) | — |
| G-2 | Process inputs: manifest, staging, session-policy read grants, resolver skip, env vars, `network` field + cap with `isolated` only, ADR 0018, docs | — |
| G-3 | Latency posture: immediate dispatch at the three hops, `settle: immediate` for S3, queued-run coalescing | G-2 (shares `enqueue_run`) |
| G-4 | Tile server image with href mapping + compose switch + serving docs | — |
| G-5 | Raster preview layer on the item page | G-4 |
| G-6 | Extractors: `kind` column, association strategy, `extracting` state, extract finalize branch, graph edge, UI (kind badge, association picker), loadgen profile | G-2, G-3 |
| G-7 | GOES worked example (extractor + process code, docs) and the gated e2e spec; `run-e2e` skill update | G-1…G-6 |

**Touchpoints with the live M3 queue** (the lead sequences; nothing here
blocks M3-A): M3-C replaces the EXTRACT byte-source seam with a URI — G-6's
extractor receives a *location*, never a buffer, so it is unaffected; G-3's
immediate enqueues should be exercised at M3-D's concurrency setting before
either is declared done.

## 14. Risks and issues to open

- **Session-policy size.** A run with many distinct source collections could
  exceed real STS's 2048-character inline policy (MinIO is laxer). Slice 1
  grants per source collection (bounded by `process_sources`, typically 1–3)
  and refuses launch with a clear error past a documented count. Log as an
  issue for the cloud backend.
- **Immediate-dispatch fan-in.** Under M3-D concurrency, N ITEMIZE jobs
  enqueue N `dispatch_poll` jobs; each is cheap and idempotent, but the plan
  measures it (loadgen) and adds a short `queueing_lock` if the queue table
  shows churn.
- **Extractor draft size in `input_items`.** A draft item is a few KB; a
  batch of hundreds is under a MB of jsonb. Acceptable; noted.
- **Reference-mode staging for extractors** doubles bytes moved for
  reference-mode + extractor associations until slice 2. Documented as a
  known cost.
- **Live-data e2e** depends on NOAA availability and the internet; it is
  gated and excluded from CI's default run. Log an issue to add a seeded
  offline variant if it proves flaky.
- **titiler-pgstac 3.x bump** will require re-porting the mapping module
  (return-shape change). Note in `docs/serving.md`.
- **Geostationary CRS on the web map.** The tile server reprojects on the
  fly; off-disk pixels rely on the COG's mask. Verified by the e2e tile
  fetch, not by pixel inspection.

## 15. G-6 / G-7 planning addendum (2026-09-02, lead-approved)

Decisions taken while writing the G-6 and G-7 plans, after reading the code
the slices touch and probing the runtime image. Where these differ from the
sections above, this addendum wins.

- **Extractor runs coalesce on the association, not a source.** §6.4 keys
  coalescing on `(process_id, source_id)`, but §6.1 forbids `process_sources`
  rows on an extractor, and `enqueue_run` only takes the `ON CONFLICT` path
  with a source. Migration 027 adds a nullable `process_runs.association_id`
  and a second partial unique index `(process_id, association_id) WHERE
  status = 'queued'`; extractor triggers pass the association and coalesce
  against it. Transform runs are untouched.
- **Ledger rows carry a reason and a run id.** §6.2 says failed rows record
  the reason; `ingest_files` had no such column. Migration 027 adds
  `reason text`, `extract_run_id uuid`, and `source_mtime timestamptz` (the
  I-100 fix: DISCOVER persists the listed modified time and `file_mtime`
  prefers it over the settle time).
- **Rows fail at terminal `dead`, not on the first failed attempt.** A run's
  own retry may still succeed; ledger rows stay `extracting` through
  retryable failures and fail (with the run's error) when the run reaches
  `dead`. A sweep alongside `sweep_stuck_stored` fails `extracting` rows
  whose run is dead or missing, or whose `extract_run_id` was never stamped.
- **Read grants for extractor runs derive from the association's
  collection.** The transform path grants `assets/{collection}/` per
  `process_sources` row; an extractor has none, so the run planner uses the
  collection ids in `input_items` instead. Copy-mode files are read in place;
  reference-mode files are staged as §6.2 says.
- **The graph edge is process → collection.** §6.6 drew connection →
  collection, but graph nodes are minted only from edges, so the extractor
  process would never appear on `/monitoring`. The display-only `extractor`
  edge now runs from the extractor process node to the ingest collection;
  still not traversable by the cycle check.
- **The worked example downloads inputs to local disk.** §9's
  `NETCDF:"/vsis3/…"` does not work in the runtime image: GDAL's netCDF
  driver needs Linux userfaultfd for any `/vsi` path (verified 2026-09-02 on
  `stac-higher-process-runtime:local`, rasterio 1.5.1 / GDAL 3.12.4). Both
  GOES scripts fetch the staged object with boto3 to a temp file and open
  `NETCDF:"{path}":CMI_Cxx`. netCDF, HDF5 and COG drivers, numpy and rio-stac
  are all present, so §9's "no image change" holds.
- **The extractor sets the footprint (I-101).** §9 assumed the built-in
  netCDF path derives geometry; it does not for MCMIPC — the container
  dataset has no georeferencing, only its subdatasets do. The extractor
  reads the `CMI_C02` subdataset's bounds and CRS and reprojects them to
  WGS84. The built-in `raster_auto` path is unchanged; I-101 stays open
  annotated with the cause.
- **One home for the GOES code.** `services/pipeline/src/pipeline/demo/goes/`
  holds `extractor.py` and `geocolor.py`; `pipeline.demo goes` seeds the whole
  loop against the live bucket (the manual full-hour recipe), the gated e2e
  reads the same files, and `docs/processes.md` quotes them.
- **Extractor policy in the app.** Create/update of an ingest association
  with `strategy: extractor` verifies the process exists, has
  `kind = 'extractor'`, and has the connection's `group_id` ("owned by the
  association's group" in §6.1). Soft-deleting an extractor named by any
  association is a 409. `kind` is create-only.
- **Migration numbering.** G-6 takes **027**; K-3 moves to 028.
- **The run planner resolves reference-mode canonical hrefs through the
  ledger (Task 9b).** The planner treated every canonical `/api/assets/...`
  href as platform-held, but a reference-mode item is catalogued with a
  canonical href while its bytes stay at the connection's own source (the app
  resolves this via `lookupReferenceHref`); granting a read on the canonical
  key would point at an object that does not exist. `ProcessRepo.
  reference_source_hrefs` mirrors that lookup so the planner can stage such
  assets from `ingest_files.source_href` instead — for both transform and
  extract runs. This closes a gap G-2 shipped with, found while wiring G-7's
  reference-mode GOES flow.

## 16. §9 addendum — the night side (G-8, 2026-09-04)

§9's night branch was scoped as the inverted C13 blended in by per-pixel
maximum, and it worked as written: measured 2026-09-04, a night COG is ~1 %
colour pixels against 57 % for a daytime one, so the "black and white" the
lead saw was the grey night layer, not a broken true-colour path. G-8 changes
`compose()` only, and only in two ways. **The ramp:** the inverted brightness
temperature `night = 1 − clip((K − 90) / (313 − 90), 0, 1)` now drives a
linear interpolation between `NIGHT_WARM_RGB = (0.02, 0.05, 0.18)` — a deep
blue for warm surfaces and low cloud — and `NIGHT_COLD_RGB = (1, 1, 1)` for
the coldest tops, per channel, instead of being written to all three as grey.
**The blend:** day and night are mixed by the per-pixel solar zenith angle
rather than by `np.maximum`, linearly across a twilight band of
`TWILIGHT_DAY_DEG = 80` to `TWILIGHT_NIGHT_DEG = 96` degrees, so dusk fades
instead of flipping. The angle comes from `solar_zenith()`: pixel centres
every `ZENITH_STEP = 64` pixels reprojected to WGS84 (bisecting to isolate the
off-limb samples GDAL refuses, then nearest-filling them), the NOAA
low-precision sun position at the granule's scan time, and a bilinear
upsample — no new dependency, and the sampling error is two orders of
magnitude inside the twilight band. `compose()` takes the zenith array as a
fifth argument, since it cannot be derived from the radiances. City lights
stay out of scope (they need a static reference-asset input the platform does
not have — I-106).

Measured 2026-09-04 on three real `noaa-goes19` MCMIPC granules from
2026-09-03 (DOY 246), run through the process's own code — `read_band` →
`solar_zenith` → `compose` — with the pre-G-8 `compose()` alongside it for a
same-granule before/after. Colour = `|r−g| > 8 or |g−b| > 8` over the on-disk
pixels:

| granule | solar zenith | in the 80–96° band | colour before | colour after |
|---|---|---|---|---|
| 07:01:17Z (night) | 105.6–157.2° | 0.0 % | **0.0 %** | **100.0 %** |
| 11:01:17Z (terminator) | 69.9–123.3° | 35.0 % | 13.9 % | 91.4 % |
| 18:01:17Z (day) | 6.9–61.9° | 0.0 % | 62.5 % | 62.5 % |

The day granule's output is **bit-identical** before and after — zero pixels
changed, maximum channel delta 0 — so the true-colour path is provably
untouched, and the 11:01Z granule (a third of the scene inside the twilight
band) is what exercises the blend rather than just the ramp.
