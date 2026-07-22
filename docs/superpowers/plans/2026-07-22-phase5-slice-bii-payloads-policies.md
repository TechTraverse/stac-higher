# Phase 5 Slice B-ii Implementation Plan — delivery payloads, on_update/overwrite, reference source, S3→S3 copy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the delivery worker honor `payload` sidecars, `on_update`, and `overwrite` from the §5.1 delivery config, and extend the byte path to reference-mode sources and S3→S3 server-side copy.

**Architecture:** Per-asset delivered fingerprints land in a new `delivery_log.delivered_assets` jsonb column (app migration 009); the worker gates writes on them (log-based `overwrite`, item-level `on_update`), resolves reference assets ledger-first through the ingest source adapter, server-side copies when the s3 destination shares the platform endpoint, and writes sidecars (checksums per file, item JSON every event, completion marker last). Spec: `docs/superpowers/specs/2026-07-22-phase5-slice-bii-payloads-policies-design.md` — read it first.

**Tech Stack:** Python 3.12 (pipeline: psycopg, boto3, pytest-asyncio via `uv`), TypeScript (app migration in `migrate.ts`).

## Global Constraints

- Work in the existing worktree `.claude/worktrees/deliver-b2`, branch `ai/deliver-b2` (off `ai/main`). All paths below are worktree-relative.
- Pipeline tests: `cd services/pipeline && uv run pytest` (no database needed). App gate: `npm run verify` at the repo root (must pass before declaring the slice done).
- **No new dependencies** (no HTTP client — reference reads go through the source adapter).
- ADR 0001 ownership: the app owns all DDL; the pipeline never runs DDL. `delivery_log` writes stay in `PgDeliveryRepo`.
- Cross-runtime contract: field names/defaults in `services/pipeline/src/pipeline/delivery/config.py` must not drift from `app/src/lib/associations/schemas.ts` (`payload.item_json` / `payload.checksums` / `payload.completion_marker`, `on_update`, `overwrite`). This slice changes NEITHER schema.
- `PgDeliveryRepo` network methods stay `# pragma: no cover` (exercised by live verification); unit tests target the fakes + worker.
- No e2e, no dev server, no Docker during task execution (lead-only live verification is Task 11).
- Conventional commits, one per task, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Migration 009 — `delivered_assets` column

**Files:**
- Modify: `app/src/lib/db/migrate.ts` (append to the `migrations` array, directly after the `008_delivery_log` entry that ends near line 375)

**Interfaces:**
- Produces: `stac_higher.delivery_log.delivered_assets jsonb NOT NULL DEFAULT '{}'` — shape `{asset_key: {"fingerprint": str, "size": int, "filename": str}}`, read/written by Task 2's repo methods.

- [ ] **Step 1: Add the migration entry**

Insert after the closing `},` of the `008_delivery_log` object:

```ts
  {
    // Phase 5 Slice B-ii (ROADMAP §6.4): per-asset delivered fingerprints — the
    // change-detection substrate for on_update (redeliver only changed assets)
    // and log-based overwrite. Shape: {asset_key: {fingerprint, size, filename}}.
    // fingerprint is "sha256:<hex>" (streamed) or "etag:<etag>/<size>"
    // (server-side copy); kinds compare unequal → worst case one redundant
    // redeliver (at-least-once, ISSUES I-43).
    name: "009_delivery_log_delivered_assets",
    sql: `
      ALTER TABLE stac_higher.delivery_log
        ADD COLUMN IF NOT EXISTS delivered_assets jsonb NOT NULL DEFAULT '{}'::jsonb;
    `,
  },
```

- [ ] **Step 2: Confirm the PostToolUse astro check hook reports no errors**

The Edit hook runs `npx astro check --minimumSeverity error` in `app/` automatically. Expected: no errors (this is data-only TS).

- [ ] **Step 3: Commit**

```bash
git add app/src/lib/db/migrate.ts
git commit -m "feat(phase5): migration 009 — delivery_log.delivered_assets fingerprint map (B-ii)"
```

---

### Task 2: Delivery repo — prior-row read, delivered_assets persistence, attempts reset, reference sources

**Files:**
- Modify: `services/pipeline/src/pipeline/delivery/repo.py`
- Modify: `services/pipeline/tests/_delivery_fake.py`
- Test: `services/pipeline/tests/test_delivery_repo_fake.py`

**Interfaces:**
- Consumes: migration 009 column (Task 1); `ConnectionRow` + `_to_connection_row` from `pipeline.connections.repo`; `source_fetch_path` from `pipeline.ingest.discover`.
- Produces (worker Tasks 5–7 depend on these exact names):
  - `@dataclass DeliveryRow: id: str; status: str; attempts: int; delivered_assets: dict[str, Any]`
  - `@dataclass ReferenceSource: filename: str; fetch_path: str; connection: ConnectionRow`
  - `DeliveryRepo.get_row(association_id: str, item_id: str) -> DeliveryRow | None`
  - `DeliveryRepo.load_reference_sources(item_id: str) -> list[ReferenceSource]`
  - `DeliveryRepo.mark_delivered(row_id: str, byte_count: int, delivered_assets: dict[str, Any] | None = None) -> None` (extended; default keeps Task-5-precursor callers compiling)
  - `upsert_pending` now resets `attempts = 0` on the conflict branch (**resolves I-44**).

- [ ] **Step 1: Write failing fake-repo tests**

Append to `services/pipeline/tests/test_delivery_repo_fake.py`:

```python
async def test_upsert_pending_resets_attempts_on_redelivery():
    repo = FakeDeliveryRepo()
    rid = await repo.upsert_pending("a1", "scene", None)
    await repo.mark_delivering(rid)
    await repo.mark_delivered(rid, 3, {"data": {"fingerprint": "sha256:x", "size": 3, "filename": "a.tif"}})
    # A new event for the same (association, item) starts a fresh attempt cycle.
    rid2 = await repo.upsert_pending("a1", "scene", None)
    assert rid2 == rid
    assert repo.rows[rid]["attempts"] == 0
    assert repo.rows[rid]["status"] == "pending"


async def test_get_row_returns_prior_state():
    repo = FakeDeliveryRepo()
    assert await repo.get_row("a1", "scene") is None
    rid = await repo.upsert_pending("a1", "scene", None)
    await repo.mark_delivering(rid)
    delivered = {"data": {"fingerprint": "sha256:abc", "size": 7, "filename": "a.tif"}}
    await repo.mark_delivered(rid, 7, delivered)
    row = await repo.get_row("a1", "scene")
    assert row is not None
    assert row.status == "delivered"
    assert row.delivered_assets == delivered


async def test_load_reference_sources_filters_by_item():
    from pipeline.delivery.repo import ReferenceSource

    src = ReferenceSource(filename="a.tif", fetch_path="incoming/a.tif", connection=None)
    repo = FakeDeliveryRepo(reference_sources={"scene": [src]})
    assert await repo.load_reference_sources("scene") == [src]
    assert await repo.load_reference_sources("other") == []
```

(If the file lacks a `FakeDeliveryRepo` import or `pytestmark = pytest.mark.asyncio`, mirror the header of `test_delivery_worker.py`.)

- [ ] **Step 2: Run to verify failure**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_repo_fake.py -v`
Expected: FAIL — `get_row`/`ReferenceSource` undefined, `mark_delivered` arity.

- [ ] **Step 3: Implement in `delivery/repo.py`**

Add after the `DeliverTarget` dataclass (import `source_fetch_path` at top: `from pipeline.ingest.discover import source_fetch_path`):

```python
@dataclass
class DeliveryRow:
    """Prior delivery_log state for one (association, item) — the substrate for
    the on_update gate and the log-based overwrite gate (spec decisions 1–2)."""

    id: str
    status: str
    attempts: int
    delivered_assets: dict[str, Any]


