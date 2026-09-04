# Built-in extractor library — stactools packages as one-click extractors — design

**Date:** 2026-09-04
**Status:** **approved by the lead 2026-09-04** (the §12 decisions stand
as written unless overturned later). Written from the lead's 2026-09-04
feedback (`FEEDBACK.md` item 5: "can we make use of stactools-packages to
offer a built-in list of metadata extractors?") and two answers given in
the same session (§2). The decisions in §12 were taken by the agent
without a lead answer. The X queue in `TODO.md` is copied from §13; X-1
may start.
**Scope source:** GOES spec §6 / §15 (extractors: `processes.kind`, the
`build_item → validate_item` seam, the §6.1 immutability rules in
`finalize/extract_run.py`), ADR 0013 (one platform-built runtime image;
user images refused), ADR 0018 (staged inputs + manifest), the K spec
(hardware profiles carry an `image` — the coordination point in §8),
`tests/contract-fixtures/README.md` (cross-runtime shapes are fixtures).

## 1. Problem

Every extractor today is hand-written inline Python. The GOES worked
example needed ~150 lines to date a granule from its filename and
footprint it from the CMI_C02 grid — and the community already maintains
`stactools-goes`, which reads the same file and emits a richer item. The
same is true across NOAA's public archives: HRRR, MRMS, NWM, CDR, SST,
GLM all have stactools packages. An operator pointing a connection at a
public bucket should be able to pick "GOES ABI" from a list and get
metadata, not write a parser.

## 2. Lead answers (2026-09-04)

- **Scope: a curated NOAA/public-archive set** from day one — `goes`,
  `goes-glm`, `noaa-hrrr`, `noaa-mrms-qpe`, `noaa-cdr`, `noaa-nwm`,
  `noaa-sst`, `viirs`, `modis`, `hls`, `landsat`, `sentinel1`,
  `sentinel2`, `naip` — rather than GOES-only or the whole organisation.
- **Product shape: pick it in the Data flow form.** The ingest form's
  Extractor picker gains a "Built-in" group; choosing one creates (or
  reuses) a group-owned extractor process from the template, marked
  built-in with read-only code. It IS a process, so runs, alerts, the
  graph and re-runs work unchanged. "New from template" and "both" were
  offered and declined.

## 3. Facts that shape the design (verified 2026-09-04)

1. **There is no uniform stactools entry point.** `stactools-goes`'s
   `create_item(product_hrefs: List[ProductHrefs], read_href_modifier=…)`
   takes a list of typed href objects; other packages take a single href,
   a directory, or a metadata file plus bands. The library therefore needs
   a **per-package adapter**, not one generic call.
2. **The platform's extract contract is strict** (`check_extract_output`):
   the extractor may not change the item id, collection, asset SET or any
   asset href; it must leave non-null geometry and `properties.datetime`.
   stactools items mint their own ids and hrefs and often ADD assets
   (COG conversions, thumbnails). The shim must merge, not replace.
3. **Runs are network-isolated** (`PROCESS_NETWORK_MAX = isolated`).
   Packages only ever see staged local files; anything that fetches
   (checksums from a sidecar URL, remote thumbnails) is off.
4. **Maintenance is uneven.** Pushed within a year: `goes`, `goes-glm`,
   `noaa-hrrr`, `modis`, `sentinel1`, `sentinel2`, `naip`, `noaa-cdr`.
   Two to five years stale: `noaa-mrms-qpe`, `noaa-nwm`, `landsat`,
   `viirs`, `hls`, `noaa-sst` (2021). Stale packages may not import
   against current `pystac`/`stactools`; the build must prove each one.
5. **Source access splits the set.** On anonymous public buckets, live-
   gateable today: `goes`, `goes-glm` (`noaa-goes19`), `noaa-hrrr`
   (`noaa-hrrr-bdp-pds`), `noaa-mrms-qpe` (`noaa-mrms-pds`), `noaa-nwm`
   (`noaa-nwm-pds`), `noaa-cdr` (`noaa-cdr-*`), `noaa-sst`. Needing
   Earthdata login or requester-pays: `viirs`, `modis`, `hls` (LAADS / LP
   DAAC), `landsat` (`usgs-landsat`), `sentinel1`/`sentinel2` (requester-
   pays), `naip` (`naip-analytic`). Those seven ship and are import-
   tested; their live gate waits for a credentialed connection.
6. **Multi-file products.** Sentinel-2 SAFE, Landsat MTL + bands, HLS
   tiles are one item from many files. The ingest `grouping.rule` already
   groups files into one draft item; the adapter receives the group's
   assets. A package that needs a full directory tree the grouping cannot
   deliver is marked `supports: single_file` and refused for grouped
   associations at the form (and by the pipeline).
7. `processes` has `kind` but nothing that marks a process as platform-
   authored; `runtime.image` is forced `null` for `inline_python` and the
   executor takes ONE image from `PROCESS_RUNTIME_IMAGE`.

## 4. Goals / non-goals

Goals:
- A registry of built-in extractors, versioned with the platform, listed
  in the ingest form under "Built-in".
- One click creates a group-owned, deployed, read-only extractor process
  on a runtime image that ships the packages.
- The GOES demo's hand-written extractor is replaceable by the built-in
  `goes` one with equal or better metadata (the live gate).
- Adding a package is one registry entry, one adapter, one image rebuild.

Non-goals:
- Per-process `pip install` at run time (ADR 0013 risk list).
- Operator-supplied packages or images.
- stactools' COG/thumbnail generation — outputs of an extractor are
  metadata only; asset creation is a transform's job.
- Auto-upgrading existing built-in processes when the image moves (a
  deliberate re-deploy, §7).

## 5. The registry — a contract fixture

`tests/contract-fixtures/builtin-extractors.json`, read by BOTH runtimes
(the app for the picker and the process template; the pipeline for the
adapter dispatch and the launch-time check), per the fixtures README:

```json
{
  "id": "stactools-goes",
  "label": "GOES-R ABI (L1b / L2)",
  "package": "stactools-goes",
  "version": "0.1.x",              // the pin the image was built with
  "adapter": "goes",               // module under stac_higher_stactools.adapters
  "supports": "single_file",       // | "grouped"
  "products": ["ABI-L1b-*", "ABI-L2-*"],   // hint text + include-glob suggestion
  "access": "anonymous",           // | "credentialed" — drives the live-gate split
  "runtime": {"memory_mb": 1024, "timeout_seconds": 120, "network": {"level": "isolated"}},
  "extensions": ["proj", "raster", "goes"]  // stac_extensions the item will carry
}
```

The fixture is the single source of truth; `services/process-runtime/
Dockerfile.stactools` pins exactly the `package==version` pairs it lists,
and a CI check fails if the two drift.

## 6. Runtime — a second platform image, one wrapper module

`services/process-runtime/Dockerfile.stactools` extends the existing
runtime image with `stactools` + the fourteen packages, pinned, plus a
platform module `stac_higher_stactools`:

- `run(builtin_id)` — reads the ADR 0018 manifest (`kind: "extract"`),
  and for each `input_items` draft: resolves the draft's assets to their
  staged local paths, calls `adapters.<adapter>.create(paths, draft)` →
  a `pystac.Item`, then **merges** it onto the draft under the §6.1 rules:
  - keep the draft's `id`, `collection`, asset KEYS and every asset
    `href`;
  - copy `properties` (all, including `datetime`), `geometry`, `bbox`,
    `stac_extensions`;
  - for each draft asset, copy asset-level metadata the package produced
    for the matching file (`type`, `roles`, `title`, `raster:bands`,
    `proj:*`) — "assets may gain metadata, not members";
  - DROP every asset the package added (COG derivatives, thumbnails) and
    any href pointing at the staged path; log what was dropped;
  - write the result to the run's output prefix as the extract branch
    expects today.
- `adapters/` — one small module per package that knows how to call it:
  `goes` builds `ProductHrefs(nc_href=path)` lists; `sentinel2` locates
  the SAFE root among the grouped paths; etc. Each adapter is ~20 lines
  and unit-tested against the package's own test fixture file.
- Import smoke test at image build: `python -c "import stactools.<pkg>"`
  for every registry entry, so a stale package (§3.4) fails the build,
  not a run.

The built-in process's revision is `inline_python` with a two-line body:

```python
from stac_higher_stactools import run
run("stactools-goes")
```

plus `runtime.runtime_image: "stactools"` (§8). Upgrades happen by
rebuilding the image and re-deploying (§7), never by editing the body.

## 7. App — registry, template, ownership, UI

- `GET /api/extractors/builtin` (member+) serves the registry (minus
  nothing — it holds no secrets).
- **Create-or-reuse**: `POST /api/processes/builtin` (operator+, audited
  `create`) with `{builtin_id, group_id}` — finds the group's process
  with that `builtin_id` or inserts one: `kind = extractor`, name from the
  registry label, `max_runs_per_hour 600`, and deploys revision 1 from the
  template in the same transaction. Migration **028** (settled 2026-09-04: X is worked first; K-3 takes 029 —
  whichever merges second renumbers) adds `processes.builtin_id text
  NULL` + a unique index on `(group_id, builtin_id) WHERE builtin_id IS
  NOT NULL AND deleted_at IS NULL`.
- A built-in process is **read-only in the editor**: the deploy form is
  replaced by a "Built-in · stactools-goes 0.1.x · image stactools@sha"
  card with one action, **Update to current** (a new revision from the
  template — the only way `current_revision` moves), and the existing
  enable/disable and soft-delete (still 409 while an association names it).
- **Ingest form**: the Extractor picker gains a "Built-in" optgroup
  listing the registry (filtered by `supports` against the form's
  grouping rule); choosing one calls create-or-reuse, then stores the
  returned `process_id` in `metadata.extractor.process_id` exactly as a
  hand-written extractor is stored. Nothing downstream changes.
- The process list shows a `built-in` badge next to the `extractor`
  badge; the graph inherits it through the node label.

## 8. Executor — image aliases (coordination with the K queue)

`runtimeLimits` gains `runtime_image: "default" | "stactools"` (default
`default`; every stored revision lacks it, lenient reader on the Python
side — the K-1 `hardware` pattern). The pipeline resolves the alias
through settings (`PROCESS_RUNTIME_IMAGE`, `PROCESS_RUNTIME_IMAGE_STACTOOLS`)
at launch; an unknown alias is a dead run with a reason (the
`PROCESS_NETWORK_MAX` dual-enforcement pattern: write gate AND launch).
`runtime.image` stays `null` for inline processes — ADR 0013's "user
images refused" is untouched; aliases name PLATFORM images only.

K-1's hardware profiles also carry an `image`. Rule: a profile's `image`
is a BASE; the alias selects the variant (`<base>-stactools`), so a GPU
profile and the stactools variant compose. The K spec gets a one-line
note; whichever lands first adds the other's field.

## 9. Pipeline — launch-time check and the finalize seam

- Launch refuses an extractor run whose `builtin_id` is not in the
  registry (registry drift after a downgrade) — dead run, reason set.
- Finalize's extract branch is UNCHANGED: the wrapper emits exactly what a
  hand-written extractor emits, and `check_extract_output` remains the
  gate. This is the point of merging in the wrapper rather than relaxing
  the contract.

## 10. Testing and gates

- Contract: `builtin-extractors.json` round-trips both readers; the
  Dockerfile pin check.
- Unit (runtime module, run in the pipeline suite with the packages
  installed in a test extra): the merge keeps id/asset keys/hrefs, drops
  added assets, copies properties/geometry/extensions; each adapter
  produces an item from its package's fixture file.
- Unit (app): create-or-reuse is idempotent per group; a built-in
  process refuses a code deploy (409) and accepts Update to current; the
  picker filters by `supports`.
- **Live gate A (lead, Docker + internet):** switch the standing demo's
  `goes-abi-mcmipc` association from the hand-written extractor to
  built-in `stactools-goes`; the next granule's item must carry the same
  scan-time `datetime` (to the second), a footprint overlapping the
  hand-written one by ≥ 95 % (IoU), `platform`, and at least the `goes:*`
  fields the hand-written extractor produced. Then switch back or keep —
  lead's call.
- **Live gate B:** one granule each through `goes-glm`, `noaa-hrrr`,
  `noaa-mrms-qpe`, `noaa-nwm`, `noaa-cdr`, `noaa-sst` from their public
  buckets via anonymous connections + `defaults` grouping, recorded as a
  table (package, file, seconds, fields produced, dropped assets).
- The credentialed seven: import smoke + adapter unit tests only; ISSUES
  entry recording that their live gate is owed.

## 11. Risks

- **Image size**: fourteen packages plus h5py, xarray, netCDF4, etc. is
  a multi-GB image. Acceptable locally; Phase 8 should measure pull time.
  If it bites, split into `stactools-noaa` and `stactools-optical`
  variants — the alias mechanism already allows it.
- **Stale packages** (§3.4) may pin old `pystac` and conflict. The build
  smoke test surfaces it; the fallback is dropping the entry from the
  registry with an ISSUES line, not patching the package in-image.
- **Semantic mismatch**: a package's `datetime` convention (start vs
  midpoint) may differ from what the hand-written extractor chose. Live
  gate A measures it; the registry entry documents the convention.
