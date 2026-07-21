# Phase 4 Slice C — `storage_mode: reference` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the ingest pipeline so `storage_mode: reference` associations catalog items whose asset bytes stay at the source — no copy into canonical storage — and the app's asset route redirects downloads to a persisted stable source URL.

**Architecture:** Reference mode targets *durably-reachable* sources only. The pipeline persists a stable public object URL in `ingest_files.source_href` at FETCH (no byte copy), EXTRACT reads member bytes from the source adapter instead of canonical storage via a byte-source seam, and the app's `resolveAssetTarget` 302s to `source_href` when present — never decrypting or presigning, preserving the `crypto.ts` "app never decrypts" invariant. Private sources use copy mode; destructive `post_ingest` is rejected for reference.

**Tech Stack:** Python 3.12 pipeline (pytest, ruff, boto3/rasterio/rio-stac); Astro 6 + TypeScript app (vitest, Zod, node:crypto); PostgreSQL/pgstac; MinIO/S3.

## Global Constraints

- **Base branch `ai/main`; work in a worktree** off it: `git worktree add .claude/worktrees/slice-c -b ai/slice-c ai/main`. Never commit to `main`.
- **Verify gates (must pass before done):** app — `npm run verify` (repo root); pipeline — `cd services/pipeline && uv run pytest && uv run ruff check`.
- **The cross-runtime config contract must not drift:** field names/defaults in `app/src/lib/associations/schemas.ts` (Zod) and `services/pipeline/src/pipeline/ingest/config.py` stay identical (§5.1).
- **ADR 0001 (migration ownership):** the app owns ALL `stac_higher.*` DDL (including `ingest_files`); the pipeline reads/writes rows but NEVER runs DDL.
- **`crypto.ts` invariant:** no app request path may decrypt connection credentials. Reference redirect URLs are plain stored strings — no presign, no decrypt.
- **Reference is s3-only** (already enforced in the association route); this slice does not change that.
- **Asset href is identical in both modes:** built items always carry `href: /api/assets/{collection}/{item}/{filename}`. Only `resolveAssetTarget` differs by mode.
- **Commit messages** end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

**App (TypeScript):**
- Modify `app/src/lib/db/migrate.ts` — append migration 006 (`ingest_files.source_href`).
- Modify `app/src/lib/associations/schemas.ts` — cross-field guard on `ingestConfigSchema`.
- Create `app/src/lib/storage/reference.ts` — `lookupReferenceHref(collection, itemId, filename)` DB helper.
- Modify `app/src/lib/storage/resolve.ts` — reference branch; `AssetTarget.mode` gains `"reference"`.
- Create/modify tests under `app/src/__tests__/`.

**Pipeline (Python):**
- Modify `services/pipeline/src/pipeline/ingest/repo.py` — `LedgerEntry.source_href`, columns, mutable set, row mapper.
- Modify `services/pipeline/src/pipeline/connections/adapters/base.py` — `public_object_url` default (raises).
- Modify `services/pipeline/src/pipeline/connections/adapters/s3.py` — `public_object_url` override.
- Modify `services/pipeline/src/pipeline/ingest/fetch.py` — reference branch.
- Modify `services/pipeline/src/pipeline/ingest/extract.py` — byte-source seam (`MemberByteSource`, `CanonicalByteSource`, `SourceAdapterByteSource`); `build_item` reads via the seam.
- Modify `services/pipeline/src/pipeline/ingest/itemize.py` — construct the byte source from `storage_mode`.
- Modify `services/pipeline/src/pipeline/ingest/postingest.py` — skip destructive actions in reference mode.
- Modify tests: `test_ingest_fetch.py`, `test_adapters.py`, `test_ingest_extract.py`, `test_ingest_itemize.py`, `test_ingest_postingest.py`; the fake ledger in `tests/_ingest_fake.py` needs no change (it uses `setattr`).

**Docs:** `docs/FEATURES.md`, `docs/ISSUES.md`, `ROADMAP.md`, `docs/decisions/0005-asset-service.md`, and the auto-memory `phase-progress.md`.

---

## Task 1: App migration 006 — `ingest_files.source_href`

**Files:**
- Modify: `app/src/lib/db/migrate.ts` (append to the `MIGRATIONS` array, after `005_...`, before the closing `];` at line ~261-262)

**Interfaces:**
- Produces: a nullable `text` column `stac_higher.ingest_files.source_href`. Non-null ⇒ referenced asset; value is the stable source URL. Consumed by Task 8 (app) and Task 5 (pipeline write).

- [ ] **Step 1: Add the migration object.** In `app/src/lib/db/migrate.ts`, add a new element to the `MIGRATIONS` array immediately after the `005_ingest_associations_and_files` object:

```ts
  {
    // Phase 4 Slice C: reference-mode assets keep their bytes at the source.
    // The pipeline records the stable source URL here at FETCH; the app's asset
    // route (resolveAssetTarget) 302s to it. Null ⇒ canonical (copy mode /
    // manual upload). ADR 0001: app owns this DDL, pipeline only writes rows.
    name: "006_ingest_files_source_href",
    sql: `
      ALTER TABLE stac_higher.ingest_files
        ADD COLUMN IF NOT EXISTS source_href text;
    `,
  },
```

- [ ] **Step 2: Verify the app still builds/type-checks.**

Run: `cd app && npx astro check --minimumSeverity error`
Expected: no new errors (the PostToolUse hook also runs this).

- [ ] **Step 3: Confirm existing migration tests still pass (if present).**

Run: `cd app && npx vitest run --silent 2>&1 | tail -20`
Expected: PASS (no migration test asserts an exact array length; if one does, update its count).

- [ ] **Step 4: Commit.**