@dataclass
class ReferenceSource:
    """A reference-mode source file for an item: read in place from the ingest
    source connection's adapter (spec decision 3 — the pipeline has no HTTP
    client; ``source_href`` presence flags reference mode)."""

    filename: str
    fetch_path: str
    connection: ConnectionRow
```

Add abstract methods to `DeliveryRepo`:

```python
    @abc.abstractmethod
    async def get_row(self, association_id: str, item_id: str) -> DeliveryRow | None:
        """The existing delivery_log row (status + delivered_assets), or None on
        first delivery. Read BEFORE upsert_pending, which resets status."""

    @abc.abstractmethod
    async def load_reference_sources(self, item_id: str) -> list[ReferenceSource]:
        """Latest-version ingest_files rows for this item with a source_href —
        the item's reference-mode files, with their source connection loaded."""
```

Change `mark_delivered`'s abstract signature to:

```python
    @abc.abstractmethod
    async def mark_delivered(
        self,
        row_id: str,
        byte_count: int,
        delivered_assets: dict[str, Any] | None = None,
    ) -> None:
        """Flip to delivered; record bytes + delivered_at + the per-asset
        fingerprint map; clear error."""
```

`PgDeliveryRepo` (all `# pragma: no cover`, live-verified):

```python
    async def get_row(  # pragma: no cover
        self, association_id: str, item_id: str
    ) -> DeliveryRow | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, status, attempts, delivered_assets"
                " FROM stac_higher.delivery_log"
                " WHERE association_id = %s AND item_id = %s",
                (association_id, item_id),
            )
            row = await cur.fetchone()
        if not row:
            return None
        return DeliveryRow(
            id=str(row[0]),
            status=row[1],
            attempts=row[2],
            delivered_assets=dict(row[3]) if row[3] else {},
        )

    async def load_reference_sources(  # pragma: no cover
        self, item_id: str
    ) -> list[ReferenceSource]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (f.association_id, f.source_path)"
                " f.source_path, cc.config,"
                f" {_CONNECTION_COLUMNS}"
                " FROM stac_higher.ingest_files f"
                " JOIN stac_higher.collection_connections cc ON cc.id = f.association_id"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE f.item_id = %s AND f.source_href IS NOT NULL"
                " ORDER BY f.association_id, f.source_path, f.version DESC",
                (item_id,),
            )
            rows = await cur.fetchall()
        sources: list[ReferenceSource] = []
        for row in rows:
            source_path, ingest_config = row[0], dict(row[1]) if row[1] else {}
            sources.append(
                ReferenceSource(
                    filename=source_path.rsplit("/", 1)[-1],
                    fetch_path=source_fetch_path(
                        ingest_config.get("source_path", ""), source_path
                    ),
                    connection=_to_connection_row(row[2:]),
                )
            )
        return sources
```

In `upsert_pending`'s SQL, extend the conflict branch (I-44: a redelivery event starts a new attempt cycle; the B-iii retry sweep never passes through here):

```sql
 ON CONFLICT (association_id, item_id) DO UPDATE
 SET status = 'pending',
     attempts = 0,
     item_created_at = EXCLUDED.item_created_at,
     updated_at = now()
```

And `mark_delivered` (import `json` at top of the file):

```python
    async def mark_delivered(  # pragma: no cover
        self,
        row_id: str,
        byte_count: int,
        delivered_assets: dict[str, Any] | None = None,
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = 'delivered', bytes = %s, error = NULL,"
                "     delivered_assets = %s::jsonb,"
                "     delivered_at = now(), updated_at = now()"
                " WHERE id = %s",
                (byte_count, json.dumps(delivered_assets or {}), row_id),
            )
            await conn.commit()
```

- [ ] **Step 4: Update `tests/_delivery_fake.py` to match**

```python
"""In-memory DeliveryRepo for worker + deliver-job unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline.delivery.repo import (
    DeliverTarget,
    DeliveryRepo,
    DeliveryRow,
    ReferenceSource,
)


@dataclass
class FakeDeliveryRepo(DeliveryRepo):
    targets: dict[str, DeliverTarget] = field(default_factory=dict)
    items: dict[tuple[str, str], dict] = field(default_factory=dict)
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    reference_sources: dict[str, list[ReferenceSource]] = field(default_factory=dict)
    _seq: int = 0

    async def load_target(self, association_id: str) -> DeliverTarget | None:
        return self.targets.get(association_id)

    async def get_item(self, collection_id: str, item_id: str) -> dict | None:
        return self.items.get((collection_id, item_id))

    async def get_row(self, association_id: str, item_id: str) -> DeliveryRow | None:
        for rid, rec in self.rows.items():
            if (rec["association_id"], rec["item_id"]) == (association_id, item_id):
                return DeliveryRow(
                    id=rid,
                    status=rec["status"],
                    attempts=rec["attempts"],
                    delivered_assets=dict(rec.get("delivered_assets") or {}),
                )
        return None

    async def load_reference_sources(self, item_id: str) -> list[ReferenceSource]:
        return list(self.reference_sources.get(item_id, []))

    async def upsert_pending(
        self, association_id: str, item_id: str, item_created_at: str | None
    ) -> str:
        for rid, rec in self.rows.items():
            if (rec["association_id"], rec["item_id"]) == (association_id, item_id):
                # I-44: a redelivery event starts a fresh attempt cycle.
                rec.update(status="pending", attempts=0, item_created_at=item_created_at)
                return rid
        self._seq += 1
        rid = f"row{self._seq}"
        self.rows[rid] = {
            "association_id": association_id,
            "item_id": item_id,
            "item_created_at": item_created_at,
            "status": "pending",
            "attempts": 0,
            "bytes": None,
            "error": None,
            "delivered_assets": {},
        }
        return rid

    async def mark_delivering(self, row_id: str) -> None:
        rec = self.rows[row_id]
        rec["status"] = "delivering"
        rec["attempts"] += 1

    async def mark_delivered(
        self,
        row_id: str,
        byte_count: int,
        delivered_assets: dict[str, Any] | None = None,
    ) -> None:
        rec = self.rows[row_id]
        rec.update(
            status="delivered",
            bytes=byte_count,
            error=None,
            delivered_assets=dict(delivered_assets or {}),
        )

    async def mark_failed(self, row_id: str, error: str) -> None:
        rec = self.rows[row_id]
        rec.update(status="failed", error=error)
```

- [ ] **Step 5: Run the full pipeline suite**

Run: `cd services/pipeline && uv run pytest`
Expected: PASS (the `mark_delivered` default keeps the current worker call compiling; new tests pass).

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/delivery/repo.py services/pipeline/tests/_delivery_fake.py services/pipeline/tests/test_delivery_repo_fake.py
git commit -m "feat(phase5): delivery repo — prior-row read, delivered_assets, attempts reset (I-44), reference sources (B-ii)"
```

---

### Task 3: Transfer helpers — fingerprints, server-side-copy gate, `head_object`, `copy_object_from`

**Files:**
- Create: `services/pipeline/src/pipeline/delivery/transfer.py`
- Modify: `services/pipeline/src/pipeline/storage/platform.py`
- Modify: `services/pipeline/src/pipeline/connections/adapters/s3.py`
- Test: `services/pipeline/tests/test_delivery_transfer.py` (new), `services/pipeline/tests/test_platform_get.py`, `services/pipeline/tests/test_adapter_move.py`

**Interfaces:**
- Produces (Tasks 7–8 depend on):
  - `transfer.sha256_fingerprint(data: bytes) -> str` → `"sha256:<hex>"`
  - `transfer.etag_fingerprint(etag: str, size: int) -> str` → `"etag:<etag>/<size>"`
  - `transfer.can_server_side_copy(protocol: str, connection_endpoint: str | None, platform_endpoint: str | None) -> bool`
  - `platform.head_object(client: S3Like, bucket: str, key: str) -> tuple[str, int]` (quote-stripped ETag, size)
  - `S3Adapter.copy_object_from(src_bucket: str, src_key: str, dst_path: str) -> None`

- [ ] **Step 1: Write failing tests**

Create `services/pipeline/tests/test_delivery_transfer.py`:

```python
import hashlib

