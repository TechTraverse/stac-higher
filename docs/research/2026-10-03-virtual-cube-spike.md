# The virtual GOES cube works on Silo, with three cube-server fixes

- **Kind:** research (2026-10-03), spike results, not a spec. Tracking: #84
  (Z-1, epic #83). Feeds the Z design spec; tests the open risks of
  [ADR 0022](../decisions/0022-virtual-cube-sink.md).
- **Question:** do the risks ADR 0022 left open hold up when measured: tiles and
  EDR on the geostationary grid, tile latency without overviews, a long-running
  rolling append, ref integrity, MCMIPC, writer mechanics, and late files?
- **Evidence:** a throwaway Silo (`z1-silo`, the `pgsty/silo` digest pinned in
  `docker-compose.yml`, on :19000), a throwaway Postgres (`z1-pg`), live
  anonymous reads of `noaa-goes19`, and the scripts in
  [`2026-10-03-virtual-cube-spike/`](2026-10-03-virtual-cube-spike/) (recipe in
  the appendix). Measured from a laptop (macOS, residential link to us-east-1).
  The shared stack and the standing demo were not touched.
- **Versions:** icechunk 2.2.2, virtualizarr 2.7.3, zarr 3.4.0, xarray 2026.9.0,
  xpublish 0.5.2, xpublish-tiles 0.9.2, xpublish-edr 0.11.1, numba 0.68.0,
  procrastinate 3.9.0, titiler-multidim 0.9.1 (git; not on PyPI). All pins were
  still the latest PyPI releases on 2026-10-03.

**Nothing measured contradicts an ADR 0022 decision. One *Consequences* bullet
needs sharpening (Q5), and the cube server needs three changes the ADR did not
anticipate (Q2, Q3, Q5).** A virtual CONUS band-13 cube on Silo renders in the
right place to within one source pixel. Its values match a direct NetCDF read to
float32 precision, fill mask included. Appends take about 1.5 s, almost all of
it the remote HDF5 header parse; the commit itself takes about 0.04 s. The
storage and writer mechanics behave exactly as the ADR assumes. The Procrastinate
`lock` serializes across worker processes, and `queueing_lock` coalesces a
burst. The pipeline's own S3 connection config maps cleanly onto obstore and
Icechunk, and `resolve_pinned` vets the endpoint host. The fixes all sit in the
cube server:

1. **xpublish-edr ignores the geostationary grid.** On the stored scan-angle
   coordinates every `position`, `area` and `cube` query returns **HTTP 200 with
   no data**: silently wrong, not an error. Rescaling `x`/`y` to metres
   (× `perspective_point_height`) when the server opens the repo fixes EDR
   exactly and leaves tiles identical.
2. **Nothing is cached.** Repeated tile requests cost as much as the first:
   0.45–0.75 s at z0–z4 and 0.15–0.22 s at z5–z8, about 0.24 s of which is a
   single round trip from the laptop to NODD. Rendering itself costs 0.1 s at
   low zoom and under 30 ms from z5 up. Animation is viable without overviews:
   a CONUS z4 viewport loads 14 frames in 8.5 s. It needs `Cache-Control:
   immutable` on time-pinned tiles and a bounded frame count. No overview
   level, and so no new ADR, is required.
3. **One missing source breaks every time series that spans it.** A deleted or
   changed source object fails loudly (VirtualiZarr always records a
   modification-time checksum), but the error fails the whole EDR query, not
   just one step. The cube server must read per step and return gaps.

**The 24 h soak (Q4) ran clean but found one unbounded leak**: 289 commits, no
errors, flat append and commit latency, flat working data. But Icechunk never
deletes transaction logs or the `overwritten/` backups of its `repo` object,
so the repository grows on every commit (0.25 → 7.7 MB after GC over 24 h).
Lowering `num_updates_per_repo_info_file` to 100 cuts that to a projected
~8 MB per cube per day. The lead accepted the rest for v1 (I-143). **Q8:**
across a day of `noaa-goes19` CMIPC, no file arrived late and no 5-minute step
was missing, so skip-late stands.

## Risk summary

| # | Risk | Answer | Verdict |
|---|---|---|---|
| 1 | Tiles on the fixed grid | Correct placement z3–z8; values = direct read (≤1.5e-5 K, float32 vs float64); fill and scaling survive | **go** |
| 2 | EDR on scan angles | Silently empty on radians; exact on a metres view | **changes-needed** (cube server rescales `x`/`y`) |
| 3 | Tile latency without overviews | 0.45–0.75 s low zoom, 0.15–0.22 s high zoom, laptop; no caching; 14-frame z4 loop in 8.5 s | **go with changes** (`Cache-Control`, warm-up, bounded frames) |
| 4 | Soak: unbounded growth? | 24 h, 289 commits, 0 errors; latency, manifests, snapshots, RSS flat; transaction logs + `overwritten/` grow one per commit (7.7 MB after 24 h) | **go**, with I-143 accepted (history limit 100, size readout) |
| 5 | Ref integrity | Always checksummed (write time); replace/modify/delete fail loudly, never wrong data; but one bad step fails a whole series | **go**; **changes-needed** (pass `last_updated_at`; per-step reads) |
| 6 | MCMIPC | All 16 bands one layout (52×2500, shuffle+zlib); 3-file append works; lazy RGB 2.0 s per 256² | **go** (stretch; RGB needs a custom renderer) |
| 7 | Writer mechanics | `lock` serializes across processes; `queueing_lock` 1 of 5; connection config → obstore + Icechunk works; `resolve_pinned` vets the host | **go**; **changes-needed** (pin the endpoint so checked host = dialed host) |
| 8 | Late and missing files | 0 late, 0 missing steps in 24 h; publish lag p50 30 s, max 48 s | **go** (skip-late stands; no region writes) |
| 9 | titiler-multidim on Silo; NODD CORS | Endpoint env honoured, but no path-style → repo not found; CORS allows `*` + `Range` | fallback **no-go** without a fork; CORS **go** |

## 1. Tiles land in the right place with the right values

The static test repo held 14 consecutive CMIPC C13 steps (2026-10-03
17:01–18:06Z), appended virtually exactly as the soak does. Two checks compared it
against a direct `h5netcdf` read of the same NODD file. That file was copied
once to local disk as test tooling; it is not the product path.

**Whole frame.** For the 17:21Z step, the virtual cube's decoded `CMI` equals the
direct read to 1.5e-5 K, which is the difference between the cube's float64
decode and the direct read's float32. The NaN (fill) mask is identical: 47,163
pixels. `DQF` is identical. The encoding survives virtualization unchanged
(int16, `scale_factor` 0.06145, `add_offset` 89.62, `_FillValue` −1,
`_Unsigned` true; raw 2711 → 256.22 K both ways). Coordinates `x`/`y` differ
by ≤7.5e-9 rad, about 1e-4 of a pixel.

**Tiles.** Each tile was rendered with `raster/gray` over a fixed 190–310 K range
and inverted back to kelvin. Every tile pixel was then compared with the direct
read sampled at that pixel's centre, through pyproj's geostationary transform.
A search over ±3 source-pixel shifts tells a misplaced grid apart from resampling
noise. Results are for four points (Kansas, DC, Seattle, Miami) at z3–z8.

| Zoom | MAE vs direct (K) | Best shift (source px) |
|---|---|---|
| z6–z8 | 0.19–1.40 (Miami z6: 2.1) | (0, 0) in 11 of 12 tiles; Miami z6 (0, −1) |
| z3–z5 | 1.1–2.5 | (0, 0) or (±1, ∓1), i.e. 2 km against a 5–20 km tile pixel |

The residual is 8-bit quantization (±0.24 K) plus nearest-neighbour differences
at cloud edges, where the 99th percentile reaches 6–15 K. At z3–z5 one tile pixel
covers 3–10 source pixels, so a one-pixel best shift is within resampling. Only a
handful of edge pixels at z3–z4 disagree on coverage (≤201 of 65,536). A z5 mosaic over
OpenStreetMap (`2026-10-03-virtual-cube-spike/mosaic-z5.jpg`) shows the curved
geostationary sector edges and cloud features registered on the coastlines.
xpublish-tiles picks its `Geostationary` grid only when `x.attrs.units` is
`rad`, and it handles the scan-angle scaling itself.

**Two serving notes.** Time selection must be exact to the nanosecond or prefixed:
`?t=2026-10-03T17:22:36` returns 422, while `?t=nearest::2026-10-03T17:22:36`
works. The UI frame builder should pass the exact `t` values it read from the
cube. A request without `t` renders the last step.

**Verdict: go.**

## 2. EDR needs metres, not scan angles

xpublish-edr has no geostationary handling. It projects the query point into the
dataset CRS (geostationary metres) and then selects on `x`/`y`, which hold
radians. On the stored dataset, every query answers **200 with an empty series**,
reporting the point as the sub-satellite point (−75°, 0°):

```
t,CMI,y_image,x_image,longitude,latitude
2026-10-03 17:02:36.714359936,,0.08624,-0.03136,-75.00000091028086,1.1595102382679878e-06
```

`area` and `cube` likewise return 200 with zero rows. A second view of the same
repo, with `x`/`y` multiplied by `perspective_point_height` and `units` set to
`m`, fixes all three:

| Query (metres view) | Result | Laptop latency |
|---|---|---|
| `position` Kansas / DC / Seattle, 14 steps | equals the cube read exactly (max abs 0.0); equals direct read to float32; nearest pixel at (−97.011, 38.511) for (−97, 38.5) | 0.29–0.75 s |
| `area` 2° × 1° polygon, one step | 2,772 points | 0.16 s |
| `cube` 2° × 1° bbox, 14 steps | 51,086 rows CSV; 166 KB NetCDF | 0.48–0.61 s |

Tiles on the metres view are identical to the scan-angle view (same per-zoom
MAE and shifts). xpublish-tiles falls back to its generic projected-grid path
and still lands correctly. **One dataset with metres coordinates therefore
serves both plugins.**

**Verdict: changes-needed (cube server only).** Rescale `x`/`y` when opening.
The repository keeps the source's radians, so the cube stays a faithful virtual
copy of the file.

## 3. Tile latency: viable without overviews, but nothing caches

The figures below measure the tile over Kansas, all 14 frames, with each frame
requested twice. The first request after a server start (Numba JIT plus manifest
load) took **2.4–2.6 s**.

| Zoom | Virtual, laptop (median s) | Second request, same frame | In-memory floor (median s) |
|---|---|---|---|
| z0–z2 | 0.61–0.76 | same | 0.11–0.18 |
| z3 | 0.55 | same | 0.12 |
| z4 | 0.44 | same | 0.10 |
| z5 | 0.21 | same | 0.03 |
| z6–z8 | 0.16–0.22 | same | 0.007–0.013 |

The "in-memory floor" is the same server with the dataset `.load()`ed: render
cost only, and not the product path. The gap is NODD. From this laptop a single
ranged GET has a 0.24 s time-to-first-byte, and a tile needs at least one round
of concurrent strip fetches. **A second request costs as much as the first**, and
enabling Icechunk's 1 GiB chunk cache (the xpublish-tiles CLI's configuration)
changed nothing. Virtual chunk bytes are not cached on the read path.
In-region, where time-to-first-byte is in the tens of ms, tiles should approach the floor.