```bash
git add app/src/lib/db/migrate.ts
git commit -m "feat(db): migration 006 — ingest_files.source_href for reference mode

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: App schema guard — reject destructive `post_ingest` for reference mode

**Files:**
- Modify: `app/src/lib/associations/schemas.ts:77-93` (add `.superRefine` to `ingestConfigSchema`)
- Test: `app/src/__tests__/associations-schemas.test.ts`

**Interfaces:**
- Produces: `ingestConfigSchema` rejects `{ storage_mode: "reference", post_ingest: "delete" | "move:<path>" }` with a custom issue on path `["post_ingest"]`. Consumed by `parseAssociationCreate`/`parseAssociationUpdate` (unchanged).

- [ ] **Step 1: Write the failing tests.** Add to `app/src/__tests__/associations-schemas.test.ts` inside the `ingestConfigSchema` describe block:

```ts
it("rejects reference mode with post_ingest delete", () => {
  const result = ingestConfigSchema.safeParse({
    source_path: "/out",
    storage_mode: "reference",
    post_ingest: "delete",
  });
  expect(result.success).toBe(false);
  if (!result.success) {
    expect(result.error.issues[0].path).toEqual(["post_ingest"]);
  }
});

it("rejects reference mode with post_ingest move", () => {
  const result = ingestConfigSchema.safeParse({
    source_path: "/out",
    storage_mode: "reference",
    post_ingest: "move:/archive",
  });
  expect(result.success).toBe(false);
});

it("allows reference mode with post_ingest leave", () => {
  const result = ingestConfigSchema.safeParse({
    source_path: "/out",
    storage_mode: "reference",
    post_ingest: "leave",
  });
  expect(result.success).toBe(true);
});

it("still allows copy mode with post_ingest delete", () => {
  const result = ingestConfigSchema.safeParse({
    source_path: "/out",
    storage_mode: "copy",
    post_ingest: "delete",
  });
  expect(result.success).toBe(true);
});
```

- [ ] **Step 2: Run to verify they fail.**

Run: `cd app && npx vitest run associations-schemas -t "reference mode"`
Expected: FAIL (the first two currently parse successfully — no guard yet).

- [ ] **Step 3: Add the guard.** In `app/src/lib/associations/schemas.ts`, change the `ingestConfigSchema` definition (currently ends with `.strict();` at line ~93) to append a refinement:

```ts
export const ingestConfigSchema = z
  .object({
    source_path: z.string().min(1, "source_path is required"),
    include: globList,
    exclude: globList,
    poll_frequency_seconds: z.number().int().min(60).default(300),
    storage_mode: z.enum(STORAGE_MODES).default("copy"),
    grouping: groupingSchema.default(() => groupingSchema.parse({})),
    metadata: metadataSchema.default(() => metadataSchema.parse({})),
    post_ingest: postIngestSchema,
  })
  .strict()
  .superRefine((cfg, ctx) => {
    // Reference mode's source bytes ARE the catalog's asset — deleting or moving
    // them would orphan every item that references them. Only `leave` is valid.
    if (cfg.storage_mode === "reference" && cfg.post_ingest !== "leave") {
      ctx.addIssue({
        code: "custom",
        path: ["post_ingest"],
        message:
          "reference mode cannot delete or move the source — its bytes are the " +
          "catalog's asset; use post_ingest 'leave' (or switch to copy mode)",
      });
    }
  });
```

- [ ] **Step 4: Run to verify they pass.**

Run: `cd app && npx vitest run associations-schemas`
Expected: PASS (all, including the four new cases).

- [ ] **Step 5: Commit.**

```bash
git add app/src/lib/associations/schemas.ts app/src/__tests__/associations-schemas.test.ts
git commit -m "feat(associations): reject delete/move post_ingest in reference mode

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Pipeline repo — persist and read `source_href`

**Files:**
- Modify: `services/pipeline/src/pipeline/ingest/repo.py` (`LedgerEntry` dataclass ~53-67; `_LEDGER_COLUMNS` ~141-144; `_LEDGER_MUTABLE` ~146; `_to_ledger_entry` ~149-175)
- Test: `services/pipeline/tests/test_ingest_fetch.py` (fake-repo round-trip via `set_ledger_fields`)

**Interfaces:**
- Produces: `LedgerEntry.source_href: str | None` (default `None`); `set_ledger_fields(id, source_href=...)` is accepted (added to `_LEDGER_MUTABLE`). Consumed by Task 5 (FETCH writes it) and, in production SQL, exposed for future reads. The in-memory `FakeIngestRepo` already supports it via `setattr` — no fake change needed.

- [ ] **Step 1: Write the failing test.** Add to `services/pipeline/tests/test_ingest_fetch.py`:

```python
async def test_set_ledger_fields_accepts_source_href_on_fake():
    from tests._ingest_fake import FakeIngestRepo
    from pipeline.ingest.repo import LedgerEntry

    repo = FakeIngestRepo()
    repo.rows["1"] = LedgerEntry(
        id="1", association_id="a", source_path="products/scene.tif",
        version=1, size=10, fingerprint="f", checksum=None,
        status="settled", item_id=None,
    )
    await repo.set_ledger_fields("1", source_href="https://src/scene.tif")
    assert repo.rows["1"].source_href == "https://src/scene.tif"
```

- [ ] **Step 2: Run to verify it fails.**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_fetch.py::test_set_ledger_fields_accepts_source_href_on_fake -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument` is NOT it; it fails because `LedgerEntry` has no `source_href` attribute (the constructor above omits it, and `setattr` would create an ad-hoc attr — so assert also guards the dataclass field). Expected failure: `AttributeError`/dataclass field missing.

- [ ] **Step 3: Add the field + plumbing.** In `services/pipeline/src/pipeline/ingest/repo.py`:

  (a) Add to `LedgerEntry` (after `item_id: str | None`):
```python
    source_href: str | None = None