from pipeline.delivery.transfer import (
    can_server_side_copy,
    etag_fingerprint,
    sha256_fingerprint,
)


def test_sha256_fingerprint_format():
    data = b"bytes"
    assert sha256_fingerprint(data) == f"sha256:{hashlib.sha256(data).hexdigest()}"


def test_etag_fingerprint_format():
    assert etag_fingerprint("abc123", 42) == "etag:abc123/42"


def test_copy_gate_requires_s3():
    assert not can_server_side_copy("sftp", "http://minio:9000", "http://minio:9000")


def test_copy_gate_same_custom_endpoint():
    assert can_server_side_copy("s3", "http://minio:9000", "http://minio:9000")


def test_copy_gate_different_host():
    assert not can_server_side_copy("s3", "http://other:9000", "http://minio:9000")


def test_copy_gate_default_port_equivalence():
    assert can_server_side_copy("s3", "http://minio:80", "http://minio")
    assert not can_server_side_copy("s3", "http://minio:9000", "http://minio")


def test_copy_gate_both_aws():
    # No custom endpoint on either side: both are real AWS S3, where
    # CopyObject spans buckets.
    assert can_server_side_copy("s3", None, None)


def test_copy_gate_mixed_none():
    assert not can_server_side_copy("s3", None, "http://minio:9000")
    assert not can_server_side_copy("s3", "http://minio:9000", None)


def test_copy_gate_host_case_insensitive():
    assert can_server_side_copy("s3", "http://MinIO:9000", "http://minio:9000")
```

Append to `services/pipeline/tests/test_platform_get.py` (mirror its existing fake-client style):

```python
def test_head_object_returns_stripped_etag_and_size():
    from pipeline.storage.platform import head_object

    class _Client:
        def head_object(self, Bucket, Key):  # noqa: N803 - boto3 kwarg names
            assert (Bucket, Key) == ("bucket", "assets/col/scene/a.tif")
            return {"ETag": '"d41d8cd98f00b204e9800998ecf8427e"', "ContentLength": 9}

    etag, size = head_object(_Client(), "bucket", "assets/col/scene/a.tif")
    assert etag == "d41d8cd98f00b204e9800998ecf8427e"
    assert size == 9