- **Credentials in the runtime**: none — the packages read staged files,
  and the run's STS scope is unchanged.

## 12. Agent-taken decisions (confirm or overturn)

1. Registry is a contract fixture, not a DB table — it ships with the
   image it describes.
2. One wrapper module in the image; the process body is two lines. The
   alternative (full wrapper as inline code per process) drifts per group
   and cannot be upgraded.
3. Built-in processes are per GROUP (ownership rules unchanged) rather
   than one shared platform process — a shared one would break the
   group-scoped graph, alerts and audit.
4. Added assets are DROPPED, not published — extractors stay metadata-
   only; a COG wants a transform.
5. Image variant selected by a `runtime_image` alias, composed with K-1's
   profile `image` as base + variant.
6. Migration number 028 (K-3 → 029), settled at approval rather than by the renumbering rule the K/W queues
   used.
7. The seven credentialed packages ship without a live gate.

## 13. Slices (copied to `TODO.md` as the X queue)

- **X-1 · Registry fixture + both readers.** `builtin-extractors.json`
  (fourteen entries), Zod + Python loaders, the Dockerfile pin check.
- **X-2 · stactools runtime image + wrapper + adapters.**
  `Dockerfile.stactools`, `stac_higher_stactools` (`run`, merge,
  adapters ×14), import smoke at build, unit tests; `containers.yml`
  builds it. Depends on X-1.