```
  (b) Extend `_LEDGER_COLUMNS`:
```python
_LEDGER_COLUMNS = (
    "id, association_id, source_path, version, size, fingerprint, checksum,"
    " status, item_id, source_href, created_at, updated_at"
)
```
  (c) Extend `_LEDGER_MUTABLE`:
```python
_LEDGER_MUTABLE = frozenset({"status", "size", "fingerprint", "checksum", "item_id", "source_href"})
```
  (d) Update `_to_ledger_entry` to unpack the new column (it is now between `item_id` and `created_at`):
```python
def _to_ledger_entry(record: Sequence[Any]) -> LedgerEntry:
    (
        lid, association_id, source_path, version, size, fingerprint, checksum,
        status, item_id, source_href, created_at, updated_at,
    ) = record
    return LedgerEntry(
        id=str(lid), association_id=str(association_id), source_path=source_path,
        version=int(version), size=int(size) if size is not None else None,
        fingerprint=fingerprint, checksum=checksum, status=status, item_id=item_id,
        source_href=source_href, created_at=created_at, updated_at=updated_at,
    )
```

- [ ] **Step 4: Run to verify it passes.**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_fetch.py::test_set_ledger_fields_accepts_source_href_on_fake -q`
Expected: PASS.

- [ ] **Step 5: Run the full fetch + repo suites.**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_fetch.py tests/test_ingest_discover.py -q`
Expected: PASS (column-order change is internal; no consumer indexes the tuple positionally outside `_to_ledger_entry`).

- [ ] **Step 6: Commit.**

```bash
git add services/pipeline/src/pipeline/ingest/repo.py services/pipeline/tests/test_ingest_fetch.py
git commit -m "feat(pipeline): ingest_files.source_href in the ledger repo

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Pipeline — `S3Adapter.public_object_url`

**Files:**
- Modify: `services/pipeline/src/pipeline/connections/adapters/base.py` (add a concrete default method to `StorageAdapter`)
- Modify: `services/pipeline/src/pipeline/connections/adapters/s3.py` (override on `S3Adapter`)
- Test: `services/pipeline/tests/test_adapters.py`

**Interfaces:**
- Produces: `StorageAdapter.public_object_url(path: str) -> str` (base raises `NotImplementedError`); `S3Adapter.public_object_url` returns a stable object URL from config, reading **no credentials**. Consumed by Task 5 (FETCH). `path` is the value `source_fetch_path(config.source_path, relpath)` produces (the full S3 key).

- [ ] **Step 1: Write the failing tests.** Add to `services/pipeline/tests/test_adapters.py`:

```python
def test_s3_public_object_url_path_style_custom_endpoint():
    from pipeline.connections.adapters.s3 import S3Adapter
    a = S3Adapter(
        {"bucket": "src-bucket", "endpoint": "http://minio:9000", "force_path_style": True},
        {"access_key_id": "k", "secret_access_key": "s"},
    )
    assert a.public_object_url("products/scene.tif") == \
        "http://minio:9000/src-bucket/products/scene.tif"

def test_s3_public_object_url_virtual_hosted_default_aws():
    from pipeline.connections.adapters.s3 import S3Adapter
    a = S3Adapter(
        {"bucket": "src-bucket", "region": "us-west-2"},
        {"access_key_id": "k", "secret_access_key": "s"},
    )
    assert a.public_object_url("products/scene.tif") == \
        "https://src-bucket.s3.us-west-2.amazonaws.com/products/scene.tif"

def test_base_adapter_public_object_url_raises():
    import pytest
    from pipeline.connections.adapters.sftp import SftpAdapter  # any non-s3
    # Construct minimally enough to call the base method; if construction needs
    # more, assert on S3's sibling instead — the invariant is the base default.
    with pytest.raises(NotImplementedError):
        StorageAdapter.public_object_url(object.__new__(SftpAdapter), "x")  # type: ignore[arg-type]
```

If constructing `SftpAdapter` via `object.__new__` is awkward, replace the third test with a tiny local subclass:
```python
def test_base_adapter_public_object_url_raises():
    import pytest
    from pipeline.connections.adapters.base import StorageAdapter
    class Dummy(StorageAdapter):
        protocol = "dummy"
        async def test(self): ...
        async def list(self, prefix=""): return []
        async def get(self, path): return b""
        async def put(self, path, data): ...
        async def delete(self, path): ...
    with pytest.raises(NotImplementedError):
        Dummy().public_object_url("x")
```
Use the local-subclass version (more robust). Ensure `from pipeline.connections.adapters.base import StorageAdapter` is imported in the test module.

- [ ] **Step 2: Run to verify they fail.**

Run: `cd services/pipeline && uv run pytest tests/test_adapters.py -k public_object_url -q`
Expected: FAIL — `AttributeError: 'S3Adapter' object has no attribute 'public_object_url'`.

- [ ] **Step 3: Add the base default.** In `services/pipeline/src/pipeline/connections/adapters/base.py`, add to `StorageAdapter` (after `delete`):

```python
    def public_object_url(self, path: str) -> str:
        """Stable, credential-free URL for a source object (reference storage
        mode, §5.1). Only object-store adapters implement this; the base raises
        so a non-s3 adapter reaching reference mode fails loudly (the app also
        restricts reference associations to s3)."""
        raise NotImplementedError(
            f"{self.protocol} connections do not support storage_mode 'reference'"
        )
```

- [ ] **Step 4: Add the S3 override.** In `services/pipeline/src/pipeline/connections/adapters/s3.py`, add a method to `S3Adapter` (near `_host`):

```python
    def public_object_url(self, path: str) -> str:
        """Construct a stable object URL from config (no credentials read).
        Path-style for a custom endpoint or force_path_style (MinIO); otherwise
        virtual-hosted against the AWS regional host."""
        key = path.lstrip("/")
        if self._endpoint or self._force_path_style:
            base = (self._endpoint or f"https://s3.{self._region}.amazonaws.com").rstrip("/")
            return f"{base}/{self._bucket}/{key}"
        region = self._region or "us-east-1"
        return f"https://{self._bucket}.s3.{region}.amazonaws.com/{key}"
```