```

Append to `services/pipeline/tests/test_adapter_move.py`:

```python
async def test_s3_copy_object_from_issues_server_side_copy(monkeypatch):
    from pipeline.connections.adapters.s3 import S3Adapter

    a = S3Adapter({"bucket": "dest"}, {"access_key_id": "k", "secret_access_key": "s"})
    calls: list[dict] = []

    class _Client:
        def copy_object(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(a, "_pinned_endpoint", lambda: None)
    monkeypatch.setattr(a, "_make_client", lambda endpoint_url: _Client())
    await a.copy_object_from("platform", "assets/col/scene/a.tif", "col/a.tif")
    assert calls == [
        {
            "Bucket": "dest",
            "Key": "col/a.tif",
            "CopySource": {"Bucket": "platform", "Key": "assets/col/scene/a.tif"},
        }
    ]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_transfer.py tests/test_platform_get.py tests/test_adapter_move.py -v`
Expected: FAIL — module/functions not defined.

- [ ] **Step 3: Implement**

Create `services/pipeline/src/pipeline/delivery/transfer.py`:

```python
"""Delivery transfer-path helpers (ROADMAP §6.4, Slice B-ii).

Pure and I/O-free: the fingerprint formats stored in
``delivery_log.delivered_assets`` and the S3→S3 server-side-copy gate.
Fingerprints of different kinds (streamed sha256 vs copy-path etag)
intentionally compare unequal — worst case one redundant redeliver,
consistent with at-least-once delivery (ISSUES I-43).
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse

_DEFAULT_PORTS = {"http": 80, "https": 443}


def sha256_fingerprint(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def etag_fingerprint(etag: str, size: int) -> str:
    return f"etag:{etag}/{size}"


def _normalized(endpoint: str) -> tuple[str, str, int | None]:
    parsed = urlparse(endpoint)
    scheme = parsed.scheme or "https"
    return scheme, (parsed.hostname or "").lower(), parsed.port or _DEFAULT_PORTS.get(scheme)


def can_server_side_copy(
    protocol: str,
    connection_endpoint: str | None,
    platform_endpoint: str | None,
) -> bool:
    """True when the destination can CopyObject straight from the canonical
    bucket: an s3 connection on the platform's own endpoint (spec decision 4),
    or both sides on real AWS (no custom endpoint — CopyObject spans buckets
    there). The worker still falls back to streaming if the copy itself fails
    (e.g. the destination credentials cannot read the canonical bucket).
    """
    if protocol != "s3":
        return False
    if connection_endpoint is None and platform_endpoint is None:
        return True
    if connection_endpoint is None or platform_endpoint is None:
        return False
    return _normalized(connection_endpoint) == _normalized(platform_endpoint)
```

In `services/pipeline/src/pipeline/storage/platform.py`, add to the `S3Like` protocol:

```python
    def head_object(self, **kwargs: Any) -> Any: ...
```

and add after `get_object`:

```python
def head_object(client: S3Like, bucket: str, key: str) -> tuple[str, int]:
    """Quote-stripped ETag + size of the object at ``key`` — the server-side
    copy path's fingerprint source (delivery B-ii; no byte read). Pure over an
    injected client; synchronous boto3 — wrap in ``asyncio.to_thread``."""
    resp = client.head_object(Bucket=bucket, Key=key)
    return (resp.get("ETag") or "").strip('"'), int(resp["ContentLength"])
```

In `services/pipeline/src/pipeline/connections/adapters/s3.py`, add after `move`:

```python
    async def copy_object_from(self, src_bucket: str, src_key: str, dst_path: str) -> None:
        """Server-side CopyObject from another bucket on the SAME endpoint into
        this adapter's bucket (delivery §6.4 — no bytes through the worker).
        Callers gate on ``delivery.transfer.can_server_side_copy`` and fall
        back to streaming when the copy fails (e.g. these credentials cannot
        read ``src_bucket``)."""
        endpoint_url = self._pinned_endpoint()

        def _copy() -> None:
            client = self._make_client(endpoint_url)
            client.copy_object(
                Bucket=self._bucket,
                Key=dst_path,
                CopySource={"Bucket": src_bucket, "Key": src_key},
            )

        await asyncio.to_thread(_copy)
```

- [ ] **Step 4: Run the full suite**

Run: `cd services/pipeline && uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/delivery/transfer.py services/pipeline/src/pipeline/storage/platform.py services/pipeline/src/pipeline/connections/adapters/s3.py services/pipeline/tests/test_delivery_transfer.py services/pipeline/tests/test_platform_get.py services/pipeline/tests/test_adapter_move.py
git commit -m "feat(phase5): transfer helpers — fingerprints, copy gate, head_object, copy_object_from (B-ii)"
```

---

### Task 4: Payload sidecar builders

**Files:**
- Create: `services/pipeline/src/pipeline/delivery/payload.py`
- Test: `services/pipeline/tests/test_delivery_payload.py` (new)

**Interfaces:**
- Produces (Task 5 depends on): `item_json_payload(item: dict) -> tuple[str, bytes]`, `checksum_payload(filename: str, algo: str, digest_hex: str) -> tuple[str, bytes]`, `completion_payload(item_id: str, delivered_assets: dict[str, dict]) -> tuple[str, bytes]` — each returns `(sidecar_filename, body_bytes)`; `delivered_assets` is the Task-2 shape.

- [ ] **Step 1: Write failing tests**

Create `services/pipeline/tests/test_delivery_payload.py`:

```python
import json

from pipeline.delivery.payload import (
    checksum_payload,
    completion_payload,
    item_json_payload,
)


def test_item_json_payload_named_by_item_id_and_verbatim():
    item = {"id": "scene", "collection": "col", "assets": {"a": {"href": "/api/assets/col/scene/a.tif"}}}
    name, body = item_json_payload(item)
    assert name == "scene.json"
    assert json.loads(body) == item
    assert body.endswith(b"\n")


def test_checksum_payload_coreutils_format():
    name, body = checksum_payload("a.tif", "sha256", "deadbeef")
    assert name == "a.tif.sha256"
    # `sha256sum -c`-compatible: two spaces between digest and filename.
    assert body == b"deadbeef  a.tif\n"


def test_completion_payload_lists_assets_sorted_by_key():
    delivered = {
        "b": {"fingerprint": "sha256:2", "size": 2, "filename": "b.tif"},
        "a": {"fingerprint": "sha256:1", "size": 1, "filename": "a.tif"},
    }
    name, body = completion_payload("scene", delivered)
    assert name == "scene.done"
    manifest = json.loads(body)
    assert manifest["item_id"] == "scene"
    assert [e["key"] for e in manifest["assets"]] == ["a", "b"]
    assert manifest["assets"][0] == {
        "key": "a", "filename": "a.tif", "fingerprint": "sha256:1", "size": 1,
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_payload.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `delivery/payload.py`**

```python
"""Payload sidecar builders (ROADMAP §5.1/§6.4, Slice B-ii).

Pure and I/O-free: each returns ``(sidecar_filename, body_bytes)``; the worker
renders the filename through the association's ``path_template`` (so sidecars
land beside the assets) and writes via the adapter. The completion marker is
written LAST (§6.4) — its appearance is the "product is complete" signal for
directory-watching consumers.
"""

from __future__ import annotations

import json
from typing import Any


def item_json_payload(item: dict[str, Any]) -> tuple[str, bytes]:
    """``{item_id}.json`` — the pgstac item verbatim. Asset hrefs stay
    ``/api/assets/...`` (resolvable through the asset service; no href
    rewriting in this slice)."""
    return f"{item['id']}.json", (json.dumps(item, indent=2) + "\n").encode()


def checksum_payload(filename: str, algo: str, digest_hex: str) -> tuple[str, bytes]:
    """``{filename}.{algo}`` in coreutils format — ``sha256sum -c`` /
    ``md5sum -c`` verifiable."""
    return f"{filename}.{algo}", f"{digest_hex}  {filename}\n".encode()


def completion_payload(
    item_id: str, delivered_assets: dict[str, dict[str, Any]]
) -> tuple[str, bytes]:
    """``{item_id}.done`` — JSON manifest of every delivered-or-current asset
    (skipped-but-current assets keep their prior fingerprint entries)."""
    entries = [
        {
            "key": key,
            "filename": delivered_assets[key].get("filename"),
            "fingerprint": delivered_assets[key].get("fingerprint"),
            "size": delivered_assets[key].get("size"),
        }
        for key in sorted(delivered_assets)
    ]
    body = json.dumps({"item_id": item_id, "assets": entries}, indent=2) + "\n"
    return f"{item_id}.done", body.encode()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_payload.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/delivery/payload.py services/pipeline/tests/test_delivery_payload.py
git commit -m "feat(phase5): payload sidecar builders — item JSON, checksums, completion marker (B-ii)"
```

---

### Task 5: Worker — on_update/overwrite gates, delivered_assets, payload writing (streaming paths)

**Files:**
- Modify: `services/pipeline/src/pipeline/delivery/worker.py` (full rewrite below)
- Test: `services/pipeline/tests/test_delivery_worker.py`

**Interfaces:**
- Consumes: Task 2 repo methods, Task 3 `sha256_fingerprint`, Task 4 payload builders.
- Produces: `deliver_item(repo, adapter, s3_client, bucket, *, target, config, item, asset_keys, item_created_at, build_source_adapter: SourceAdapterFactory | None = None, server_side_copy: bool = False)` — the two new kwargs are inert in this task (wired in Tasks 6–8); `SourceAdapterFactory = Callable[[ConnectionRow], StorageAdapter]` exported from `worker.py`.

- [ ] **Step 1: Write failing worker tests**

In `services/pipeline/tests/test_delivery_worker.py`, append the following helpers and tests (`_FakeAdapter.puts` already records writes in order — assert ordering through it):

```python
def _config(**overrides):
    base = {"path_template": "{filename}"}
    base.update(overrides)
    return parse_delivery_config(base)


async def _run(repo, adapter, s3, item, config, asset_keys=("data",)):
    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=list(asset_keys), item_created_at=None,
    )


async def test_on_update_ignore_skips_after_delivered():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"V1"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(on_update="ignore")

    await _run(repo, adapter, s3, item, config)
    assert len(adapter.puts) == 1
    # A second event for the already-delivered item is consumed without writes.
    await _run(repo, adapter, s3, item, config)
    assert len(adapter.puts) == 1
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"
    assert rec["attempts"] == 1  # untouched by the ignored event


async def test_on_update_ignore_still_delivers_after_failure():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({})  # canonical object missing -> first attempt fails
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(on_update="ignore")

    await _run(repo, adapter, s3, item, config)
    (rec,) = repo.rows.values()
    assert rec["status"] == "failed"
    # ignore only applies to a *delivered* item — a failed one retries.
    s3.objects[("bucket", "assets/col/scene/a.tif")] = b"V1"
    await _run(repo, adapter, s3, item, config)
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"


async def test_if_newer_skips_unchanged_and_redelivers_changed():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"V1"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config()  # defaults: on_update=redeliver, overwrite=if_newer

    await _run(repo, adapter, s3, item, config)
    await _run(repo, adapter, s3, item, config)  # unchanged -> no second write
    assert len(adapter.puts) == 1
    s3.objects[("bucket", "assets/col/scene/a.tif")] = b"V2-different"
    await _run(repo, adapter, s3, item, config)  # changed -> rewrite
    assert len(adapter.puts) == 2
    (rec,) = repo.rows.values()
    assert rec["delivered_assets"]["data"]["fingerprint"].startswith("sha256:")


async def test_overwrite_never_skips_previously_delivered():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"V1"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(overwrite="never")

    await _run(repo, adapter, s3, item, config)
    s3.objects[("bucket", "assets/col/scene/a.tif")] = b"V2-changed"
    await _run(repo, adapter, s3, item, config)
    assert len(adapter.puts) == 1  # changed bytes, but never overwrite
    (rec,) = repo.rows.values()
    # The prior fingerprint is kept — it reflects what is at the destination.
    import hashlib
    assert rec["delivered_assets"]["data"]["fingerprint"] == f"sha256:{hashlib.sha256(b'V1').hexdigest()}"


async def test_overwrite_always_rewrites_unchanged():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"V1"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(overwrite="always")

    await _run(repo, adapter, s3, item, config)
    await _run(repo, adapter, s3, item, config)
    assert len(adapter.puts) == 2


async def test_payload_sidecars_written_in_order_marker_last():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMG"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": True, "checksums": "sha256", "completion_marker": True})

    await _run(repo, adapter, s3, item, config)
    paths = [p for p, _ in adapter.puts]
    assert paths == ["a.tif", "a.tif.sha256", "scene.json", "scene.done"]
    import hashlib
    body = dict(adapter.puts)["a.tif.sha256"]
    assert body == f"{hashlib.sha256(b'IMG').hexdigest()}  a.tif\n".encode()
    (rec,) = repo.rows.values()
    assert rec["bytes"] == sum(len(d) for _, d in adapter.puts)


async def test_metadata_only_update_rewrites_item_json_only():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMG"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": True, "checksums": None, "completion_marker": True})

    await _run(repo, adapter, s3, item, config)
    n = len(adapter.puts)
    item2 = dict(item, properties={"platform": "edited"})
    await _run(repo, adapter, s3, item2, config)
    new = [p for p, _ in adapter.puts[n:]]
    # asset unchanged -> only the item JSON refreshes, then the marker.
    assert new == ["scene.json", "scene.done"]


async def test_no_writes_when_nothing_changed_and_no_item_json():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMG"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": False, "checksums": None, "completion_marker": True})

    await _run(repo, adapter, s3, item, config)
    n = len(adapter.puts)
    await _run(repo, adapter, s3, item, config)
    # nothing written -> no marker either; row still flips back to delivered.
    assert len(adapter.puts) == n
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"
```

Existing tests in the file stay as-is (the new kwargs default off).

- [ ] **Step 2: Run to verify failure**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_worker.py -v`
Expected: new tests FAIL (no gating, no sidecars, no delivered_assets); old tests PASS.

- [ ] **Step 3: Rewrite `delivery/worker.py`**

```python
"""Deliver one item's assets to a destination (ROADMAP §6.4, Slice B-ii).

