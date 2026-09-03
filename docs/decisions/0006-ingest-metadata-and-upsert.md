# ADR 0006 — Ingest metadata extraction + pgstac upsert library choices

- **Status:** accepted (Phase 4, Slice B4)
- **Owners:** ingest pipeline (`services/pipeline`)
- **Related:** ROADMAP §4 (pipeline runtime), §6.1 (EXTRACT/ITEMIZE), Phase 4
  Slice B4; ADR 0001 (migration ownership)

## Context

Slice B4 adds the last two stages of Ingest flow A (§6.1): **EXTRACT** turns a
`stored` group of source files into a STAC item dict per the association's
`metadata` strategy (§5.1 — `raster_auto` / `sidecar` / `defaults_only`; G-6
adds a fourth, `extractor`, dispatching to an operator process instead of a
library — see `docs/processes.md` "Extractors"), and
**ITEMIZE** validates that dict and writes it into pgstac. Two library
questions had to be settled before either stage could exist: what does
metadata extraction depend on, and how does the pipeline write into a schema
it does not own (ADR 0001: pgstac's schema is image-owned, the pipeline never
runs DDL)?

## Decision

**1. Metadata extraction libraries, pinned:**
- `rio-stac==0.12.0` + `pystac==1.15.1` + `rasterio>=1.5,<2` for `raster_auto`
  — rio-stac builds the item (geometry, datetime, proj/raster extension
  fields) from an open rasterio dataset.
- `defusedxml>=0.7.1` for `sidecar` XML parsing; the stdlib `json` module for
  `sidecar` JSON (no extra dependency needed there).
- `defaults_only` needs no extraction dependency — pure Python, a
  null-geometry item from collection defaults.

**2. GDAL comes bundled, not installed.** rasterio's `>=1.5,<2` manylinux and
macOS wheels are self-contained: they bundle GDAL 3.12.1 and its own driver
set. This means **no system GDAL package and no `Dockerfile` change** — the
existing `python:3.12-slim-bookworm` runtime image is unchanged. A
`[tool.uv] no-build-package = ["rasterio"]` guardrail in `pyproject.toml`
forbids `uv` from ever falling back to a source build (which would need
system GDAL headers and silently reintroduce the dependency). The image grows
by roughly 180–230 MB for the bundled GDAL + rasterio wheel.

**3. ITEMIZE validates with `stac-pydantic==3.6.0` using the *core*
`stac_pydantic.Item` model, not `stac_pydantic.api.Item`.** The API variant
additionally requires a `root` link, which is an API-response concern — a
freshly EXTRACT-built item is a plain catalog item, not an API page entry, so
requiring a `root` link would reject every valid item this stage produces.
This is an **offline, core-structural gate**: it checks required STAC fields
and types, not extension-specific JSON Schemas. Network-fetching
`stac-validator`/extension schema validation is **not** run in the ingest hot
path — it stays available for backfill/QA tooling, where a slow, network-bound
check is acceptable and a hot-path stall is not.

**4. `pypgstac[psycopg]==0.9.11` performs the upsert**, and the `ghcr.io/stac-utils/pgstac`
image in `docker-compose.yml` is pinned from `:latest` to the matching
**`v0.9.11`** tag. pypgstac's `Loader.load_items(..., insert_mode=Methods.upsert)`
talks to pgstac through its own SQL functions, and that surface can shift
between minor versions — pinning the client to the same minor as the schema
image keeps them in lockstep rather than relying on `:latest` staying
compatible by chance.

**5. The upsert is ADR-0001-compatible: verified to write item data only.**
`Methods.upsert` creates `ON COMMIT DROP` temporary staging tables scoped to
its own transaction and calls pgstac's own `upsert_item` functions — it runs
no `CREATE TABLE`/`ALTER TABLE`/migration statements against permanent
schema objects. The data-plane code that writes STAC items therefore still
respects the invariant that "pgstac DDL is image-owned" (ADR 0001); the
pipeline reads and writes *rows*, never schema.

**6. `defusedxml`, not `xml.etree`, for sidecar XML.** The FISMA-High posture
(ROADMAP §2) requires the sidecar parser to defend against both XML External
Entity (XXE) injection and entity-expansion denial-of-service (billion-laughs,
quadratic blowup) — stdlib `xml.etree.ElementTree` blocks neither by default.
`defusedxml.ElementTree.fromstring` rejects DOCTYPE declarations and external
entity references outright, closing both classes of attack for untrusted
sidecar files arriving from source connections.

## Consequences

- The pipeline image grows by ~180–230 MB (bundled GDAL + rasterio wheel) with
  **no Dockerfile change** — the growth is entirely inside the dependency
  layer `uv sync` installs.
- **rasterio's bundled GDAL ships a driver subset**, not the full driver list
  a system GDAL install would carry. Sufficient for the supported ingest media
  types (COG/GeoTIFF and friends); an exotic raster format outside that subset
  would fail EXTRACT rather than silently degrade — tracked in
  [`../ISSUES.md`](../ISSUES.md).
- **pgstac upgrades are no longer a free `:latest` pull.** Bumping the pgstac
  image tag now requires bumping the `pypgstac` pin in lockstep and
  re-running the upsert path (unit + the DB integration test) before the
  upgrade ships — an explicit two-file change instead of an implicit one.
- **`file_mtime` is an approximation, not a true source modification time.**
  The `ingest_files` ledger has no durable mtime column — SFTP/FTP expose one
  inconsistently and S3 exposes only an ETag — so `metadata.defaults.datetime:
  file_mtime` resolves to the ledger row's `updated_at` (the settle-check
  timestamp), not the file's actual mtime at the source. Good enough for the
  common case (files land and settle quickly) but not exact. A true
  source-mtime column would be a future app-owned migration (ADR 0001 keeps
  DDL ownership with the app). Logged in [`../ISSUES.md`](../ISSUES.md).
- The stac-pydantic-only gate means a structurally valid but semantically
  wrong item (e.g. a custom extension field with the wrong type) can still
  reach the catalog through ingest. Backfill/QA tooling that runs
  `stac-validator` against extension schemas is the intended place to catch
  that class of error, not the hot path.

## Revisit

If pgstac ships a stable extension-point for validated-item upsert that
doesn't require staging-table creation per call, or if the item volume
outgrows `pypgstac`'s single-process throughput, re-evaluate `rustac` (already
tracked as an evaluation item for bulk paths in ROADMAP §4). Re-evaluate the
stac-pydantic-only gate if a collection's data quality requires
extension-schema enforcement in the hot path rather than as a backfill pass.