- [ ] **Step 5: Run to verify they pass.**

Run: `cd services/pipeline && uv run pytest tests/test_adapters.py -k public_object_url -q`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add services/pipeline/src/pipeline/connections/adapters/base.py services/pipeline/src/pipeline/connections/adapters/s3.py services/pipeline/tests/test_adapters.py
git commit -m "feat(pipeline): S3Adapter.public_object_url for reference mode

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Pipeline FETCH — reference branch (settled → stored + source_href)

**Files:**
- Modify: `services/pipeline/src/pipeline/ingest/fetch.py:58-63` (replace the skip short-circuit)
- Test: `services/pipeline/tests/test_ingest_fetch.py`

**Interfaces:**
- Consumes: `LedgerEntry.source_href` + `set_ledger_fields(..., source_href=)` (Task 3); `adapter.public_object_url(path)` (Task 4); `source_fetch_path` (existing).
- Produces: reference FETCH advances each still-`settled` member to `stored`, writes `item_id` + `source_href`, performs NO `put_object`, and returns the stored count (so `jobs/ingest.py` enqueues ITEMIZE). Copy mode unchanged.

- [ ] **Step 1: Write the failing test.** Add to `services/pipeline/tests/test_ingest_fetch.py` (reuse the module's existing helpers for building an association/config; mirror an existing copy-mode test's setup, but with `storage_mode: "reference"`):