- **X-3 · Image alias.** `runtime_image` on `runtimeLimits` + Python
  reader, executor resolution, launch-time refusal, `process-runtime.json`
  cases. Coordinates with K-1. Depends on X-1.
- **X-4 · Built-in processes in the app.** Migration 028, create-or-
  reuse route, template deploy, read-only card + Update to current,
  `built-in` badge, ingest-form optgroup. Depends on X-1, X-3.
- **X-5 · Live gates (LEAD).** Gate A against the standing GOES demo,
  gate B across the six other anonymous packages; ISSUES entry for the
  credentialed seven. Depends on X-2, X-4.

## 14. X-1 addendum — the set is eleven, not fourteen (2026-09-04)

Verified against PyPI while writing the registry: three of §2's fourteen —
`noaa-nwm`, `noaa-sst` and `hls` — have never been **published**. They exist
only as repos under `stactools-packages`, with no tags or releases at all
(last pushed 2023-10, 2021-09, 2022-08), so there is no `package==version` for
`Dockerfile.stactools` to pin and nothing for the §6 import smoke test to
prove. The lead's call (2026-09-04) is to ship the eleven that have a release
and log the three: **I-107**. Consequences elsewhere in this spec:

- §5's registry holds eleven entries. The concrete pins are `stactools-goes`
  0.1.8, `goes-glm` 0.2.4, `noaa-hrrr` 1.0.1, `noaa-mrms-qpe` 0.3.1,
  `noaa-cdr` 0.2.1, `viirs` 0.1.0, `modis` 0.2.0, `landsat` 0.5.0,
  `sentinel1` 0.8.1, `sentinel2` 0.8.0, `naip` 0.5.0. §5's illustrative
  `"version": "0.1.x"` is not a legal value: both readers refuse a range,
  because the pin check compares the registry and the Dockerfile literally.