For each requested asset key: resolve the source bytes (canonical platform
bucket; Tasks 6–7 add reference-mode sources and S3→S3 server-side copy),
render the destination path from ``path_template``, apply the association's
``on_update``/``overwrite`` policy against ``delivery_log.delivered_assets``
(log-based — never a destination round-trip), and write atomically via the
adapter. Payload sidecars land beside the assets: a checksum per written file,
the item JSON on every processed event, and the completion marker LAST (§6.4).

Records one ``delivery_log`` row per (association, item). A transfer failure
marks the row ``failed`` and does NOT re-raise, so one item's failure never
aborts the rest of the batch job; the B-iii retry sweep re-drives ``failed``
rows.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.connections.repo import ConnectionRow
from pipeline.delivery.config import DeliveryConfig
from pipeline.delivery.path import render_path
from pipeline.delivery.payload import (
    checksum_payload,
    completion_payload,
    item_json_payload,
)
from pipeline.delivery.repo import DeliverTarget, DeliveryRepo
from pipeline.delivery.transfer import sha256_fingerprint
from pipeline.storage import platform
from pipeline.storage.keys import canonical_asset_key

logger = logging.getLogger(__name__)

#: Builds a live adapter for a reference-mode item's ingest source connection
#: (decrypt → adapter; supplied by the deliver job, faked in unit tests).
SourceAdapterFactory = Callable[[ConnectionRow], StorageAdapter]


def _asset_filename(asset: dict[str, Any]) -> str:
    """The canonical object filename for an asset — the last path segment of its
    ``href`` (which the ingest/upload paths set to ``/api/assets/.../{filename}``)."""
    href = asset.get("href")
    if not href:
        raise ValueError("asset has no href")
    return unquote(str(href).rstrip("/").rsplit("/", 1)[-1])


def _should_write(overwrite: str, prev: dict[str, Any] | None, fingerprint: str) -> bool:
    """Log-based overwrite gate (spec decision 2): decide from our own
    delivered_assets, never a destination round-trip. A first delivery
    (no prior entry) always writes."""
    if prev is None or overwrite == "always":
        return True
    if overwrite == "never":
        return False
    return prev.get("fingerprint") != fingerprint  # if_newer


async def _write_sidecar(
    adapter: StorageAdapter,
    config: DeliveryConfig,
    item: dict[str, Any],
    payload: tuple[str, bytes],
) -> int:
    """Render the sidecar's own filename through the path template and write it
    atomically. Returns the byte count (counted into delivery_log.bytes)."""
    filename, body = payload
    await adapter.put_atomic(render_path(config.path_template, item, filename), body)
    return len(body)


async def deliver_item(
    repo: DeliveryRepo,
    adapter: StorageAdapter,
    s3_client: platform.S3Like,
    bucket: str,
    *,
    target: DeliverTarget,
    config: DeliveryConfig,
    item: dict[str, Any],
    asset_keys: list[str],
    item_created_at: str | None,
    build_source_adapter: SourceAdapterFactory | None = None,
    server_side_copy: bool = False,
) -> None:
    item_id = str(item["id"])
    prior = await repo.get_row(target.id, item_id)
    if prior is not None and prior.status == "delivered" and config.on_update == "ignore":
        # Fire-once-per-item (§6.4): the item already delivered and this
        # association ignores updates — consume the event, touch nothing.
        logger.info(
            "delivery skipped (on_update: ignore)",
            extra={"association_id": target.id, "item_id": item_id},
        )
        return
    row_id = await repo.upsert_pending(target.id, item_id, item_created_at)
    await repo.mark_delivering(row_id)
    try:
        assets = item.get("assets") or {}
        checksums_algo = config.payload.get("checksums")
        delivered: dict[str, dict[str, Any]] = dict(prior.delivered_assets) if prior else {}
        total = 0
        wrote_any = False
        for key in asset_keys:
            asset = assets.get(key)
            if asset is None:
                # Asset vanished between match and delivery — skip, deliver the rest.
                continue
            filename = _asset_filename(asset)
            canonical_key = canonical_asset_key(target.collection_id, item_id, filename)
            data = await asyncio.to_thread(
                platform.get_object, s3_client, bucket, canonical_key
            )
            fingerprint = sha256_fingerprint(data)
            size = len(data)
            if not _should_write(config.overwrite, delivered.get(key), fingerprint):
                continue  # keep the prior entry — it reflects the destination
            dest = render_path(config.path_template, item, filename)
            await adapter.put_atomic(dest, data)
            total += size
            wrote_any = True
            if checksums_algo:
                digest = hashlib.new(checksums_algo, data).hexdigest()
                total += await _write_sidecar(
                    adapter, config, item, checksum_payload(filename, checksums_algo, digest)
                )
            delivered[key] = {"fingerprint": fingerprint, "size": size, "filename": filename}
        if config.payload.get("item_json"):
            # Rewritten on every processed event — item metadata can change
            # with no asset change.
            total += await _write_sidecar(adapter, config, item, item_json_payload(item))
            wrote_any = True
        if config.payload.get("completion_marker") and wrote_any:
            # LAST (§6.4): a consumer that sees the marker sees every listed file.
            total += await _write_sidecar(
                adapter, config, item, completion_payload(item_id, delivered)
            )
        await repo.mark_delivered(row_id, total, delivered)
        logger.info(
            "delivery complete",
            extra={"association_id": target.id, "item_id": item_id, "bytes": total},
        )
    except Exception as exc:  # record + continue, retry is B-iii (not enabled: BLE001)
        await repo.mark_failed(row_id, str(exc))
        logger.exception(
            "delivery failed",
            extra={"association_id": target.id, "item_id": item_id},
        )
```

(`build_source_adapter` and `server_side_copy` are accepted but unused until Tasks 6–7 — keep them in the signature now so call sites are stable.)

- [ ] **Step 4: Run the full suite**

Run: `cd services/pipeline && uv run pytest`
Expected: PASS (including the pre-existing worker tests — first deliveries write unconditionally).

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/delivery/worker.py services/pipeline/tests/test_delivery_worker.py
git commit -m "feat(phase5): worker enforces on_update/overwrite + payload sidecars, records delivered_assets (B-ii)"
```

---

### Task 6: Worker — reference-mode source reads

**Files:**
- Modify: `services/pipeline/src/pipeline/delivery/worker.py`
- Test: `services/pipeline/tests/test_delivery_worker.py`