```python
async def test_fetch_reference_mode_records_source_href_no_copy():
    from tests._ingest_fake import FakeIngestRepo, FakeAdapter, FakeS3
    from pipeline.ingest.config import parse_ingest_config
    from pipeline.ingest.fetch import fetch_stage
    from pipeline.ingest.repo import LedgerEntry, STATUS_SETTLED, STATUS_STORED
    from pipeline.connections.repo import ConnectionRow
    from pipeline.ingest.repo import IngestAssociation

    repo = FakeIngestRepo()
    repo.rows["1"] = LedgerEntry(
        id="1", association_id="a", source_path="products/scene.tif",
        version=1, size=10, fingerprint="f", checksum=None,
        status=STATUS_SETTLED, item_id=None,
    )
    conn = ConnectionRow(
        id="c", name="src", protocol="s3",
        config={"bucket": "src-bucket", "endpoint": "http://minio:9000", "force_path_style": True},
        credentials=b"", host_key=None, enabled=True,
    )
    assoc = IngestAssociation(id="a", collection_id="col", config={}, connection=conn)
    config = parse_ingest_config({"source_path": "products", "storage_mode": "reference"})

    # A real S3Adapter provides public_object_url; FETCH must not call get/put on it.
    from pipeline.connections.adapters.s3 import S3Adapter
    adapter = S3Adapter(conn.config, {"access_key_id": "k", "secret_access_key": "s"})
    s3 = FakeS3()

    stored = await fetch_stage(repo, assoc, config, adapter, s3, "bucket",
                               "scene", ["products/scene.tif"])

    assert stored == 1
    assert s3.puts == []  # no canonical copy
    row = repo.rows["1"]
    assert row.status == STATUS_STORED
    assert row.item_id == "scene"
    assert row.source_href == "http://minio:9000/src-bucket/products/scene.tif"
```
(Adjust `ConnectionRow`/`IngestAssociation` construction to match the existing test module's helper if one exists — grep the file for how other tests build them and reuse that.)

- [ ] **Step 2: Run to verify it fails.**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_fetch.py::test_fetch_reference_mode_records_source_href_no_copy -q`
Expected: FAIL — current reference branch returns 0 and writes nothing.

- [ ] **Step 3: Implement the reference branch.** In `services/pipeline/src/pipeline/ingest/fetch.py`, replace the current reference short-circuit (lines ~58-63):

```python
    if config.storage_mode == "reference":
        logger.info(
            "ingest fetch: reference mode deferred to Slice C — skipping copy",
            extra={"association_id": association.id, "item_id": item_id},
        )
        return 0
```

with:

```python
    if config.storage_mode == "reference":
        return await _reference_stage(repo, association, config, adapter, item_id, source_paths)
```

and add the helper (below `fetch_stage`):

```python
async def _reference_stage(
    repo: IngestRepo,
    association: IngestAssociation,
    config: IngestConfig,
    adapter: StorageAdapter,
    item_id: str,
    source_paths: list[str],
) -> int:
    """Reference mode: no byte copy. Record the stable source URL and advance the
    ledger settled → stored so EXTRACT/ITEMIZE run. Idempotent (only acts on a
    still-`settled` row). A per-member failure marks only that member failed."""
    stored = 0
    for source_path in source_paths:
        latest = await repo.get_latest_ledger(association.id, source_path)
        if latest is None or latest.status != STATUS_SETTLED:
            continue
        try:
            href = adapter.public_object_url(source_fetch_path(config.source_path, source_path))
            await repo.set_ledger_fields(
                latest.id, status=STATUS_STORED, item_id=item_id, source_href=href
            )
            stored += 1
        except Exception:
            await repo.set_ledger_fields(latest.id, status=STATUS_FAILED)
            logger.exception(
                "ingest reference-fetch failed for source file",
                extra={"association_id": association.id, "item_id": item_id,
                       "source_path": source_path},
            )
    logger.info(
        "ingest reference-fetch group done",
        extra={"association_id": association.id, "item_id": item_id,
               "stored": stored, "files": len(source_paths)},
    )
    return stored
```

- [ ] **Step 4: Run to verify it passes.**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_fetch.py -q`
Expected: PASS (new test + all existing copy-mode tests).

- [ ] **Step 5: Commit.**

```bash
git add services/pipeline/src/pipeline/ingest/fetch.py services/pipeline/tests/test_ingest_fetch.py
git commit -m "feat(pipeline): FETCH reference branch — record source_href, no copy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Pipeline EXTRACT — byte-source seam; read source bytes in reference mode

**Files:**
- Modify: `services/pipeline/src/pipeline/ingest/extract.py` (add the seam; `build_item`, `_best_effort_raster_geometry`, `_resolve_geometry_fallback` read via the seam)
- Modify: `services/pipeline/src/pipeline/ingest/itemize.py:139-179` (`run_itemize` constructs the byte source from `storage_mode`)
- Test: `services/pipeline/tests/test_ingest_extract.py` (update the 7 `build_item(...)` call sites; add a reference-reads test), `services/pipeline/tests/test_ingest_itemize.py` (add a reference round-trip)

**Interfaces:**
- Consumes: `ExtractMember.source_path` (existing), `source_fetch_path` (existing), `config.storage_mode`, the adapter.
- Produces:
  - `MemberByteSource` protocol with `async def read(self, member: ExtractMember) -> bytes`.
  - `CanonicalByteSource(s3_client, bucket)` and `SourceAdapterByteSource(adapter, source_path)`.
  - `build_item(..., byte_source: MemberByteSource, ...)` — replaces the `s3_client`/`bucket` params.
  Consumed only by `run_itemize` (production) and the extract tests.

- [ ] **Step 1: Write the failing reference-reads test.** Add to `services/pipeline/tests/test_ingest_extract.py`:

```python
async def test_build_item_reference_reads_from_adapter():
    from pipeline.ingest.extract import build_item, SourceAdapterByteSource
    from tests._ingest_fake import FakeAdapter

    members = [_member("scene.tif")]
    # canonical_key on the member is "assets/col/scene/scene.tif"; reference mode
    # must NOT use it — it reads the source via the adapter instead.
    adapter = FakeAdapter(blobs={"products/scene.tif": _geotiff_bytes()})
    byte_source = SourceAdapterByteSource(adapter, "products")
    item = await build_item(
        collection_id="col", item_id="scene", members=members,
        metadata={"strategy": "raster_auto"}, byte_source=byte_source,
        asset_href_base="/api/assets",
    )
    assert item["geometry"] is not None
    assert adapter.get_calls == ["products/scene.tif"]
```
(`_member("scene.tif")` builds an `ExtractMember` with `source_path="products/scene.tif"` in this test module — verify the helper; if it sets a bare `"scene.tif"` source_path, pass `SourceAdapterByteSource(adapter, "")` and set the blob key to `"scene.tif"` accordingly.)

- [ ] **Step 2: Run to verify it fails.**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_extract.py::test_build_item_reference_reads_from_adapter -q`
Expected: FAIL — `ImportError: cannot import name 'SourceAdapterByteSource'` / `build_item() got an unexpected keyword argument 'byte_source'`.

- [ ] **Step 3: Add the seam to `extract.py`.** Near the top (after the existing imports, before `build_assets`), add:

```python
from collections.abc import Awaitable  # if not already imported
from typing import Protocol
from pipeline.connections.adapters.base import StorageAdapter
from pipeline.ingest.discover import source_fetch_path


class MemberByteSource(Protocol):
    """Where EXTRACT reads a member's bytes from — canonical storage (copy mode)
    or the source adapter (reference mode). Lets build_item stay mode-agnostic."""
    async def read(self, member: "ExtractMember") -> bytes: ...


@dataclass(frozen=True)
class CanonicalByteSource:
    """Copy mode: read the object FETCH wrote to canonical platform storage."""
    s3_client: "platform.S3Like"
    bucket: str

    async def read(self, member: "ExtractMember") -> bytes:
        return await asyncio.to_thread(
            platform.get_object, self.s3_client, self.bucket, member.canonical_key
        )


@dataclass(frozen=True)
class SourceAdapterByteSource:
    """Reference mode: read the object in place from the source adapter."""
    adapter: StorageAdapter
    source_path: str

    async def read(self, member: "ExtractMember") -> bytes:
        return await self.adapter.get(source_fetch_path(self.source_path, member.source_path))
```

- [ ] **Step 4: Rewire `build_item` and the geometry helpers to the seam.** In `extract.py`:

  (a) `build_item` signature — replace `s3_client: platform.S3Like, bucket: str` with `byte_source: MemberByteSource`. Update the three read sites inside `build_item`:
   - sidecar: `data = await byte_source.read(sidecar)`
   - raster_auto primary: `data = await byte_source.read(primary)`
   - the fallback call: `await _resolve_geometry_fallback(item, members, byte_source=byte_source, collection_fallback=collection_fallback)`

  (b) `_resolve_geometry_fallback` signature — replace `s3_client`/`bucket` with `byte_source: MemberByteSource`; pass it to `_best_effort_raster_geometry`.

  (c) `_best_effort_raster_geometry` signature — replace `(primary, s3_client, bucket)` with `(primary, byte_source: MemberByteSource)`; its body becomes:
```python
async def _best_effort_raster_geometry(
    primary: ExtractMember, byte_source: MemberByteSource
) -> tuple[dict[str, Any], list[float]] | None:
    if not is_gdal_candidate(primary.filename):
        return None
    try:
        data = await byte_source.read(primary)
    except Exception:
        return None
    return geometry_from_raster(data)
```

- [ ] **Step 5: Update the 7 existing `build_item` call sites** in `test_ingest_extract.py`. Each currently passes `s3_client=s3, bucket="bucket"`. Replace with `byte_source=CanonicalByteSource(s3, "bucket")` and add `from pipeline.ingest.extract import CanonicalByteSource` to the test imports. Example (the `test_build_item_dispatches_raster_auto_reads_from_storage` case):

```python
item = await build_item(
    collection_id="col", item_id="scene", members=members,
    metadata={"strategy": "raster_auto"},
    byte_source=CanonicalByteSource(s3, "bucket"),
    asset_href_base="/api/assets",
)
```
Apply the identical `s3_client=…, bucket=…` → `byte_source=CanonicalByteSource(…)` substitution to all 7.

- [ ] **Step 6: Wire `run_itemize` to pick the byte source.** In `services/pipeline/src/pipeline/ingest/itemize.py`, import the seam and construct it from `config.storage_mode`, then pass it to `build_item` (replacing `s3_client=s3_client, bucket=bucket`):

```python
from pipeline.ingest.extract import (
    ExtractError, ExtractMember, MetadataConfig, bbox_to_polygon, build_item,
    parse_metadata, CanonicalByteSource, SourceAdapterByteSource,
)
...
    byte_source = (
        SourceAdapterByteSource(adapter, config.source_path)
        if config.storage_mode == "reference"
        else CanonicalByteSource(s3_client, bucket)
    )
    try:
        item_dict = await build_item(
            collection_id=association.collection_id,
            item_id=item_id,
            members=members,
            metadata=config.metadata,
            byte_source=byte_source,
            asset_href_base=asset_href_base,
            collection_fallback=collection_fallback,
            cfg=cfg,
        )
```
(`run_itemize` keeps its `s3_client`/`bucket` params — they now feed `CanonicalByteSource`.)

- [ ] **Step 7: Add a reference round-trip test for `run_itemize`.** Add to `services/pipeline/tests/test_ingest_itemize.py` a test that sets `storage_mode: "reference"`, stages a `stored` ledger row with a `source_href`, provides a `FakeAdapter` whose `blobs` hold the source raster at `source_fetch_path`, and asserts the item is upserted with a non-null geometry and the members go `itemized`. Mirror the existing copy-mode round-trip test in that file, changing only the config's `storage_mode` and putting bytes in `adapter.blobs` instead of the canonical `FakeS3`/platform get.

- [ ] **Step 8: Run the extract + itemize suites.**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_extract.py tests/test_ingest_itemize.py -q`
Expected: PASS (updated copy-mode tests + new reference tests).

- [ ] **Step 9: Commit.**

```bash
git add services/pipeline/src/pipeline/ingest/extract.py services/pipeline/src/pipeline/ingest/itemize.py services/pipeline/tests/test_ingest_extract.py services/pipeline/tests/test_ingest_itemize.py
git commit -m "feat(pipeline): byte-source seam — EXTRACT reads source bytes in reference mode

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Pipeline post-ingest — skip destructive actions in reference mode

**Files:**
- Modify: `services/pipeline/src/pipeline/ingest/postingest.py:24-45`
- Test: `services/pipeline/tests/test_ingest_postingest.py`

**Interfaces:**
- Consumes: `config.storage_mode`, `config.post_ingest` (existing).
- Produces: `apply_post_ingest` treats a `delete`/`move:` action as a no-op (logged) when `storage_mode == "reference"`. Defense-in-depth behind the app guard (Task 2). Copy mode unchanged.

- [ ] **Step 1: Write the failing test.** Add to `services/pipeline/tests/test_ingest_postingest.py`:

```python
async def test_reference_mode_skips_destructive_post_ingest():
    from pipeline.ingest.postingest import apply_post_ingest
    from pipeline.ingest.config import parse_ingest_config
    from tests._ingest_fake import FakeAdapter

    adapter = FakeAdapter(blobs={"products/scene.tif": b"x"})
    config = parse_ingest_config({
        "source_path": "products", "storage_mode": "reference", "post_ingest": "delete",
    })
    await apply_post_ingest(adapter, config, source_paths=["products/scene.tif"])
    # Nothing deleted — the referenced bytes ARE the asset.
    assert adapter.blobs == {"products/scene.tif": b"x"}
```
(If `FakeAdapter.delete` currently has `# pragma: no cover`, this test now exercises the no-delete path; keep the assertion on `blobs`.)

- [ ] **Step 2: Run to verify it fails.**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_postingest.py -k reference -q`
Expected: FAIL — the current code deletes the blob (`adapter.blobs` becomes `{}`).

- [ ] **Step 3: Add the guard.** In `services/pipeline/src/pipeline/ingest/postingest.py`, at the start of `apply_post_ingest` (after `action = config.post_ingest`):

```python
    if action == "leave":
        return
    if config.storage_mode == "reference":
        # Reference mode never owns the bytes — delete/move would orphan the
        # item's asset. The app guard rejects this at config time; this is
        # defense-in-depth for configs written before the guard / directly to DB.
        logger.info(
            "post-ingest %r skipped: reference mode keeps source bytes", action,
            extra={"action": action},
        )
        return
```

- [ ] **Step 4: Run to verify it passes.**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_postingest.py -q`
Expected: PASS (new test + existing copy-mode tests).

- [ ] **Step 5: Commit.**

```bash
git add services/pipeline/src/pipeline/ingest/postingest.py services/pipeline/tests/test_ingest_postingest.py
git commit -m "feat(pipeline): skip destructive post_ingest in reference mode

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: App `resolveAssetTarget` — reference branch

**Files:**
- Create: `app/src/lib/storage/reference.ts`
- Modify: `app/src/lib/storage/resolve.ts`
- Test: `app/src/__tests__/storage-reference.test.ts` (new)

**Interfaces:**
- Consumes: `stac_higher.ingest_files.source_href` (Task 1); the app `query<T>(sql, params)` helper (`{ rows }`).
- Produces: `lookupReferenceHref(collection, itemId, filename): Promise<string | null>`; `resolveAssetTarget` returns `{ url, mode: "reference" }` when a href is found, else the existing canonical `{ url, mode: "canonical" }`. `AssetTarget.mode` type widened to `"canonical" | "reference"`.

- [ ] **Step 1: Write the failing tests.** Create `app/src/__tests__/storage-reference.test.ts`:

```ts
// @vitest-environment node
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { query } from "@/lib/db/connection";
import { lookupReferenceHref } from "@/lib/storage/reference";

const mockQuery = vi.mocked(query);
beforeEach(() => mockQuery.mockReset());

describe("lookupReferenceHref", () => {
  it("returns the source_href when a referenced row matches", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [{ source_href: "http://src/scene.tif" }] } as never);
    const href = await lookupReferenceHref("col", "scene", "scene.tif");
    expect(href).toBe("http://src/scene.tif");
  });

  it("returns null when no referenced row matches", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [] } as never);
    const href = await lookupReferenceHref("col", "scene", "scene.tif");
    expect(href).toBeNull();
  });
});
```

Also create `app/src/__tests__/storage-resolve.test.ts` for the branch:

```ts
// @vitest-environment node
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/storage/reference", () => ({ lookupReferenceHref: vi.fn() }));
vi.mock("@/lib/storage/presign", () => ({ presignGetUrl: vi.fn(async () => "http://canonical/presigned") }));