- §10's **live gate B** covers the four remaining anonymous packages —
  `goes-glm`, `noaa-hrrr`, `noaa-mrms-qpe`, `noaa-cdr` — beside gate A's
  `goes`.
- §3.5's "credentialed seven" is **six** (`hls` is gone): `viirs`, `modis`,
  `landsat`, `sentinel1`, `sentinel2`, `naip`. I-105 is amended.

Two smaller findings from reading the packages' own sources, recorded for
X-2's adapters: `stactools-noaa-hrrr`'s `create_item` takes
`(region, product, cloud_provider, reference_datetime, forecast_hour)` rather
than an href — its `create_item_from_idx_df` is the file-driven door, so the
entry is `supports: grouped` (the GRIB2 plus its `.idx` sidecar) — and
`stactools-sentinel1` has no top-level `stac.py`: the entry points live under
`grd/`, `rtc/` and `slc/`, so the adapter must name one (GRD, the public
product).

X-1 also leaves **packaging undecided on purpose**: both readers take a
document, because neither the app's build context (`COPY app`,
`COPY packages/shared`) nor the pipeline's (`services/pipeline`) contains
`tests/`, and choosing a COPY, a mount or an env override before X-2 and X-4
need it would bake the wrong answer into the contract.

## 15. X-2 addendum — what the build proved and the calls it forced (2026-09-04)