**Animation.** With every CONUS tile at 6 concurrent requests (browser-like)
across 14 frames:

| Viewport | Tiles per frame | 14 frames, wall | Per frame |
|---|---|---|---|
| z3 | 4 | 6.5 s | 0.46 s |
| z4 | 8 | 8.5 s | 0.61 s |
| z5 | 28 | 18.2 s | 1.30 s |

Extrapolated, a full 72-step (6 h) window at z4 takes about 44 s to preload and
24 frames about 15 s. That is acceptable behind a progress bar, especially
because a frame for a fixed `t` never changes. But xpublish-tiles sends **no
`Cache-Control`**, so the browser refetches on every loop. Overviews,
a narrower sector or a coarser band are not needed for CONUS. Full disk would be
about 8× the pixels and was not measured.

**Verdict: go, with changes.** In the cube server: `Cache-Control: public,
max-age=31536000, immutable` on `t`-pinned tile responses and a short max-age on
unpinned ("latest") ones; a warm-up tile at startup to absorb the JIT. In the
UI: preload frames with progress and default the loop to the last 24 steps.
Strict-virtual holds; no ADR change.

## 4. Soak

`soak.py` started at **2026-10-03T18:13Z** on `s3://probe/soak-c13/` (z1-silo):
poll the NODD hour listing every 60 s, append each new `M6C13` file in scan
order (one commit each), `shift_array` + resize `CMI`, `DQF` and `t` to **72
steps** in the same commit, and run `expire_snapshots(now − 1 h)` +
`garbage_collect` hourly. The window fills at hour 6; after that, every commit
also trims.