**Interfaces:**
- Consumes: `ReferenceSource` + `load_reference_sources` (Task 2); `build_source_adapter` kwarg (Task 5).
- Produces: reference assets stream from the source adapter; canonical path unchanged.

- [ ] **Step 1: Write failing tests**

Append to `services/pipeline/tests/test_delivery_worker.py`:

```python
class _FakeSourceAdapter:
    def __init__(self, objects):
        self.objects = objects
        self.gets: list[str] = []

    async def get(self, path):
        self.gets.append(path)
        return self.objects[path]


async def test_reference_asset_reads_source_adapter_not_canonical():
    from pipeline.delivery.repo import ReferenceSource
    from pipeline.connections.repo import ConnectionRow

    conn = ConnectionRow(id="c9", name="src", protocol="s3", config={}, credentials=None, host_key=None)
    src = ReferenceSource(filename="a.tif", fetch_path="incoming/a.tif", connection=conn)
    repo = FakeDeliveryRepo(reference_sources={"scene": [src]})
    adapter = _FakeAdapter()
    s3 = _FakeS3({})  # canonical is EMPTY — a canonical read would raise
    source = _FakeSourceAdapter({"incoming/a.tif": b"REFBYTES"})
    built: list = []

    def _factory(connection):
        built.append(connection)
        return source

    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=_config(), item=item,
        asset_keys=["data"], item_created_at=None,
        build_source_adapter=_factory,
    )
    assert source.gets == ["incoming/a.tif"]
    assert adapter.puts == [("a.tif", b"REFBYTES")]
    assert built == [conn]
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"


async def test_source_adapter_built_once_per_connection():
    from pipeline.delivery.repo import ReferenceSource
    from pipeline.connections.repo import ConnectionRow

    conn = ConnectionRow(id="c9", name="src", protocol="s3", config={}, credentials=None, host_key=None)
    sources = [
        ReferenceSource(filename="a.tif", fetch_path="in/a.tif", connection=conn),
        ReferenceSource(filename="b.tif", fetch_path="in/b.tif", connection=conn),
    ]
    repo = FakeDeliveryRepo(reference_sources={"scene": sources})
    adapter = _FakeAdapter()
    source = _FakeSourceAdapter({"in/a.tif": b"A", "in/b.tif": b"B"})
    built: list = []

    def _factory(connection):
        built.append(connection)
        return source

    item = _item({
        "a": {"href": "/api/assets/col/scene/a.tif"},
        "b": {"href": "/api/assets/col/scene/b.tif"},
    })
    await deliver_item(
        repo, adapter, _FakeS3({}), "bucket",
        target=_target(), config=_config(), item=item,
        asset_keys=["a", "b"], item_created_at=None,
        build_source_adapter=_factory,
    )
    assert len(built) == 1  # cached per connection id
    assert {p for p, _ in adapter.puts} == {"a.tif", "b.tif"}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_worker.py -v -k reference or once_per`
(Use: `uv run pytest tests/test_delivery_worker.py -v`.) Expected: the two new tests FAIL (canonical read raises KeyError → row `failed`).

- [ ] **Step 3: Implement the reference branch**

In `worker.py`, add the import `from pipeline.delivery.repo import DeliverTarget, DeliveryRepo, ReferenceSource` (extend the existing import) and this helper after `_should_write`:

```python
async def _read_reference(
    ref: ReferenceSource,
    cache: dict[str, StorageAdapter],
    build_source_adapter: SourceAdapterFactory,
) -> bytes:
    """Reference-mode asset (spec decision 3): bytes live at the ingest source.
    Build (and cache per connection) the source adapter and read in place —
    the ``SourceAdapterByteSource`` pattern from EXTRACT."""
    src = cache.get(ref.connection.id)
    if src is None:
        src = build_source_adapter(ref.connection)
        cache[ref.connection.id] = src
    return await src.get(ref.fetch_path)
```

In `deliver_item`, after `await repo.mark_delivering(row_id)` and inside the `try:`, before the asset loop, add:

```python
        ref_sources: dict[str, ReferenceSource] = {}
        if build_source_adapter is not None:
            ref_sources = {
                ref.filename: ref
                for ref in await repo.load_reference_sources(item_id)
            }
        source_adapters: dict[str, StorageAdapter] = {}
```

Then replace the canonical read in the loop —

```python
            filename = _asset_filename(asset)
            canonical_key = canonical_asset_key(target.collection_id, item_id, filename)
            data = await asyncio.to_thread(
                platform.get_object, s3_client, bucket, canonical_key
            )
```

— with:

```python
            filename = _asset_filename(asset)
            ref = ref_sources.get(filename)
            if ref is not None:
                data = await _read_reference(ref, source_adapters, build_source_adapter)
            else:
                canonical_key = canonical_asset_key(target.collection_id, item_id, filename)
                data = await asyncio.to_thread(
                    platform.get_object, s3_client, bucket, canonical_key
                )
```

- [ ] **Step 4: Run the full suite**

Run: `cd services/pipeline && uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/delivery/worker.py services/pipeline/tests/test_delivery_worker.py
git commit -m "feat(phase5): worker delivers reference-mode assets via the ingest source adapter (B-ii)"
```

---

### Task 7: Worker — S3→S3 server-side copy

**Files:**
- Modify: `services/pipeline/src/pipeline/delivery/worker.py`
- Test: `services/pipeline/tests/test_delivery_worker.py`

**Interfaces:**
- Consumes: `etag_fingerprint`, `platform.head_object`, `adapter.copy_object_from` (Task 3); `server_side_copy` kwarg (Task 5).
- Produces: copy path engages when `server_side_copy=True` for canonical (non-reference) assets and `payload.checksums != "sha256"`; falls back to streaming on multipart-etag md5 or a copy error.

- [ ] **Step 1: Write failing tests**

Append to `services/pipeline/tests/test_delivery_worker.py`. First extend `_FakeS3` with `head_object` and `_FakeAdapter` with `copy_object_from` (edit the existing classes at the top of the file):

```python
class _FakeS3:
    """get_object/head_object over a dict keyed by (bucket, key)."""

    def __init__(self, objects, etags=None):
        self.objects = objects
        self.etags = etags or {}
        self.heads: list[tuple[str, str]] = []

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 kwarg names
        import io

        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, Bucket, Key):  # noqa: N803 - boto3 kwarg names
        self.heads.append((Bucket, Key))
        data = self.objects[(Bucket, Key)]
        import hashlib

        etag = self.etags.get((Bucket, Key), hashlib.md5(data).hexdigest())
        return {"ETag": f'"{etag}"', "ContentLength": len(data)}
```

```python
class _FakeAdapter:
    def __init__(self, copy_error=None):
        self.puts: list[tuple[str, bytes]] = []
        self.copies: list[tuple[str, str, str]] = []
        self.copy_error = copy_error

    async def put_atomic(self, path, data):
        self.puts.append((path, data))

    async def copy_object_from(self, src_bucket, src_key, dst_path):
        if self.copy_error:
            raise self.copy_error
        self.copies.append((src_bucket, src_key, dst_path))
```

Then the tests:

```python
async def test_server_side_copy_no_stream():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=_config(), item=item,
        asset_keys=["data"], item_created_at=None,
        server_side_copy=True,
    )
    assert adapter.copies == [("bucket", "assets/col/scene/a.tif", "a.tif")]
    assert adapter.puts == []  # no bytes through the worker
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"
    assert rec["delivered_assets"]["data"]["fingerprint"].startswith("etag:")
    assert rec["bytes"] == len(b"IMGDATA")


async def test_copy_skipped_unchanged_on_redelivery():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    for _ in range(2):
        await deliver_item(
            repo, adapter, s3, "bucket",
            target=_target(), config=_config(), item=item,
            asset_keys=["data"], item_created_at=None,
            server_side_copy=True,
        )
    assert len(adapter.copies) == 1  # unchanged etag -> if_newer skips


async def test_sha256_checksums_force_streaming():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": False, "checksums": "sha256", "completion_marker": False})

    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["data"], item_created_at=None,
        server_side_copy=True,
    )
    assert adapter.copies == []  # honest checksum beats copy efficiency
    assert [p for p, _ in adapter.puts] == ["a.tif", "a.tif.sha256"]


async def test_md5_checksum_uses_single_part_etag_and_keeps_copy():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": False, "checksums": "md5", "completion_marker": False})

    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["data"], item_created_at=None,
        server_side_copy=True,
    )
    assert len(adapter.copies) == 1
    import hashlib
    body = dict(adapter.puts)["a.tif.md5"]
    assert body == f"{hashlib.md5(b'IMGDATA').hexdigest()}  a.tif\n".encode()


async def test_md5_checksum_multipart_etag_falls_back_to_stream():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3(
        {("bucket", "assets/col/scene/a.tif"): b"IMGDATA"},
        etags={("bucket", "assets/col/scene/a.tif"): "abc123-4"},  # multipart
    )
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": False, "checksums": "md5", "completion_marker": False})

    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["data"], item_created_at=None,
        server_side_copy=True,
    )
    assert adapter.copies == []  # "abc123-4" is not an md5 — stream instead
    import hashlib
    body = dict(adapter.puts)["a.tif.md5"]
    assert body == f"{hashlib.md5(b'IMGDATA').hexdigest()}  a.tif\n".encode()


async def test_copy_failure_falls_back_to_streaming():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter(copy_error=RuntimeError("AccessDenied"))
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=_config(), item=item,
        asset_keys=["data"], item_created_at=None,
        server_side_copy=True,
    )
    assert adapter.puts == [("a.tif", b"IMGDATA")]  # fell back, delivery succeeded
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"
    assert rec["delivered_assets"]["data"]["fingerprint"].startswith("sha256:")


async def test_reference_asset_never_server_side_copies():
    from pipeline.delivery.repo import ReferenceSource
    from pipeline.connections.repo import ConnectionRow

    conn = ConnectionRow(id="c9", name="src", protocol="s3", config={}, credentials=None, host_key=None)
    src = ReferenceSource(filename="a.tif", fetch_path="in/a.tif", connection=conn)
    repo = FakeDeliveryRepo(reference_sources={"scene": [src]})
    adapter = _FakeAdapter()
    source = _FakeSourceAdapter({"in/a.tif": b"REF"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    await deliver_item(
        repo, adapter, _FakeS3({}), "bucket",
        target=_target(), config=_config(), item=item,
        asset_keys=["data"], item_created_at=None,
        build_source_adapter=lambda c: source,
        server_side_copy=True,
    )
    assert adapter.copies == []
    assert adapter.puts == [("a.tif", b"REF")]
```

Note: `test_delivers_asset_bytes_and_records_row` and other pre-existing tests construct `_FakeS3({...})` positionally — the extended class stays compatible. Any test that constructed `_FakeAdapter()` is also compatible.

- [ ] **Step 2: Run to verify failure**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_worker.py -v`
Expected: new copy tests FAIL (`server_side_copy` inert).

- [ ] **Step 3: Implement the copy path**

In `worker.py`, extend the transfer import: `from pipeline.delivery.transfer import etag_fingerprint, sha256_fingerprint`. Replace the per-asset source-resolution + write block (the code from `ref = ref_sources.get(filename)` through the `delivered[key] = ...` line) with:

```python
            filename = _asset_filename(asset)
            ref = ref_sources.get(filename)
            canonical_key: str | None = None
            data: bytes | None = None
            etag = ""
            if ref is not None:
                data = await _read_reference(ref, source_adapters, build_source_adapter)
                fingerprint, size = sha256_fingerprint(data), len(data)
            else:
                canonical_key = canonical_asset_key(target.collection_id, item_id, filename)
                # sha256 sidecars need the bytes; md5 can ride a single-part etag.
                use_copy = server_side_copy and checksums_algo != "sha256"
                if use_copy:
                    etag, size = await asyncio.to_thread(
                        platform.head_object, s3_client, bucket, canonical_key
                    )
                    if checksums_algo == "md5" and "-" in etag:
                        use_copy = False  # multipart etag is not an md5 — stream
                    else:
                        fingerprint = etag_fingerprint(etag, size)
                if not use_copy:
                    data = await asyncio.to_thread(
                        platform.get_object, s3_client, bucket, canonical_key
                    )
                    fingerprint, size = sha256_fingerprint(data), len(data)
            if not _should_write(config.overwrite, delivered.get(key), fingerprint):
                continue  # keep the prior entry — it reflects the destination
            dest = render_path(config.path_template, item, filename)
            if data is None:
                try:
                    await adapter.copy_object_from(bucket, canonical_key, dest)
                except Exception:  # copy denied/failed — stream instead (not enabled: BLE001)
                    logger.warning(
                        "server-side copy failed; streaming instead",
                        extra={"association_id": target.id, "item_id": item_id, "dest": dest},
                        exc_info=True,
                    )
                    data = await asyncio.to_thread(
                        platform.get_object, s3_client, bucket, canonical_key
                    )
                    fingerprint, size = sha256_fingerprint(data), len(data)
                    await adapter.put_atomic(dest, data)
            else:
                await adapter.put_atomic(dest, data)
            total += size
            wrote_any = True
            if checksums_algo:
                digest = etag if data is None else hashlib.new(checksums_algo, data).hexdigest()
                total += await _write_sidecar(
                    adapter, config, item, checksum_payload(filename, checksums_algo, digest)
                )
            delivered[key] = {"fingerprint": fingerprint, "size": size, "filename": filename}
```

Also update the module docstring's first paragraph to mention reference sources + server-side copy (replace "Tasks 6–7 add reference-mode sources and S3→S3 server-side copy" phrasing with the final behavior: "canonical platform bucket, the ingest source adapter for reference-mode assets, or an S3→S3 server-side copy when the destination shares the platform endpoint").

- [ ] **Step 4: Run the full suite**

Run: `cd services/pipeline && uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/delivery/worker.py services/pipeline/tests/test_delivery_worker.py
git commit -m "feat(phase5): S3→S3 server-side copy with streaming fallback (B-ii)"
```

---

### Task 8: Deliver job wiring — copy gate + source-adapter factory

**Files:**
- Modify: `services/pipeline/src/pipeline/jobs/dispatch.py`
- Test: `services/pipeline/tests/test_delivery_jobs.py`

**Interfaces:**
- Consumes: `can_server_side_copy` (Task 3), `deliver_item` kwargs (Task 5), `build_adapter` (existing).
- Produces: the `pipeline.deliver` handler passes `build_source_adapter` (decrypt→adapter over `settings.egress_allow_hosts`) and the endpoint-gated `server_side_copy` flag.

- [ ] **Step 1: Write failing wiring tests + update the existing handler test**

In `services/pipeline/tests/test_delivery_jobs.py`:

(a) The `deliver` handler will now read `target.connection.protocol`/`.config` and pass two extra kwargs, so update `test_deliver_handler_calls_worker_per_item`: replace `connection=object()` with a real row and widen the fake's signature —

```python
from pipeline.connections.repo import ConnectionRow


def _s3_connection(endpoint):
    return ConnectionRow(
        id="c1",
        name="dest",
        protocol="s3",
        config={"bucket": "dest", "endpoint": endpoint},
        credentials=None,
        host_key=None,
    )