- **§11's stale-package risk was real, and the resolver found it first.**
  `goes-glm` and `noaa-hrrr` cap `pystac<1.12`, `sentinel1` `~=1.9.0`, all
  below the platform's `pystac==1.15.1`. §11's fallback — drop the entry —
  would have cost two of gate B's four packages, and every one of the eleven
  imports and builds an item on 1.15.1. Decision: the image installs with a
  uv `--override pystac==1.15.1`, mirrored in the pipeline's `[tool.uv]`, and
  the build-time smoke plus the adapter tests are what keep it honest
  (**I-109**). Beside it `setuptools<81`: `pkg_resources` left setuptools in
  81 and eight packages still import it.
- **Packaging (§5's "reaches both runtimes")**: a named build context
  `fixtures` → `tests/contract-fixtures`, `COPY --from=fixtures` in the
  pipeline's Dockerfile and `Dockerfile.stactools`, the path published in
  `STAC_HIGHER_BUILTIN_REGISTRY`, readers falling back to the checkout. The
  runtime images build through `services/process-runtime/docker-bake.hcl`
  (base → variant chained, run from the repo root), locally and in
  `containers.yml` via `docker/bake-action`.
- **The smoke is registry-driven**, not a hand-typed import list:
  `python -m stac_higher_stactools.smoke` imports every entry's derived
  module and its adapter and checks the installed distribution's version
  equals the pin — stronger than the text pin check, which cannot see what
  pip resolved.
- **Merge conventions settled in the wrapper** (§6): a null `datetime` with
  `start_datetime` set (noaa-cdr, modis) becomes `datetime = start_datetime`;
  produced assets are matched to draft assets by the staged file's path or
  basename (noaa-hrrr emits the archive URL, mrms the decompressed name —
  its adapter puts the `.gz` back); properties are OVERLAID on the draft's
  rather than replacing them; nothing invents a geometry.
- **Flat staging vs product trees**: SAFE products and NAIP's state need the
  source path, which only reference-mode hrefs carry (**I-110**). The
  adapters rebuild the tree from hrefs and fail clearly otherwise.
- **Fixtures**: nine adapters run against the package's own test file,
  vendored at the pinned tag (1.4 MB, `services/pipeline/tests/data/
  stactools/README.md` records provenance); `viirs` and `sentinel1` use
  doubles (I-105 amended). `noaa-hrrr` keeps no files in its repo, so its
  `.idx` fixture is hand-written in the archive's format.
- **Measured**: the variant is 1.18 GB against the 525 MB base — §11's
  "multi-GB" did not materialise; no split into `stactools-noaa` /
  `stactools-optical` is needed yet.