It ran **24.0 h** (to 2026-10-04T18:15Z) on a laptop kept awake, plugged in
and online: **289 commits, 0 errors, 0 gaps**, and all 23 hourly read checks
(first and last step of the window decode to finite values) passed in ≤1.07 s.
The full summary is in `2026-10-03-virtual-cube-spike/soak-summary.json`.

| Hours | Commits | Append total p50 / p95 | Header parse p50 | Commit p50 / p95 | Trim p50 | Window |
|---|---|---|---|---|---|---|
| 0–6 | 72 | 1.59 / 2.98 s | 1.51 s | 0.043 / 0.056 s | — | filling |
| 6–12 | 72 | 1.65 / 2.88 s | 1.52 s | 0.040 / 0.049 s | 0.053 s | 72 |
| 12–18 | 72 | 1.53 / 2.03 s | 1.39 s | 0.040 / 0.046 s | 0.055 s | 72 |
| 18–24 | 72 | 1.58 / 2.37 s | 1.43 s | 0.040 / 0.045 s | 0.055 s | 72 |

Commit and trim cost did not grow. GC's expiry took ≤0.03 s and its collection
≤0.15 s. Process RSS moved between 102 and 162 MB with no trend.

**What GC keeps flat and what it does not.** After every GC the working data
was constant: 96 manifests, 13 snapshots, 24 inline chunks. Each GC deleted
about 12 snapshots, 96 manifests and 24 chunks. Two object kinds were
**never** deleted (`transaction_logs_deleted: 0` in all 23 runs), so the
repository grew on every commit:

| After GC at | `transactions/` | `overwritten/` | Repo bytes |
|---|---|---|---|
| hour 1 | 14 | 15 | 0.25 MB |
| hour 12 | 148 | 171 | 3.1 MB |
| hour 23 | 282 | 327 | 7.7 MB |

- **Transaction logs** are about 600 B before the window fills and about 17 KB
  after: each `shift_array` records every chunk reference it moved, the whole
  window, on every commit. The Icechunk spec says they are not needed to read
  data; they serve rebase conflict detection and diffs. Icechunk 2.2.2 has no
  public call that removes them.
- **`overwritten/`** holds a copy of the `repo` object, the one mutable file
  (branches, snapshot list and an operations log), taken before each update.
  The spec keeps these by design, for recovery and as the operations-log chain.
  The `repo` object grew from 751 B to 17 KB, about 67 B per commit, as its
  operations log filled. It caps at `num_updates_per_repo_info_file` (default
  1,000) entries, so each copy would reach about 75 KB.

**The history-limit test.** Two 150-commit backfills of real NODD files, with
a 72-step window, one with `num_updates_per_repo_info_file = 100` and one at
the default: same commit time (0.037 s), no errors, reads fine. In the capped
run the growth of each `repo` copy roughly halved after update 100, and the
live `repo` object was 6.7 KB after GC against 8.7 KB at the default. Projected
steady state at a 5-minute cadence with hourly GC:

| Setting | `repo` object | Backups per day | Transaction logs per day | Per cube per year |
|---|---|---|---|---|
| default (1,000) | ~75 KB | ~22 MB | ~5 MB | ~10 GB |
| 100 | ~10 KB | ~3 MB | ~5 MB | ~3 GB |

That is small next to copying the data (~1.1 GB a day for CMIPC C13).

**Verdict: go, with an accepted limitation.** The sink sets the history limit
to 100, and `cube_maintain` records per-kind object counts and bytes and warns
above a threshold. The transaction-log question (is it intended, will upstream
clean them up?) is recorded as **I-143** in `docs/ISSUES.md`, with its revisit
triggers. The lead decided on 2026-10-04 not to ask upstream yet.

## 5. Ref integrity: changes fail loudly, but per series

`HDFParser` itself records no ETag. **VirtualiZarr 2.7.3 always writes a
checksum**, though: `to_icechunk(last_updated_at=…)`, which defaults to *write
time + 1 s*. Icechunk refuses any chunk whose source object's `LastModified` is
newer. The test used copies of a NODD file in a throwaway Silo bucket, never
NODD, with two repos over the same object: default, and
`last_updated_at = <object LastModified>`:

| Tamper | Default checksum | `last_updated_at` = LastModified |
|---|---|---|
| Baseline | reads | reads |
| Re-upload identical bytes | `StorageError: the checksum of the object owning the virtual chunk has changed` | same |
| Replace with the next scan's file | same error | same |
| Flip 585 bytes mid-file (same size) | same error | same |
| Delete | `StorageError: … NoSuchKey` | same |

No case returned wrong data. Identical re-uploads are a false positive by design,
since the checksum is a timestamp, not a content hash. The default leaves a
window: a source rewritten between header parse and commit would pass.
Recording the listing's `LastModified` closes it.

A second test (`q5b_series.py`) put three steps in a cube and deleted one source.
The other frames still read, but **a point time series across the window fails
entirely** with `NoSuchKey`. ADR 0022's consequence that "a missing source object
makes that step unreadable, not the cube" holds for tiles but not for EDR.
Through xpublish-edr, one bad step breaks every series and cube query for up to a
full window (6–24 h).

**Verdict: go, with changes.** The sink passes `last_updated_at` = the source
object's `LastModified` (from the item or a HEAD). The cube server reads
time-series queries per step, or catches `StorageError` per step, and returns
the gap as null. `cube_maintain` may also HEAD the window's sources and log
missing ones. The ADR's "unverified" checksum question is now answered.

## 6. MCMIPC: one layout for every band

`ABI-L2-MCMIPC` (124 variables per file) stores all 16 `CMI_Cnn` as int16
1500 × 2500 in **52 × 2500 chunks with shuffle + zlib**, the same as CMIPC C13.
All 16 share one layout. The `DQF_Cnn` bands are int8 in 104 × 2500 chunks. A
3-file virtual append of all 32 band variables worked, at 1.8–2.0 s per commit
including the header parse. A lazy RGB composite (C02, C01 and a synthetic green
from 0.45·C02 + 0.1·C03 + 0.45·C01, gamma 2.2) over a 256 × 256 window read and
computed in 2.0 s from the laptop.

**Verdict: go (stretch).** The data layer is ready. xpublish-tiles renders one
variable per request, so an RGB tile needs a small custom endpoint in the cube
server. That belongs in a later slice, not v1.

## 7. Writer mechanics hold

**Lock.** On `z1-pg` with the Procrastinate 3.9.0 schema (the version locked in
`services/pipeline/uv.lock`), 4 jobs each on sinks A and B
(`lock="cube:<sink>"`, 2 s each) ran on two worker processes at concurrency 4.
The results:

- **0 overlaps per sink.** Same-sink jobs alternated between the two processes.
- A and B ran in parallel (4 overlapping pairs).
- A burst of 5 defers with `queueing_lock="cube:C"` accepted 1 and rejected 4
  with `AlreadyEnqueued`.

Consecutive same-lock jobs started about 3.0 s apart: a lock release does not
wake a waiting worker; the next fetch poll does. With ADR 0022's coalescing
(one job drains all pending rows), that is noise against a 5-minute cadence. A
worker started with `wait=False` exits while locked jobs remain, which is
irrelevant to the pipeline's long-running workers.

**Connection config → libraries.** The pipeline's own `parse_s3_config` and
`_endpoint_host` were loaded read-only from `connections/adapters/s3.py`. The
fixture's shapes map onto obstore (`S3Store`) and Icechunk
(`VirtualChunkContainer` store + `containers_credentials`) and append and read
correctly:

| Config | `_endpoint_host` | `resolve_pinned` | Append + read |
|---|---|---|---|
| `{bucket: noaa-goes19, region: us-east-1, anonymous: true}` (fixture) | `s3.us-east-1.amazonaws.com` | 8 public IPs | ok, 1.9 s |
| same + `endpoint: https://s3.us-east-1.amazonaws.com`, `force_path_style: true` | same | same | ok, 1.7 s |
| `{bucket, endpoint: http://localhost:19000, force_path_style: true, anonymous: false}` + keys (fixture shape) | `localhost` | `EgressBlocked` (ok when allowlisted) | ok |

