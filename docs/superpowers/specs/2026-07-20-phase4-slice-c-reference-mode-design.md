# Phase 4 Slice C — `storage_mode: reference` (design)

Date: 2026-07-20
Status: approved for planning
Related: ROADMAP §5.1/§5.3 + Phase 4 Slice C bullet; ADR 0001 (migration
ownership), ADR 0005 (asset service / `resolveAssetTarget` seam); ISSUES I-4.

## 1. Goal

Complete the ingest pipeline: reference associations catalog items whose asset
bytes **stay at the source** — no copy into canonical storage. Today reference
associations stall at ledger status `settled` (GROUP forms no groups, FETCH
skips the copy). After this slice they flow past `settled` → EXTRACT → ITEMIZE
and become queryable STAC items, and the app's asset route redirects downloads
to the source instead of to canonical storage.

## 2. Key decision — reference mode targets *durably-reachable* sources only

Reference mode persists a **stable, public object URL** and 302s to it. It does
**not** presign and does **not** decrypt anything.

Rationale: the app carries a deliberate invariant — *no app request path may
decrypt connection credentials* (`app/src/lib/connections/crypto.ts:13-18`);
decryption is pipeline-only. Presigning a **private** source at asset-request
time would require the app to decrypt source credentials, reversing that
boundary. Rather than cross it, we scope reference mode to sources that are
directly reachable by a stable URL (a public / path-style-readable object
store). If a source is private, the operator uses **copy mode**. A reference
association pointed at a private bucket will 302 to a URL that 403s *at the
source* — a visible, debuggable failure, never a silent one.

Consequences:
- No presigned-URL TTL/staleness problem (the stored href is permanent).
- The `crypto.ts` "never decrypts" invariant is fully preserved.
- Private-source reference (via a pipeline resolver endpoint that mints fresh
  presigned URLs per read) is a **named follow-up**, not part of this slice.

The existing **s3-only** restriction on reference associations
(`app/src/lib/associations/schemas.ts`, enforced in-route) stays — consistent
with the ROADMAP's "object-store sources only".

## 3. Data model — `ingest_files.source_href` (migration 006)

New app-owned migration (ADR 0001: the app owns `ingest_files` DDL; the pipeline
reads/writes rows, never DDL):

```sql
-- 006_ingest_files_source_href
ALTER TABLE stac_higher.ingest_files
  ADD COLUMN IF NOT EXISTS source_href text;
```

Semantics: `source_href` is **per-file** (one `ingest_files` row = one source
file = one item asset). Non-null ⇒ this asset is *referenced*; the value is the
stable source URL the asset route redirects to. Null ⇒ canonical (copy mode or
manual upload) — unchanged behavior. The presence of a non-null `source_href`
is the mode signal the app keys on; no separate mode column is needed.

`_LEDGER_COLUMNS` / `_LEDGER_MUTABLE` in the pipeline repo
(`services/pipeline/src/pipeline/ingest/repo.py`) gain `source_href` so FETCH
can write it and stages can read it.

## 4. Pipeline changes (Python)

### 4.1 Stable source URL construction (S3 adapter)

Add `public_object_url(path) -> str` to `S3Adapter` (it already holds
`_bucket`/`_region`/`_endpoint`/`_force_path_style`). Reference mode is s3-only,
so only the S3 adapter needs it. Construction:
- custom endpoint or `force_path_style` ⇒ `{endpoint}/{bucket}/{key}`
  (path-style — MinIO and the demo path);
- default AWS, virtual-hosted ⇒ `https://{bucket}.s3.{region}.amazonaws.com/{key}`.

The `path` passed in is the same `source_fetch_path(config.source_path,
source_path)` FETCH already computes for `adapter.get`. No credentials are read;
this is pure config + key string-building. (Base `StorageAdapter` may declare it
`NotImplementedError` so the seam is typed; only S3 implements it.)

### 4.2 FETCH — reference branch (`ingest/fetch.py`)

Replace the current "log + return 0" reference short-circuit with a real
reference path. For each still-`settled` member:
1. compute `href = adapter.public_object_url(fetch_path)`;
2. `set_ledger_fields(row.id, status=STORED, item_id=item_id, source_href=href)`;
3. count it.