```

In that test use `connection=_s3_connection("http://minio:9000")` and change the fake to:

```python
    async def _fake_deliver_item(
        _repo, _adapter, _s3, _bucket, *, target, config, item, asset_keys,
        item_created_at, **kwargs,
    ):
        calls.append(item["id"])
```

(b) Append a shared runner + the two gate tests:

```python
async def _run_deliver_capturing(monkeypatch, connection, settings):
    queue = InMemoryQueue()
    dispatch.register(queue, settings)
    target = DeliverTarget(
        id="a1", collection_id="col",
        config={"path_template": "{filename}"}, connection=connection,
    )

    class _Repo:
        def __init__(self, _url): ...
        async def load_target(self, _aid):
            return target
        async def get_item(self, _c, item_id):
            return {"id": item_id, "collection": "col", "properties": {}, "assets": {}}

    captured: dict = {}

    async def _fake_deliver_item(_repo, _adapter, _s3, _bucket, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(dispatch, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", _Repo)
    monkeypatch.setattr(dispatch, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(dispatch, "build_platform_client", lambda _s: object())
    monkeypatch.setattr(dispatch, "deliver_item", _fake_deliver_item)
    await queue.tasks[JOB_DELIVER](
        association_id="a1",
        items=[{"item_id": "i1", "asset_keys": ["data"], "item_created_at": None}],
    )
    return captured


async def test_deliver_passes_copy_gate_when_endpoints_match(monkeypatch):
    settings = Settings.from_env(env={})
    captured = await _run_deliver_capturing(
        monkeypatch, _s3_connection(settings.staging_s3_endpoint), settings
    )
    assert captured["server_side_copy"] is True
    assert callable(captured["build_source_adapter"])


async def test_deliver_copy_gate_false_on_foreign_endpoint(monkeypatch):
    settings = Settings.from_env(env={})
    captured = await _run_deliver_capturing(
        monkeypatch, _s3_connection("http://elsewhere:9000"), settings
    )
    assert captured["server_side_copy"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_jobs.py -v`
Expected: FAIL — `deliver_item` not called with the new kwargs.

- [ ] **Step 3: Implement in `jobs/dispatch.py`**

Add imports:

```python
from pipeline.connections.repo import ConnectionRow
from pipeline.delivery.transfer import can_server_side_copy
```

In the `deliver` handler, after `config = parse_delivery_config(target.config)`:

```python
        def _source_adapter(connection: ConnectionRow):
            # Reference-mode assets: decrypt + build the ingest source adapter
            # on demand (worker caches per connection).
            return build_adapter(connection, master_key, settings.egress_allow_hosts)

        server_side_copy = can_server_side_copy(
            target.connection.protocol,
            (target.connection.config or {}).get("endpoint"),
            settings.staging_s3_endpoint,
        )
```

and extend the `deliver_item(...)` call with:

```python
                build_source_adapter=_source_adapter,
                server_side_copy=server_side_copy,
```

Also update the module docstring's B-i scope note (payload/on_update are now enforced; retry → dead-letter remains B-iii).

- [ ] **Step 4: Run the full suite**

Run: `cd services/pipeline && uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/jobs/dispatch.py services/pipeline/tests/test_delivery_jobs.py
git commit -m "feat(phase5): deliver job wires copy gate + reference source-adapter factory (B-ii)"
```

---

### Task 9: Docs — ROADMAP, ISSUES, FEATURES, pipeline README

**Files:**
- Modify: `ROADMAP.md` (Slice B-ii bullet ~line 889 + the phase-status table row for Phase 5, line 579)
- Modify: `docs/ISSUES.md` (I-44 resolved; extend I-43; add I-47)
- Modify: `docs/FEATURES.md` (Phase 5 delivery section)
- Modify: `services/pipeline/README.md` (delivery section)

**Interfaces:** none (prose only). Keep each edit consistent with what Tasks 1–8 actually shipped; follow the existing per-slice writing style in each file.

- [ ] **Step 1: ROADMAP** — change the `⬜ Slice B-ii/B-iii` bullet into a `🟢 Slice B-ii (code done)` entry describing: migration 009 `delivered_assets`, log-based `on_update`/`overwrite`, payload sidecars (item JSON / coreutils checksums / `{item_id}.done` manifest written last), reference-source delivery via the ingest source adapter (ledger-first), same-endpoint S3→S3 `CopyObject` with streaming fallback, I-44 resolved; note the checksums×copy trade (sha256 forces streaming; md5 rides a single-part etag) and that live verification is pending (Task 11). Keep a `⬜ Slice B-iii` bullet for the remaining scope (retry → dead-letter, `next_attempt_at`, concurrency caps, live SFTP/FTP — I-43/I-45). Update the phase-table row (line 579) equivalently.
- [ ] **Step 2: ISSUES** — mark I-44 `✅ RESOLVED` (attempts reset in `upsert_pending`, B-ii) following the file's resolved-issue style; extend I-43 with "delivered_assets fingerprints make redundant redelivery cheap but do not dedup concurrent dispatch"; add `I-47 · Copy-path etag fingerprints are endpoint-generation-specific ⚪`: switching an association between streamed (sha256) and copy (etag) transfer — or a destination-bucket re-upload changing etag generation — makes fingerprints compare unequal, costing one redundant redelivery; benign by design (at-least-once), noted for Phase 6 observability.
- [ ] **Step 3: FEATURES + pipeline README** — extend both delivery sections with the B-ii behavior (entry points: `delivery/worker.py`, `delivery/payload.py`, `delivery/transfer.py`, migration 009).
- [ ] **Step 4: Commit**

```bash
git add ROADMAP.md docs/ISSUES.md docs/FEATURES.md services/pipeline/README.md
git commit -m "docs(phase5): Slice B-ii status — payloads, policies, reference source, S3→S3 copy"
```

---

### Task 10: Full gates

- [ ] **Step 1: Pipeline suite**

Run: `cd services/pipeline && uv run pytest`
Expected: PASS (~280+ passed, 2 skipped).

- [ ] **Step 2: Lint (if configured)**

Run: `cd services/pipeline && uv run ruff check src tests`
Expected: clean (fix any findings).

- [ ] **Step 3: App verify**

Run: `npm run verify` (repo root of the worktree; run `npm install` first if `node_modules` is absent).
Expected: build + unit tests pass (~472 passed).

- [ ] **Step 4: Commit any fixes**

```bash
git add -A && git commit -m "fix(phase5): B-ii gate fixes"   # only if fixes were needed
```

---

### Task 11: Live verification (LEAD ONLY — requires Docker; do not run as a subagent/teammate)

Per the spec's live-verification list, against the compose stack (`docker compose up -d`, MinIO + pgstac + pipeline). Follow the Slice B-i live-verification approach (scripted checks against real services; see the B-i entry in ROADMAP for the pattern). Verify:

- [ ] 1. MinIO→MinIO destination delivers via real `CopyObject` (worker logs no stream; object byte-identical; `delivered_assets` fingerprint `etag:...`).
- [ ] 2. A reference-mode item delivers from its source bucket (no canonical read; fingerprint `sha256:...`).
- [ ] 3. A metadata-only re-upsert rewrites only `{item_id}.json` (+ marker), assets skipped.
- [ ] 4. `sha256sum -c` passes against a delivered checksum sidecar; marker `{item_id}.done` lists all assets and arrived last.
- [ ] 5. `on_update: ignore` association ignores a re-upsert; `overwrite: never` skips an unchanged/changed asset re-send; `attempts` resets to 0 on a legitimate redelivery (I-44).
- [ ] 6. Record results in ROADMAP (B-ii bullet → "done + live-verified") and fix-forward any findings on the branch.

Merge to `ai/main` only after this passes (`git checkout ai/main && git merge ai/deliver-b2 --no-ff`), then clean up the worktree per AGENTS.md. Do NOT push `ai/main` (user keeps it local-only).