`resolve_pinned` also blocks `169.254.169.254` and unresolvable hosts (`minio`
outside compose). **One mismatch:** with no endpoint configured, obstore dials the
virtual-hosted `noaa-goes19.s3.us-east-1.amazonaws.com`, not the
`s3.us-east-1.amazonaws.com` that `_endpoint_host` checks. Both are public
today, so nothing is exposed, but the pre-check vets a different name from the
one dialed.

**Verdict: go, with one change.** The sink always passes an explicit endpoint to
both libraries: the connection's, or `https://s3.{region}.amazonaws.com` with
path style. The host it checks is then the host it dials. The compose-internal
Silo endpoint needs `EGRESS_ALLOW_HOSTS`, as the S3 adapter already does.

## 8. Late and missing files: none in a day

The soak recorded every key as it appeared: `late` (behind the tip, skipped,
seen either at poll time or in an hourly re-listing of the last 6 h), `gap`
(missing 5-minute steps) and `duplicate`. In 24 h:

- **0 late files.** The log holds 48 `late` events, but all of them are files
  scanned *before* the soak started that the hourly re-listing picked up. That's
  an artifact of the script, filtered out here; no file scanned after the start
  arrived behind the tip.
- **0 missing steps**: 289 consecutive 5-minute scans.
- **1 duplicate**, the seed file re-listed at startup.
- **NODD publish lag** (scan end → object `LastModified`): p50 30 s, p95 39 s,
  max 48 s. **Detection** including the 60 s poll: p50 76 s, max 80 s.

**Verdict: go.** Skip-late stands for v1, and region-write slots are not
needed. NODD outages do happen (the April 2026 GOES-19 L2 gap in the first
research doc), and the sink tolerates them as gaps in `t`.

## 9. Optional: titiler-multidim and CORS

**titiler-multidim** (0.9.1, installed from git) opens Icechunk repositories with
`icechunk.s3_storage(bucket, prefix, from_env=True)`. `AWS_ENDPOINT_URL` **is**
honoured: a dead port gives a connection error. But requests go virtual-hosted
(`probe.localhost:19000`), and no environment variable forces path style, so
Silo answers `RepositoryNotFoundError`. The fallback needs a forked opener, or
Silo configured for virtual-host buckets (`MINIO_DOMAIN`). Combined with the
ADR's other objections (a client-supplied URL, `main` only), it stays a distant
fallback.

**NODD CORS:** `OPTIONS` with `Origin` and `Access-Control-Request-Headers:
range` returns 200, `Access-Control-Allow-Origin: *`, `Allow-Methods: GET`,
`Allow-Headers: range`, `Max-Age: 3000`. A ranged GET returns 206 with the
same headers, on both virtual-hosted and path-style URLs. No
`Access-Control-Expose-Headers` is sent, so browser code cannot read
`Content-Range` or `ETag`. A future browser-direct reader is possible.

## What this changes for the spec

These are the inputs for `docs/superpowers/specs/…-virtual-cube-sink-design.md`.
None of them amends ADR 0022's decisions.

- **Cube server:** open with `x`/`y` in metres (Q2); `Cache-Control` by `t`
  pinning; a startup warm-up tile (Q3); per-step tolerant time-series reads
  (Q5); exact or `nearest::` time selectors (Q1).
- **`cube_append`:** pass `last_updated_at` = source `LastModified` (Q5); pass
  an explicit endpoint to obstore and Icechunk and `resolve_pinned` that host
  (Q7); expect about 1.5 s per item from a laptop, dominated by the header parse,
  so coalesced batches parse concurrently and commit once.
- **UI:** frames from the exact `t` values; preload with progress; default the
  loop to the last 24 steps (Q3).
- **MCMIPC RGB:** a later custom render endpoint (Q6).
- **`cube_maintain`:** create and open repos with
  `num_updates_per_repo_info_file = 100`; record per-kind object counts and
  bytes and warn above a threshold (Q4, I-143).
- **Late files:** skip-late stands; no region writes (Q8).

## Appendix: reproducing the spike

Scripts are in [`2026-10-03-virtual-cube-spike/`](2026-10-03-virtual-cube-spike/).
Each has a PEP 723 header; run it with `uv run <file>` (Python 3.12). They
assume:

```sh
docker run -d --name z1-silo -p 19000:9000 -p 19001:9001 -v z1-silo-data:/data \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb9f36d01bee221d34d1c7d7960f6a95c48b0b6c01d99cd13bdae51a46 \
  server /data --console-address :9001
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 \
  aws --endpoint-url http://localhost:19000 s3 mb s3://probe
docker run -d --name z1-pg -p 15432:5432 -e POSTGRES_PASSWORD=z1 postgres:16
```

| Step | Command | Answers |
|---|---|---|
| Soak (detached) | `nohup caffeinate -is uv run -q soak.py --prefix soak-c13 --max-steps 72 >> soak.log 2>&1 &` | Q4, Q8 |
| Smoke | `uv run soak.py --prefix smoke-1 --max-steps 3 --backfill 6 --once --retention 0` | trim + GC |
| Static repo | `uv run soak.py --prefix static-c13 --max-steps 24 --backfill 24 --once --retention 86400 --log static-c13.jsonl` | Q1–Q3 input |
| Server | `uv run cube_server.py [--port 9100] [--cache] [--load]` | Q1–Q3 |
| Tiles vs direct | `uv run q1_check.py` (`DS=goes-c13-m` for the metres view); `uv run q1_array_check.py` | Q1 |
| EDR | `uv run q2_check.py` | Q2 |
| Latency | `TIMES=$(cat times.txt) uv run q3_bench.py` (`PORT`, `VP_Z`, `OUT`) | Q3 |
| Integrity | `uv run q5_integrity.py`; `uv run q5b_series.py` | Q5 |
| MCMIPC | `uv run q6_mcmipc.py` | Q6 |
| Writer | `uv run q7_conn.py`; `uv run q7_lock.py schema \| defer \| worker w1 & worker w2 \| report` | Q7 |
| Soak summary | `uv run analyse.py soak-c13.jsonl` | Q4, Q8 |
| History limit | `uv run soak.py --prefix cap100 --updates-per-file 100 --max-steps 72 --backfill 150 --backfill-hours 14 --once --retention 0` (and without `--updates-per-file` as the control) | Q4 mitigation |

`times.txt` holds the static repo's exact `t` values, from
`curl '…/datasets/goes-c13-m/edr/position?coords=POINT(-97%2038.5)&parameter-name=CMI&f=csv' | tail -n +2 | cut -d, -f1 | sed 's/ /T/' | paste -sd, -`.

The core of the append, as `cube_append` would do it:

```python
vds = open_virtual_dataset(url, registry=reg, parser=HDFParser(),
                           loadable_variables=["t", "x", "y", "goes_imager_projection"])
out = vds[["CMI", "DQF", "t", "x", "y"]].expand_dims("t")      # time chunk of 1
out["goes_imager_projection"] = vds["goes_imager_projection"]  # scalar grid_mapping, not along t
s = repo.writable_session("main")
out.vz.to_icechunk(s.store, append_dim="t", last_updated_at=source_last_modified)
g = zarr.open_group(s.store, mode="r+")
k = g["t"].shape[0] - max_steps
if k > 0:
    for name in ("CMI", "DQF", "t"):
        s.shift_array(f"/{name}", (-k,) + (0,) * (g[name].ndim - 1))
    for name in ("CMI", "DQF", "t"):
        g[name].resize((g[name].shape[0] - k,) + g[name].shape[1:])
s.commit(f"append {key}")
```

And the cube server's open, with the EDR fix:

```python
repo = ic.Repository.open(storage, authorize_virtual_chunk_access=ic.containers_credentials(
    {"s3://noaa-goes19/": ic.s3_credentials(anonymous=True)}))
session = repo.readonly_session("main")
ds = xr.open_zarr(session.store, zarr_format=3, consolidated=False, chunks=None, decode_coords="all")
ds = ds[["CMI", "DQF", "goes_imager_projection"]]
h = float(ds.goes_imager_projection.attrs["perspective_point_height"])
ds = ds.assign_coords(x=(ds.x * h).assign_attrs(ds.x.attrs, units="m"),
                      y=(ds.y * h).assign_attrs(ds.y.attrs, units="m"))
ds.attrs["_xpublish_id"] = session.snapshot_id
app = xpublish.Rest({"goes-c13": ds}, plugins={"tiles": TilesPlugin(), "edr": CfEdrPlugin()}).app
```