import { lookupReferenceHref } from "@/lib/storage/reference";
import { presignGetUrl } from "@/lib/storage/presign";
import { resolveAssetTarget } from "@/lib/storage/resolve";

const mockLookup = vi.mocked(lookupReferenceHref);
beforeEach(() => { mockLookup.mockReset(); vi.mocked(presignGetUrl).mockClear(); });

describe("resolveAssetTarget", () => {
  it("302 target is the source href in reference mode", async () => {
    mockLookup.mockResolvedValueOnce("http://src/scene.tif");
    const t = await resolveAssetTarget("col", "scene", "scene.tif");
    expect(t).toEqual({ url: "http://src/scene.tif", mode: "reference" });
    expect(presignGetUrl).not.toHaveBeenCalled();
  });

  it("falls back to presigned canonical when not referenced", async () => {
    mockLookup.mockResolvedValueOnce(null);
    const t = await resolveAssetTarget("col", "scene", "scene.tif");
    expect(t.mode).toBe("canonical");
    expect(t.url).toBe("http://canonical/presigned");
  });
});
```

- [ ] **Step 2: Run to verify they fail.**

Run: `cd app && npx vitest run storage-reference storage-resolve`
Expected: FAIL — `reference.ts` does not exist; `resolveAssetTarget` has no reference branch.

- [ ] **Step 3: Create `app/src/lib/storage/reference.ts`.**

```ts
/**
 * Reference-mode asset lookup (ROADMAP Phase 4 Slice C).
 *
 * `storage_mode: reference` associations catalog items whose bytes stay at the
 * source; the pipeline records the stable source URL in `ingest_files.source_href`
 * at FETCH. This resolves that URL for (collection, item, filename) so the asset
 * route can 302 to the source instead of presigning a canonical object. Returns
 * null for copy-mode / manually-uploaded assets (no referenced row) — the caller
 * then presigns canonical storage.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";

export async function lookupReferenceHref(
  collection: string,
  itemId: string,
  filename: string,
): Promise<string | null> {
  await runMigrations();
  const result = await query<{ source_href: string }>(
    `SELECT f.source_href
       FROM stac_higher.ingest_files f
       JOIN stac_higher.collection_connections cc ON cc.id = f.association_id
      WHERE cc.collection_id = $1
        AND f.item_id = $2
        AND f.source_href IS NOT NULL
        AND regexp_replace(f.source_path, '^.*/', '') = $3
      ORDER BY f.version DESC
      LIMIT 1`,
    [collection, itemId, filename],
  );
  return result.rows[0]?.source_href ?? null;
}
```