No byte copy, no checksum (DISCOVER's size/mtime fingerprint already drives
re-ingest versioning; a referenced object's bytes are not ours to hash). Return
the stored count so `jobs/ingest.py` enqueues ITEMIZE exactly as in copy mode.
The idempotent guard is unchanged: only a `settled` row is acted on, so a
re-enqueued group can't double-advance. A per-member failure marks only that
member `failed`.

### 4.3 EXTRACT — byte-source seam (`ingest/extract.py`, `ingest/itemize.py`)

Today `build_item` reads member bytes from **canonical storage**
(`platform.get_object(s3_client, bucket, member.canonical_key)`) for the
`raster_auto` and `sidecar` strategies. In reference mode those bytes were never
copied there; they must be read from the **source adapter**
(`adapter.get(source_fetch_path(...))`).

Introduce a minimal async byte-source seam so the build functions don't care
where bytes live. Shape (final naming settled in the plan):

```python
class MemberBytes(Protocol):
    async def read(self, member: ExtractMember) -> bytes: ...

# copy mode: read member.canonical_key from platform storage
# reference mode: read member.source_path from the source adapter
```

`build_item` takes a `MemberBytes` (or an equivalent callable) instead of
`s3_client`/`bucket` directly; `run_itemize` constructs the right one from
`config.storage_mode` + the adapter/s3 client it already has. The
best-effort-geometry layer (`_best_effort_raster_geometry`, I-27) reads through
the same seam, so reference-mode rasters get the same geometry recovery.

`ExtractMember` already carries `source_path`; it may need the source-relative
`fetch_path` too (or the seam computes it from `config.source_path`).

The built item is **identical** to copy mode: asset href stays
`/api/assets/{collection}/{item}/{filename}` in both modes (the redirect
difference lives entirely in the app's `resolveAssetTarget`).

### 4.4 ITEMIZE / post-ingest

No functional change beyond routing the byte-source. `post_ingest` (`leave` /
`move` / `delete`) still applies via the adapter. Note: `delete`/`move` on a
*referenced* source removes the very bytes the catalog points at — documented as
a caveat; `leave` is the sane default for reference and what the demo uses. (No
new guard in this slice; logged in ISSUES.)

## 5. App changes (TypeScript)

### 5.1 `resolveAssetTarget` (`app/src/lib/storage/resolve.ts`)

Add a reference branch. Look up a persisted `source_href` for
`(collection, item_id, filename)`; if present, 302 to it; else presign canonical
as today.

```sql
SELECT if.source_href
  FROM stac_higher.ingest_files if
  JOIN stac_higher.collection_connections cc ON cc.id = if.association_id
 WHERE cc.collection_id = $1
   AND if.item_id = $2
   AND if.source_href IS NOT NULL
   AND regexp_replace(if.source_path, '^.*/', '') = $3   -- basename = filename
 ORDER BY if.version DESC
 LIMIT 1
```

One indexed lookup (`ingest_files_item_idx`). The DB query lives in a small
helper (e.g. `app/src/lib/storage/reference.ts` or an
`associations/` repo function) so `resolve.ts` stays thin and the helper is
unit-testable. `AssetTarget.mode` extends to `"canonical" | "reference"`.

Error handling: helper returns `string | null`. `null` ⇒ canonical (today's
path). A thrown DB error propagates (route → 500) rather than silently falling
back to a canonical object that does not exist for a referenced item.

### 5.2 Route / DDL

- `/api/assets/[collection]/[item]/[asset].ts` is untouched (it depends only on
  `{ url }`).
- Migration 006 added to `app/src/lib/db/migrate.ts` `MIGRATIONS`.

## 6. What stays the same

- Association config schema (`storage_mode` already parsed app-side and in
  `ingest/config.py`), the s3-only reference restriction, and the Data-flow UI.
- The STAC item shape, asset hrefs, validation gate, and pgstac upsert.
- Copy-mode flow end to end.

## 7. Testing (TDD)

Pipeline (`uv run pytest`, `ruff`):
- `test_ingest_fetch.py`: reference mode advances `settled → stored`, writes
  `source_href`, writes `item_id`, returns a positive count, no `put_object`
  call; copy mode unchanged.
- `test_adapters.py`: `S3Adapter.public_object_url` for path-style/custom
  endpoint and virtual-hosted default; no credential access.
- `test_ingest_extract.py` / `test_ingest_itemize.py`: byte-source seam reads
  from the adapter in reference mode and from canonical in copy mode; item is
  identical across modes for the same bytes; best-effort geometry works in
  reference mode.
- repo fake (`tests/_repo_fake.py`, `_ingest_fake.py`) gains `source_href`.

App (`npm run verify` → vitest):
- `resolve` returns the reference `source_href` when present (mode
  `"reference"`) and falls back to canonical presign when absent; DB error
  propagates.
- migration 006 present/idempotent (mirrors existing migrate tests if any).

## 8. Live verification (against the running docker stack)

Folded in from Phase 4 completeness (ROADMAP "Remaining (deferred)"):
1. **One continuous scheduler-driven reference run**: provision a source
   connection (encrypted creds) + a reference ingest association pointed at a
   public/path-style MinIO bucket; drop a file; observe poll → DISCOVER
   (`seen → settled` across two polls) → GROUP → FETCH (reference: `stored` +
   `source_href`, no copy) → EXTRACT/ITEMIZE → queryable pgstac item; then
   `GET /api/assets/...` 302s to the source URL and the bytes download.
2. **SFTP/FTP source run (ISSUE I-4)** for the *copy* path — a continuous
   scheduler-driven poll → … → itemized through a provisioned SFTP or FTP
   connection, closing the last non-s3 adapter gap. (Reference stays s3-only;
   this leg exercises copy mode over a non-s3 adapter.)

## 9. Out of scope / follow-ups (log in ISSUES)

- **Private-source reference** via a pipeline resolver endpoint that mints fresh
  presigned URLs per asset read (durable, respects the crypto boundary). Named
  alternative to today's public-URL approach.
- **Reference-mode checksum** (would require reading source bytes at FETCH; we
  defer — fingerprint drives versioning).
- **`post_ingest: delete`/`move` on a referenced source** deletes the bytes the
  catalog references — caveat only, no guard this slice.
- **Browser-reachable source endpoint**: the constructed URL uses the
  connection's configured endpoint; a split internal/browser endpoint is the
  same class of concern as canonical (ISSUES I-15).

## 10. Non-goals

Delivery pipeline (Phase 5), retention/GC of canonical assets, per-collection
read authorization (ADR 0002 / I-1).
