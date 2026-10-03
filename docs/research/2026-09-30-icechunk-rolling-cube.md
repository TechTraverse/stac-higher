# Silo can host a rolling virtual GOES cube

- **Kind:** research (2026-09-30), not a spec. It informed
  [ADR 0022](../decisions/0022-virtual-cube-sink.md), which chose the
  platform-owned cube sink (Option B below).
- **Question:** can stac-higher append each new NODD file as strictly virtual
  references to an Icechunk repository with a rolling window, publish it as a
  collection asset on the local Silo stack, and serve it as map tiles and a
  cube API? Which NODD product demonstrates that best?
- **Evidence:** public docs and repos (linked inline), anonymous listings of
  the NODD buckets, and a local probe against the Silo digest pinned in
  `docker-compose.yml`. The probe is reproducible from the appendix.

The storage and data layers work today; the platform does not yet. A local probe on 2026-09-30 tested icechunk 2.2.2, virtualizarr 2.7.3 and zarr 3.4.0 against the exact `pgsty/silo` digest pinned in `docker-compose.yml`. It created an Icechunk repo, appended real GOES-19 NetCDF files as **strictly virtual references** (no NODD bytes copied into the store), rolled the window with a **metadata-only `shift_array` + resize**, detected a concurrent-commit conflict, and expired and garbage-collected old snapshots. It needed no `unsafe_*` overrides, because Silo correctly enforces `If-None-Match: *` and `If-Match` on PutObject. What stac-higher lacks is everything around the store. Its process model publishes items, not collection mutations. It runs at-least-once with no per-process serialization. It cannot grant a run a persistent store prefix, and it cannot reach NODD from a sandboxed run without staging a full copy, which breaks the "no copying" constraint until the egress proxy (#68) lands. Retention, GC, serving and the preview map all key on items, not on a cube's time coordinate. The best demonstration product is **GOES-19 ABI L2 CONUS imagery (`ABI-L2-CMIPC` band 13, with `ABI-L2-MCMIPC` as the multi-band stretch)**. Its NetCDF4 internal chunks virtualize with plain zlib/shuffle codecs, it publishes every 5 minutes, it reuses the team's GOES code, and apparently no one publishes it as a real-time virtual cube. The best server is a **small custom xpublish app (xpublish-tiles + xpublish-edr)**, because it is the only one with verified geostationary handling and full control over Silo endpoints and virtual-chunk credentials. About eleven gaps stand between today's repo and a demo. Three of them are ADR-level.

## The storage layer passed every test that mattered on Silo

Icechunk's safety rests on one mutable object. The spec defines `repo` as "the only mutable object in an Icechunk repo", updated by a conditional write so that concurrent commits serialize ([Icechunk spec v2.1](https://icechunk.io/en/latest/reference/spec-v2-1/)). Turning those conditional writes off is allowed, but the docs warn that concurrent commits mean "one of them could get lost" ([Icechunk storage guide](https://icechunk.io/en/latest/guides/storage/)). Silo is a MinIO fork, and Icechunk documents MinIO as a supported S3-compatible store with `endpoint_url`, `allow_http=True`, `force_path_style=True` and region `us-east-1` (same source). The local probe went further than that documentation. A raw aws-cli check showed `--if-none-match '*'` returning 412 on an existing key and `--if-match` with a stale ETag returning `PreconditionFailed`. Icechunk then created a spec-v2 repo on Silo and committed three sequential virtual appends in **1.2–1.8 s each**, including remote HDF5 header parsing from a laptop. A stale-parent commit raised `ConflictError`, which proves the conditional update runs end to end. Icechunk's Rust client signs requests with SigV4, and no signature errors occurred against Silo. None of this is in the repo today. Issue #48's checklist for choosing a local object store leaves out conditional writes, and no repo doc mentions them ([#48](https://github.com/TechTraverse/stac-higher/issues/48)). Add a four-line conditional-PUT probe to that checklist and re-run it on every Silo digest bump.

The software is mature enough to pin. Icechunk 1.0 shipped 2025-07-10, 2.0 shipped 2026-04-08 with on-disk spec v2, and 2.2.2 is the current stable release (2026-09-17) ([PyPI icechunk](https://pypi.org/project/icechunk/)). VirtualiZarr 2.7.3 shipped 2026-08-07 ([PyPI virtualizarr](https://pypi.org/project/virtualizarr/)). One research note cited 2.7.2 from a search snippet, but PyPI and the local test both used 2.7.3. Icechunk 2 also brought the feature that makes a rolling window cheap: `shift_array` moves chunk references **in chunk units**, discards whatever falls off the end and leaves vacated positions at the fill value. The documented use case is rolling time windows ([Moving Chunks guide](https://icechunk.io/en/latest/guides/moving-chunks/)), done "without ever copying a single byte" ([Icechunk 2 announcement](https://www.earthmover.io/blog/announcing-icechunk-2-better-consistency-performance-and-reliability-for-tensor-storage)). The probe confirmed this on virtual GOES refs. It shifted `CMI`, `DQF` and `t` by −1 chunk, resized, and committed, and the cube went from four timesteps to three with the virtual data still reading correctly. `expire_snapshots` followed by `garbage_collect` then deleted 18 manifests, 3 snapshots and about 39 KB of Silo objects. GC is irreversible and touches only objects under the repo prefix ([Expiration docs](https://icechunk.io/en/stable/understanding/expiration/)). It cannot delete NODD sources, because those were never the platform's bytes. One constraint shapes the whole design: `shift_array` requires a regular chunk grid, so **every time-dimensioned variable needs a time chunk of 1**. That is the natural layout when each file adds one timestep.

Two Icechunk behaviours force platform design. First, **concurrent appends along the same dimension can never auto-rebase**. In the probe, session B appended while A committed, and `BasicConflictSolver` failed with 11 conflicts. Ingest therefore needs exactly one writer per repo. Parsing can be parallel, but write+commit must be serialized, and a writer that hits `ConflictError` should reopen from the branch tip and redo the work rather than rebase. Second, **every reader must opt in** to each virtual-chunk prefix with `authorize_virtual_chunk_access`. A reader that does not raised `InvalidInputError` in the probe ([Icechunk virtual guide](https://icechunk.io/en/latest/guides/virtual/)). The writer must declare a `VirtualChunkContainer` per NODD bucket (with a trailing slash). Scoping it to `s3://noaa-goes19/` rather than `s3://` keeps a repo from pulling arbitrary buckets.

## GOES-19 CONUS beats HRRR, RTMA and MRMS as the virtual cube

Under the strict-virtual rule the deciding question is whether a file's internal layout maps to decodable byte ranges that a standard codec can read. GOES-19 ABI L2 files map cleanly. `ABI-L2-CMIPC` band 13 stores `CMI` as int16 in **52 × 2500 chunks with shuffle + deflate 5** on a 1500 × 2500 CONUS grid, at about 3.96 MB every 5 minutes ([CMIPC C13 sample file](https://noaa-goes19.s3.amazonaws.com/ABI-L2-CMIPC/2026/272/12/OR_ABI-L2-CMIPC-M6C13_G19_s20262721201171_e20262721203555_c20262721204060.nc)). `ABI-L2-RRQPEF` rainfall rate stores 24 × 5424 strips on the full disk every 10 minutes, with 144 files per day confirmed ([RRQPEF listing](https://noaa-goes19.s3.amazonaws.com/?list-type=2&prefix=ABI-L2-RRQPEF/2026/272/12/)). Both need only zlib and shuffle on the read side. The probe showed the CF packing (`scale_factor`, `add_offset`, `_Unsigned`, `_FillValue`) surviving virtualization, with C13 decoding to plausible 241–249 K brightness temperatures. GOES-19 is operational GOES-East and has its own SNS topic, `NewGOES19Object` ([AWS Registry: NOAA GOES](https://registry.opendata.aws/noaa-goes/)). Year prefixes back to 2024 (and GOES-16 back to 2017) show that sources outlive any hours-to-days window. No formal retention SLA exists, though, and outages happen: GOES-19 L2 products went missing for about 26 hours in April 2026 ([OSPO message](https://www.ospo.noaa.gov/data/messages/2026/04/MSG_20260408_2350.html)). The writer must tolerate gaps.

The alternatives each fail on a different axis. **HRRR** is the richest "model cube", and gribberish 1.0's `GribberishParser` makes it virtualizable ([gribberish README](https://github.com/mpiannucci/gribberish/blob/main/python/README.md)). But each GRIB message becomes one non-splittable chunk, so a point time series costs about 0.4–0.5 MB per variable per step. The gribberish codec would also have to be installed in every reader, tile server included. And dynamical.org already publishes virtual Icechunk HRRR, GFS and GEFS with a 2.9 s median ingest latency ([dynamical.org](https://dynamical.org/research/virtual-data-products/); [stac.dynamical.org](https://stac.dynamical.org/catalog.json)). That makes it a useful correctness reference but a non-novel demo. One source conflict is unresolved. Earthmover says gribberish "cannot decode JPEG2000 or complex packing schemes" ([Earthmover: Virtually Gribberish](https://www.earthmover.io/blog/virtual-grib-nbm)), yet NODD HRRR uses DRT 5.3 complex packing and dynamical.org decodes it with gribberish. **RTMA Rapid Update** has an attractive 15-minute cadence, but its simple-packed messages are about 7.5 MB each, so a 24-hour single-variable point series reads about 720 MB ([RTMA-RU .idx](https://noaa-rtma-pds.s3.amazonaws.com/rtma2p5_ru.20260929/rtma2p5_ru.t0000z.2dvaranl_ndfd.grb2.idx)). **MRMS** is disqualified outright. Every file is whole-file gzip around PNG-packed GRIB2, and a non-seekable gzip stream has no byte range that maps to a chunk ([MRMS listing](https://noaa-mrms-pds.s3.amazonaws.com/?list-type=2&prefix=CONUS/PrecipRate_00.00/20260929/)). GOES Mesoscale sectors move between files, which breaks a fixed-grid append. NWM's registry page promises a "rolling four week archive", but the bucket actually starts at 2025-01-01, so its retention is unclear ([AWS Registry: NWM](https://registry.opendata.aws/noaa-nwm-pds/); [NWM listing](https://noaa-nwm-pds.s3.amazonaws.com/?list-type=2&delimiter=/)).

Within GOES, the choice is between visual impact and a meaningful time series. CMIPC C13 is CONUS-framed, so low-zoom tiles read a 1500 × 2500 frame rather than a 5424² full disk. Its 288 frames a day also make the most compelling animation of convection. RRQPEF yields the more intuitive point series (mm/h through a storm), but it is full disk only and is zero most of the time in dry areas. The recommendation is to **build the spike on CMIPC C13**, which is the path verified end to end, and treat **MCMIPC** as the stretch. MCMIPC carries all 16 bands at 2 km in one file, so it adds a variable dimension. Because those bands share one resolution, it also allows a lazy, copy-free RGB stack for a GeoColor-like rendering. The existing demo already ingests `goes-abi-mcmipc`, so this also reuses its association ([seed.py](../../services/pipeline/src/pipeline/demo/goes/seed.py)). MCMIPC's internal chunking has not been inspected yet, so the spike must verify it.

## xpublish with tiles and EDR fits better than titiler-multidim

The deciding factors are how a server opens an Icechunk repo on a custom endpoint with per-prefix virtual-chunk credentials, and whether it understands the GOES fixed grid. **titiler-multidim** (v0.9.1, 2026-09-22) has the best-documented virtual-chunk config, `TITILER_MULTIDIM_AUTHORIZED_CHUNK_ACCESS` with `{"anonymous": true}` per prefix. It falls short on three points ([titiler-multidim README](https://github.com/developmentseed/titiler-multidim#readme)). Its `s3_storage` opener passes **no `endpoint_url`**, so reaching Silo depends on an unverified environment-variable fallback ([reader.py](https://github.com/developmentseed/titiler-multidim/blob/main/src/titiler/multidim/reader.py)). It hard-codes branch `main`. And it takes the dataset `url` from the client, which its own README warns "effectively publishes the data under" any authorized prefix. That client-supplied-URL design clashes with stac-higher's SSRF and egress posture. Its rendering also relies on rioxarray with 1-D x/y. GOES scan angles in radians would produce a transform roughly 35,786 km too small unless a custom opener rescales them ([titiler.xarray io.py](https://github.com/developmentseed/titiler/blob/main/src/titiler/xarray/titiler/xarray/io.py)).

**xpublish-tiles** (0.9.2, Earthmover, Apache-2.0) ships an explicit `Geostationary` grid class. It multiplies x/y scan angles by `perspective_point_height` and computes the visible-disk bounding box analytically ([grids.py](https://github.com/earth-mover/xpublish-tiles/blob/main/src/xpublish_tiles/grids.py)). Its bundled CLI opens Icechunk with no endpoint and no `authorize_virtual_chunk_access` ([cli/main.py](https://github.com/earth-mover/xpublish-tiles/blob/main/src/xpublish_tiles/cli/main.py)). The fix is to skip the CLI and write a small FastAPI app. That app opens the repo itself (Silo endpoint, `s3_anonymous_credentials()` for `s3://noaa-goes19/`, any branch or snapshot) and hands the dataset to `xpublish.Rest` with `TilesPlugin` and `EdrPlugin`. The app serves only server-registered datasets, which fits a BFF far better than a client-supplied URL. Adding **xpublish-edr** turns the tiler into a cube API: position (time series), area and cube queries, returning CoverageJSON, CSV, NetCDF or GeoTIFF ([xpublish-edr](https://github.com/xpublish-community/xpublish-edr)). That pairing (MapLibre tiles plus a click-to-EDR time series) is exactly the shape Earthmover's commercial Flux demos ([flux-web-demo](https://github.com/earth-mover/flux-web-demo)), and Development Seed recommends Xpublish for "operational scientific data on native irregular grids, OGC EDR queries" ([ecosystem comparison](https://developmentseed.org/datacube-guide/latest/visualization/ecosystem-comparison.html)). Flux itself is out of scope because it requires the repo in commercial Arraylake, not local Silo ([Announcing Flux](https://www.earthmover.io/blog/announcing-flux/)). Browser-side reading through icechunk-js plus zarr-layer is not viable either. zarr-layer's codec list lacks HDF5 shuffle, and single-level high-resolution stores "require multiscales" ([carbonplan/zarr-layer](https://github.com/carbonplan/zarr-layer)).

The costs are real. xpublish-tiles holds a long-lived dataset, so a rolling cube needs a background task that polls `main`, reopens the session on each new snapshot and sets `_xpublish_id` to the snapshot ID so the grid cache invalidates. The first render also blocks on numba JIT warm-up ([xpublish-tiles README](https://github.com/earth-mover/xpublish-tiles#readme)). Strictly virtual also means no overviews, so a z0–z4 tile reads every 52-row strip of the frame, and the probe found that decoded data defaults to float64. With CONUS that is about 29 strips per frame per variable, which is tolerable. With full disk it would not be. Two things remain untested and must go first in the spike: xpublish-edr position queries on geostationary radians, and any published latency benchmark for either server on virtual stores. **titiler-multidim with a forked opener** stays the fallback for tiles, offering `/point` but no EDR.

## The platform gaps sit in processes, retention and serving

The current GOES loop runs connection → reference ingest → extractor → transform process → finalize → titiler-pgstac. The first three links carry over ([GeoColor loop spec](../../docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md)). The last three break, because a cube append mutates one store instead of publishing new items. The spec author anticipated this: network "Slice 2" is to be "validated by the VirtualiZarr use case (which also needs a runtime image with xarray/virtualizarr and a second serving component, titiler-xarray)". No issue tracks this work yet, and a repo-wide search finds no Icechunk, VirtualiZarr or xpublish code. The hardest conflict concerns the strict-virtual constraint itself. Process runs are network-`isolated`, so reference-mode inputs are **staged as full copies** into `staging/runs/{run_id}/inputs/` before the run sees them ([docs/processes.md](../../docs/processes.md)). The finished store would still be virtual, but NODD bytes would be copied transiently, and that breaks the user's constraint. VirtualiZarr needs only header byte ranges, so the fix is network egress for the run, and the `inputs` level is blocked on #68.

That suggests a deliberate architectural fork. **Option A** keeps the appender a user process. It needs #68 egress, a new persistent store-scoped STS grant (runs today can write only `staging/runs/{run_id}/`), a single-flight claim (`claim_due_runs` has no per-process exclusion, and `process_run_now` has no `queueing_lock` ([repo.py](../../services/pipeline/src/pipeline/process/repo.py))), a new runtime image alias with a contract fixture, and a collection-level output kind. **Option B** makes the cube a **platform-owned "cube sink" job** in the pipeline, a sibling of ingest rather than a sandboxed process. The pipeline already reaches NODD through anonymous S3 connections. Procrastinate's `queueing_lock` or a per-collection advisory lock gives serialization almost for free. The pipeline can also write the collection asset, extent and `cube:dimensions` directly. Option B costs heavier pipeline dependencies (zarr, icechunk, virtualizarr, obstore). It also needs an egress caveat: Icechunk's and obstore's own HTTP clients bypass `resolve_pinned`, so the allow-list would have to move to the network layer. Option B is the shorter path to a demo and keeps the strict-virtual promise without waiting on #68. Option A is the more general product feature. That choice deserves an ADR.

Several gaps apply under either option. Retention keys on `pgstac.items.datetime` and deletes bytes only through the `asset_gc` prefix queue ([ADR 0011](../../docs/decisions/0011-retention-gc.md)). An Icechunk trim (shift + resize) plus `garbage_collect` deletes Silo objects outside that queue, so the "byte deletion only through `asset_gc`" invariant needs an amendment declaring store-internal GC store-managed. One mitigation keeps a reference-mode item per NODD file beside the cube. W-1's `window` and W-2's `retention_max_items` would then provide a ready-made catalog view of the window, and the cube trim can read the same N ([SettingsTab.tsx](../../app/src/components/collections/SettingsTab.tsx)). On the map side, `RasterTileLayer`, `RasterFrameStack` and `TimeSlider` are tile-source agnostic and need no change. But `buildPreviewFrames` makes one frame per item datetime, the URL builders are titiler-pgstac-only, and the Preview tab hides itself unless *item* assets are tileable ([frames.ts](../../app/src/lib/serving/frames.ts); [urls.ts](../../app/src/lib/serving/urls.ts)). The cube needs Zarr URL builders, a `PUBLIC_ZARR_TILER_URL`, a frame source built from the cube's time coordinate (STAC `cube:dimensions.time.values` or the server's metadata) and a Preview branch keyed on a collection-level asset. Tile auth is no worse than today. titiler and tipg are unauthenticated, and the serving toggle stays advisory until I-1 (#34) lands ([docs/serving.md](../../docs/serving.md)). Representing the cube in STAC has a working precedent but no ratified standard. VEDA uses a collection asset with `application/vnd.zarr+icechunk` (explicitly TBD), roles `["data","references","virtual","latest-version"]`, a `version` set to the snapshot ID and Storage extension v2 schemes ([VEDA notebook](https://docs.openveda.cloud/user-guide/notebooks/veda-operations/publish-cmip6-virtual-zarr-stac.html)). STAC best practices add a `rel: "store"` link ([stac-best-practices Zarr](https://github.com/radiantearth/stac-best-practices/blob/main/best-practices-zarr.md)). No standard field carries the virtual-chunk-container prefixes a reader must authorize, so a namespaced custom field is needed.

### Recommended architecture and prioritized gap list

The demo loop runs as follows. An existing reference-mode association polls `noaa-goes19/ABI-L2-CMIPC/{Y}/{j}/{H}/` with an `include` filter for C13 and a `-6h` window, which turns each file into a reference item. Item arrival triggers a single-writer cube job. That job parses the header in place and appends `CMI`, `DQF`, `t` and `goes_imager_projection` (the last two as loadable variables). When length exceeds N it applies `shift_array` + resize in the same commit, then updates the collection's Icechunk asset `version`, temporal extent and `cube:dimensions`. The repo lives at `s3://stac-higher/icechunk/{collection}/` on Silo. An hourly cron expires and garbage-collects old snapshots. A new compose service, `cube-server` (xpublish + xpublish-tiles + xpublish-edr), opens the repo with Silo credentials and anonymous `s3://noaa-goes19/` authorization and reopens it on each new snapshot. The Preview tab animates frames taken from the time coordinate, and a map click charts an EDR position time series. SNS→SQS discovery stays deferred. The ≥60 s poll costs latency, not feasibility, against a 5-minute cadence.

| Priority | Gap / decision | Why it blocks | Likely home |
|---|---|---|---|
| 1 | Where the appender lives: user process (A) vs platform cube-sink job (B) | Determines whether #68, the store grant and the output-kind work are prerequisites | [ADR 0022](../decisions/0022-virtual-cube-sink.md) (chose B) |
| 2 | Serialized single writer per repo (single-flight claim, `queueing_lock`, or profile capacity 1) | Concurrent appends never rebase. At-least-once retries can replay an append, so the writer must also skip timesteps already present | Issue + migration + contract fixture (runtime settings) if A; Procrastinate lock if B |
| 3 | Fetch NODD headers without staging copies | Staging copies break "strictly virtual" | #68 (egress proxy, `inputs` level) if A; network-layer allow-list for Icechunk/obstore clients if B |
| 4 | Persistent store-scoped write grant (`icechunk/{collection}/`) | Runs can write only `staging/runs/{run_id}/`, and finalize cannot move a repo | ADR amending 0013/0014 (A only) |
| 5 | Collection-updating output (asset `version`, `extent.temporal`, `cube:dimensions`) | Finalize upserts only items. The pipeline never writes collections, and ADR 0020 GUC rules cover only items | ADR + finalize branch (A) or pipeline collection writer (B); audit row per ADR 0008 |
| 6 | In-store trim + Icechunk GC vs the "byte deletion only via `asset_gc`" rule | GC deletes Silo objects outside the queue | ADR 0011 amendment; cron job |
| 7 | Runtime/service image with zarr 3, icechunk ≥2.2.2,<3, virtualizarr ≥2.7,<3, obstore, h5py | None of these packages exist in any image or lockfile | New platform alias (contract fixture) or scanned image per ADR 0021 |
| 8 | `cube-server` compose service (custom xpublish app) | No Zarr-capable server exists | New issue; mirror titiler's Silo env |
| 9 | Tile/EDR server auth and egress posture | It inherits titiler's unauthenticated, internet-open default | Rides I-1 (#34) and #68; record in serving.md |
| 10 | UI: Zarr URL builders, time-coordinate frame source, Preview branch on collection asset, EDR chart | Preview is gated on item assets and item datetimes | New app issues (shared map components unchanged) |
| 11 | Silo conditional-write check in the store checklist; STAC Icechunk asset convention | Guards against silent consistency loss on a Silo bump; no ratified media type | #48 comment; ADR for the asset shape |

The spike should settle the open questions before any of this becomes a plan.

| Open question / risk | How to validate in the spike |
|---|---|
| xpublish-tiles renders the virtual GOES fixed grid correctly, and xpublish-edr position queries handle scan-angle radians | Render z3–z8 tiles over CONUS and compare a point series against direct netCDF4 reads |
| Low-zoom tile latency and animation pacing with no overviews (full-width strip reads, float64 decode, numba warm-up) | Use the xpublish-tiles bench CLI. Measure laptop→us-east-1 and in-region separately |
| Manifest growth and `shift_array` cost over thousands of commits (288/day) | Run a 3–7 day soak and record manifest count and size and commit latency |
| Whether `HDFParser` records `checksum_etag`/`last_modified` so silent NODD rewrites fail loudly | Inspect refs and tamper with a local copy |
| MCMIPC internal chunking, and whether all 16 bands share one chunk shape | `ncdump -hs`, then a 3-file append |
| Out-of-order or late files (`append_dim` cannot insert) and gaps | Decide on arrival order versus pre-sized `region=` slots, and document gap tolerance |
| Whether titiler-multidim's `from_env=True` honors a custom endpoint (fallback viability) | Point it at Silo with `AWS_ENDPOINT_URL` |
| NODD CORS for any future browser-direct (icechunk-js) path | Send a preflight range GET to `noaa-goes19` |

See results: [2026-10-03 virtual cube spike](2026-10-03-virtual-cube-spike.md).

## Conclusion

The question has inverted. The expected risk was the data layer: whether a self-hosted MinIO fork could hold a transactional virtual cube, and whether a rolling window would require rewriting data. Icechunk 2's `shift_array` and Silo's correct conditional writes retire both, with local evidence rather than vendor claims. The remaining risk is architectural and lives inside stac-higher. Its process abstraction assumes that runs are stateless, parallel, at-least-once, item-publishing and sandboxed, and a cube writer is the opposite on every axis. The highest-leverage move is to decide early whether cubes are a platform primitive (a cube sink alongside ingest) or a capability granted to user processes. The first choice unblocks a demo without #68. The second forces the platform to grow serialization, persistent grants and collection outputs that other future workloads (catalog-maintaining processes, mosaics) will also want.

Choosing GOES-19 CONUS over HRRR is a positioning choice as much as a technical one. HRRR would prove that stac-higher can reproduce what dynamical.org already serves. A real-time virtual GOES L2 cube with an EDR time-series API appears to have no public equivalent, it runs on codecs every reader already has, and it reuses geostationary know-how the team already paid for. Pairing it with a slide on MRMS ("this one can't be virtual: gzip") makes the constraint itself part of the story.


## Appendix: reproducing the probe

Versions: icechunk 2.2.2, virtualizarr 2.7.3, zarr 3.4.0, obstore, aws-cli v2.
Run a throwaway Silo on a spare port (never the shared stack), using the same
image digest as the `silo` service in `docker-compose.yml`, and create a bucket
named `probe`.

**Conditional writes on Silo** (both second calls must fail with
`PreconditionFailed`, HTTP 412):

```sh
S3="aws --endpoint-url http://localhost:19000 s3api"
echo one > one.txt; echo two > two.txt; echo three > three.txt
$S3 put-object --bucket probe --key cas --body one.txt --if-none-match '*'    # 200
$S3 put-object --bucket probe --key cas --body one.txt --if-none-match '*'    # 412
ETAG=$($S3 head-object --bucket probe --key cas --query ETag --output text)
$S3 put-object --bucket probe --key cas --body two.txt --if-match "$ETAG"     # 200
$S3 put-object --bucket probe --key cas --body three.txt --if-match "$ETAG"   # 412 (stale)
```

**Virtual append on Silo.** `first` and `nxt` are `s3://noaa-goes19/ABI-L2-CMIPC/…M6C13…nc`
keys from consecutive 5-minute scans.

```python
import icechunk as ic
import xarray as xr
from obstore.store import S3Store
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser
from virtualizarr.registry import ObjectStoreRegistry

silo_storage = ic.s3_storage(
    bucket="probe", prefix="goes-c13", endpoint_url="http://localhost:19000",
    region="us-east-1", allow_http=True, force_path_style=True,
    access_key_id="…", secret_access_key="…",
)
reg = ObjectStoreRegistry({"s3://noaa-goes19": S3Store(bucket="noaa-goes19", region="us-east-1", skip_signature=True)})
cfg = ic.RepositoryConfig.default()
cfg.set_virtual_chunk_container(ic.VirtualChunkContainer("s3://noaa-goes19/", ic.s3_store(region="us-east-1", anonymous=True)))
creds = ic.containers_credentials({"s3://noaa-goes19/": ic.s3_credentials(anonymous=True)})
repo = ic.Repository.create(silo_storage, cfg, authorize_virtual_chunk_access=creds)

def vds_for(url):  # GOES ABI files carry a scalar `t`; make it a dimension
    vds = open_virtual_dataset(url, registry=reg, parser=HDFParser(), loadable_variables=["t", "x", "y"])
    return vds[["CMI", "DQF", "t", "x", "y"]].expand_dims("t")

s = repo.writable_session("main"); vds_for(first).vz.to_icechunk(s.store); s.commit("init")
s = repo.writable_session("main"); vds_for(nxt).vz.to_icechunk(s.store, append_dim="t"); s.commit("append")

ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, zarr_format=3)
```

Observed: each append committed in 1.2–1.8 s (laptop to us-east-1), and the
cube read back as `(t, y: 1500, x: 2500)` with correct per-file times. Opening
the repo without `authorize_virtual_chunk_access` failed with
`InvalidInputError` on read.

**Rolling window.** For each array along `t` (`CMI`, `DQF`, `t`):
`session.shift_array(f"/{name}", (-1, 0, 0)[:ndim])`, then resize the zarr array
to `n - 1` along `t`, then commit once. A four-step cube became three steps with
the virtual data still reading correctly. Requires a time chunk of 1.

**Conflict.** Two sessions opened from the same parent, both appending: the
second `commit(..., rebase_with=ic.BasicConflictSolver(), rebase_tries=5)` raised
`RebaseFailedError` (11 conflicts) and committed nothing. A stale-parent commit
without rebase raised `ConflictError`.

**Expiry and GC.** `repo.expire_snapshots(older_than=cutoff)` then
`repo.garbage_collect(cutoff)` returned
`GCSummary(bytes_deleted=39066, chunks_deleted=6, manifests_deleted=18, snapshots_deleted=3, transaction_logs_deleted=1)`
and touched only objects under the repo prefix.