- [ ] **Step 4: Add the branch to `resolve.ts`.** Update `app/src/lib/storage/resolve.ts`:

```ts
import { canonicalAssetKey } from "./keys";
import { presignGetUrl } from "./presign";
import { lookupReferenceHref } from "./reference";

export interface AssetTarget {
  url: string;
  mode: "canonical" | "reference";
}

export async function resolveAssetTarget(
  collection: string,
  itemId: string,
  filename: string,
): Promise<AssetTarget> {
  const referenceHref = await lookupReferenceHref(collection, itemId, filename);
  if (referenceHref) {
    return { url: referenceHref, mode: "reference" };
  }
  const key = canonicalAssetKey(collection, itemId, filename);
  const url = await presignGetUrl(key);
  return { url, mode: "canonical" };
}
```
Also update the module docstring's "canonical now; reference lands in Phase 4" note to reflect that reference now ships.

- [ ] **Step 5: Run to verify they pass.**

Run: `cd app && npx vitest run storage-reference storage-resolve`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add app/src/lib/storage/reference.ts app/src/lib/storage/resolve.ts app/src/__tests__/storage-reference.test.ts app/src/__tests__/storage-resolve.test.ts
git commit -m "feat(storage): resolveAssetTarget reference branch (302 to source_href)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Full verify + documentation

**Files:**
- Modify: `docs/FEATURES.md` (Slice C row → done; Phase 4 status line), `docs/ISSUES.md` (close the reference-stall note; add follow-ups: private-source resolver, reference checksum, browser-reachable source endpoint), `ROADMAP.md` (Slice C bullet ✅; Phase 4 status), `docs/decisions/0005-asset-service.md` ("Revisit" → note reference shipped), the auto-memory `phase-progress.md`.

**Interfaces:** none (docs + gate).

- [ ] **Step 1: Run the full app verify.**

Run: `npm run verify` (repo root)
Expected: PASS (build + all vitest).

- [ ] **Step 2: Run the full pipeline suite + lint.**

Run: `cd services/pipeline && uv run pytest -q && uv run ruff check`
Expected: PASS + "All checks passed!". Fix any ruff findings (line length, imports) before continuing.

- [ ] **Step 3: Update `docs/FEATURES.md`.** Change the `storage_mode: reference` row (line ~104) from ⬜ to ✅ with the entry point (`resolveAssetTarget` + `reference.ts`; pipeline FETCH reference branch + byte-source seam; migration 006; the destructive-`post_ingest` guard). Update the Phase 4 status paragraph (line ~92) to note Slice C done.

- [ ] **Step 4: Update `docs/ISSUES.md`.** Replace the I-? "reference associations stall at settled" note (line ~109) with a done note pointing at the slice. Add follow-up issues: (a) private-source reference via a pipeline resolver endpoint (fresh presigned URLs per read); (b) reference-mode checksum deferred (fingerprint drives versioning); (c) reference source URL uses the connection's configured endpoint — a split internal/browser endpoint is the same class as I-15.

- [ ] **Step 5: Update `ROADMAP.md`.** Flip the Slice C bullet (line ~768) to ✅ with a one-line summary; update the Phase 4 status row (line ~578) to reflect Slice C done and only the live SFTP/FTP + continuous-run verification (Task 10) remaining, then done-when met.

- [ ] **Step 6: Update ADR 0005 "Revisit".** Note that `storage_mode: reference` shipped as a durably-reachable-source-only branch in `resolveAssetTarget` (no app decryption), with private-source reference deferred.

- [ ] **Step 7: Update the auto-memory.** In `/Users/caesterlein/.claude/projects/-Users-caesterlein-Projects-ogc-maps-stac-higher/memory/phase-progress.md`, record Slice C done + the durably-reachable-only decision and its follow-ups.

- [ ] **Step 8: Commit.**

```bash
git add docs/ ROADMAP.md
git commit -m "docs(phase4): Slice C reference mode done; log follow-ups

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Live verification against the docker stack

**Files:** none (manual/scripted verification; capture findings in `docs/ISSUES.md` if anything surfaces).

**Preconditions:** `docker compose up -d --wait` (repo root) — pgstac :8082, stac-auth-proxy :8081, MinIO :9000/:9001, pipeline :8083. App dev server for the asset route (`cd app && npm run dev`, :4321) or exercise `resolveAssetTarget` via a small node script. Reference associations are s3-only, so the source is a MinIO bucket configured path-style. Read the `run-e2e` skill for backend gotchas before starting.

- [ ] **Step 1: Continuous scheduler-driven REFERENCE run.**
  1. Create/seed a **public/path-style** source bucket in MinIO (distinct from the canonical `stac-higher` bucket) and drop a georeferenced GeoTIFF at a known key.
  2. Create an s3 connection pointing at that source bucket (encrypted creds), then a **reference** ingest association on a built-in-catalog collection (`storage_mode: "reference"`, `post_ingest: "leave"`, `metadata.strategy: "raster_auto"`) via `POST /api/collections/[id]/connections`.
  3. Let the scheduler poll (1-min cron): observe the ledger go `seen → settled` (two polls) → GROUP → reference-FETCH (`stored` + `source_href`, **no** object under `assets/...` in the canonical bucket) → EXTRACT/ITEMIZE.
  4. Confirm the item is queryable in pgstac with a non-null geometry and asset href `/api/assets/{collection}/{item}/{filename}`.
  5. `GET /api/assets/{collection}/{item}/{filename}` (authenticated) → assert **302** with `Location` = the source URL, and that following it downloads the source bytes.
  Expected: the whole chain completes within a couple of poll cycles; no bytes copied into canonical storage.

- [ ] **Step 2: Continuous scheduler-driven COPY run over SFTP/FTP (ISSUE I-4).**
  1. Bring up (or point at) an SFTP or FTP source with a georeferenced file; create the connection (test-connection pins the host key for SFTP).
  2. Create a **copy**-mode ingest association; let the scheduler run.
  3. Observe poll → DISCOVER → GROUP → FETCH (bytes copied into `assets/...`) → ITEMIZE; confirm the queryable item + `GET /api/assets/...` 302 → presigned canonical → bytes download.
  Expected: closes the last non-s3 adapter gap for copy mode; I-4 resolved.

- [ ] **Step 3: Record results.** Note both runs (and any findings) in `docs/ISSUES.md` / the memory file; if a run reveals a defect, file it and fix before declaring Slice C done.

- [ ] **Step 4: Merge to `ai/main`.** With `npm run verify` and the pipeline suite green and both live runs passing:

```bash
git checkout ai/main && git merge ai/slice-c --no-ff
cd services/pipeline && uv run pytest -q   # re-verify on ai/main
git worktree remove .claude/worktrees/slice-c && git branch -d ai/slice-c
```

---

## Self-Review

**Spec coverage:**
- §2 decision (durably-reachable, no presign/decrypt) → Tasks 4, 5, 8 (public URL construction, no-copy FETCH, plain-string 302). ✓
- §3 `source_href` column → Task 1 (app DDL) + Task 3 (pipeline plumbing). ✓
- §4.1 stable URL → Task 4. §4.2 FETCH reference → Task 5. §4.3 byte-source seam → Task 6. §4.4 post_ingest guard → Task 2 (app) + Task 7 (pipeline). ✓
- §5.1 resolve branch → Task 8. §5.2 migration/route → Task 1 (route untouched, as specified). §5.3 schema guard → Task 2. ✓
- §7 testing → each task is TDD. §8 live verification → Task 10. §9 follow-ups → Task 9 Step 4. ✓

**Placeholder scan:** every code step shows real code; test bodies are concrete. The two spots that say "mirror the existing test's setup" (Task 5 association construction, Task 6 Step 7 itemize round-trip) point at a specific existing test to copy — acceptable because the exact fixture-builder differs per module and must be read in place; no logic is left unspecified.

**Type consistency:** `MemberByteSource.read(member) -> bytes`, `CanonicalByteSource(s3_client, bucket)`, `SourceAdapterByteSource(adapter, source_path)`, `build_item(..., byte_source=...)`, `lookupReferenceHref(collection, itemId, filename) -> string | null`, `AssetTarget.mode: "canonical" | "reference"`, `public_object_url(path) -> str`, `LedgerEntry.source_href: str | None` — used consistently across Tasks 3–8. ✓
