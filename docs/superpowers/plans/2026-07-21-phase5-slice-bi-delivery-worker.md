# Phase 5 Slice B-i — Delivery Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An ingest-/UI-created STAC item's asset bytes land on an S3/MinIO delivery destination at a templated path and are recorded in `stac_higher.delivery_log`, driven end-to-end through the real dispatcher.

**Architecture:** The Slice A dispatcher already claims `item_events` outbox rows → reads the item → matches `direction='deliver'` associations → applies `item_filter`/`asset_keys`. This slice replaces "log the match" with real work: the dispatcher groups matches by association and enqueues a batched `pipeline.deliver` job; a worker loads the destination connection, reads each matched asset's **canonical** bytes from the platform bucket, renders the destination path, writes atomically via the adapter, and records a `delivery_log` row. Retry, redelivery/overwrite policy, payload sidecars, reference-mode/S3→S3 copy, and per-connection concurrency are explicitly deferred to B-ii/B-iii.

**Tech Stack:** Python 3 (pipeline service — `asyncio`, `psycopg`, `boto3`, `aioftp`/`asyncssh`), Procrastinate queue behind the in-house `QueueBackend` interface; TypeScript/Astro app for the one DDL migration; `pytest` + `vitest`.

## Global Constraints

- **Ownership (ADR 0001):** the app owns ALL `stac_higher` DDL; the pipeline only reads/writes rows, never runs DDL. `delivery_log` is created by an app migration; the pipeline only writes to it.
- **Never commit to `main`.** All work is on branch `ai/phase5-slice-bi` (already created off `ai/main`, worktree at `.claude/worktrees/phase5-slice-bi`).
- **Verify gate:** `npm run verify` (repo root — app build + unit tests) MUST pass before the slice is declared done. Pipeline unit tests run with `pytest` from `services/pipeline/`. No e2e, no Docker, no dev server during task work — those belong only to the final live-verification task.
- **Pipeline test import style:** sibling test helpers import bare (`from _delivery_fake import ...`), NOT `tests._delivery_fake` — `tests/` has no `__init__.py` and pytest puts it on `sys.path` (see the note in `tests/test_dispatch_loop.py`).
- **Pg repo methods** that open a live connection are marked `# pragma: no cover` — their SQL is exercised by the final live verification, not unit tests (established pattern in `dispatcher/repo.py`, `ingest/repo.py`).
- **`ruff` clean** — the pipeline is linted; run `ruff check` / `ruff format` before each pipeline commit.
- **Deferred to B-ii/B-iii (do NOT build here):** payload sidecars (item JSON / checksums / completion marker), `on_update`/`overwrite` enforcement (B-i delivers on every event, overwrite-always), reference-mode source + S3→S3 server-side copy, retry→dead-letter + `next_attempt_at`, per-connection `max_concurrent_transfers`, live SFTP/FTP destination runs, `flow_stats` telemetry, delivery UI.

---

## File Structure

- **Modify** `app/src/lib/db/migrate.ts` — append migration `008_delivery_log`.
- **Create** `services/pipeline/src/pipeline/delivery/path.py` — pure path-template renderer.
- **Create** `services/pipeline/src/pipeline/delivery/repo.py` — `DeliverTarget`, `DeliveryRepo` ABC, `PgDeliveryRepo`.
- **Create** `services/pipeline/src/pipeline/delivery/worker.py` — `deliver_item`.
- **Modify** `services/pipeline/src/pipeline/connections/adapters/base.py` — abstract `move` + concrete `put_atomic`.
- **Modify** `services/pipeline/src/pipeline/connections/adapters/s3.py` — `move` (copy+delete), override `put_atomic` (direct PUT).
- **Modify** `services/pipeline/src/pipeline/connections/adapters/sftp.py` — `move` (posix rename).
- **Modify** `services/pipeline/src/pipeline/connections/adapters/ftp.py` — `move` (rename).
- **Modify** `services/pipeline/src/pipeline/dispatcher/repo.py` — add `occurred_at` to `ItemEvent` + claim query.
- **Modify** `services/pipeline/src/pipeline/dispatcher/loop.py` — group matches → `enqueue` callback → mark processed.
- **Modify** `services/pipeline/src/pipeline/jobs/dispatch.py` — real enqueue + `pipeline.deliver` handler.
- **Create/Modify** pipeline tests: `_delivery_fake.py`, `test_delivery_path.py`, `test_delivery_worker.py`, `test_delivery_jobs.py`, update `test_adapters.py` (Dummy) + `test_dispatch_loop.py` (enqueue arg).

---

## Task 1: Migration 008 — `delivery_log` table (app)

**Files:**
- Modify: `app/src/lib/db/migrate.ts` — append a migration object to the `MIGRATIONS` array (after `007_item_events_outbox`).

**Interfaces:**
- Produces: table `stac_higher.delivery_log` with columns `id uuid`, `association_id uuid FK→collection_connections`, `item_id text`, `status text` (pending|delivering|delivered|failed|dead), `attempts int`, `bytes bigint`, `error text`, `item_created_at timestamptz`, `delivered_at timestamptz`, `created_at/updated_at timestamptz`, `UNIQUE(association_id, item_id)`. Consumed by `PgDeliveryRepo` (Task 4).

- [ ] **Step 1: Add the migration object**

In `app/src/lib/db/migrate.ts`, add this object to the `MIGRATIONS` array immediately after the `007_item_events_outbox` entry (keep the trailing comma):

```ts
  {
    // Phase 5 Slice B-i (ROADMAP §5 DELIVERY_LOG, §6.4): the per-item delivery
    // record the delivery workers maintain. App owns this DDL; the pipeline only
    // writes rows (ADR 0001). One row per (association, item): a later event for
    // the same item UPSERTs it, so B-ii derives first-delivery-vs-redelivery from
    // this row's presence/status, never from the outbox op (an update surfaces as
    // delete+insert — ADR 0007).
    //
    // Phase 6 hygiene (do NOT build now, mirrors audit_log/ingest_files/item_events):
    // envelope-scale table — Phase 6 time-partitions it on created_at + a
    // partition-drop retention job. next_attempt_at (retry scheduling) is added
    // by the B-iii retry sweep, not here.
    name: "008_delivery_log",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.delivery_log (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        association_id uuid NOT NULL REFERENCES stac_higher.collection_connections(id) ON DELETE CASCADE,
        item_id text NOT NULL,
        status text NOT NULL DEFAULT 'pending'
          CHECK (status IN ('pending','delivering','delivered','failed','dead')),
        attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        bytes bigint,
        error text,
        item_created_at timestamptz,
        delivered_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        -- One row per (association, item): the idempotency key. UPSERTed on
        -- redelivery; attempts/delivered_at update in place.
        UNIQUE (association_id, item_id)
      );

      CREATE INDEX IF NOT EXISTS delivery_log_association_idx
        ON stac_higher.delivery_log (association_id);
      -- Reserved for the B-iii retry sweep: cheaply find retryable rows.
      CREATE INDEX IF NOT EXISTS delivery_log_retry_idx
        ON stac_higher.delivery_log (updated_at)
        WHERE status = 'failed';
    `,
  },
```

- [ ] **Step 2: Verify the app builds (typecheck the migration change)**

Run: `npm run verify`
Expected: PASS (build succeeds, all existing vitest suites green). This is the verification gate for a DDL-only change — the repo has no per-migration unit test harness; the table is live-applied and asserted in Task 8.

- [ ] **Step 3: Commit**

```bash
git add app/src/lib/db/migrate.ts
git commit -m "feat(phase5): migration 008 — stac_higher.delivery_log (Slice B-i)"
```

---

## Task 2: Path-template renderer (`delivery/path.py`)

**Files:**
- Create: `services/pipeline/src/pipeline/delivery/path.py`
- Test: `services/pipeline/tests/test_delivery_path.py`

**Interfaces:**
- Produces: `render_path(template: str, item: dict[str, Any], filename: str) -> str` and `class DeliveryPathError(ValueError)`. Tokens: `{collection} {item_id} {filename} {yyyy} {mm} {dd}`. Date tokens resolve from `item["properties"]["datetime"]` → `start_datetime` (UTC); absent → raises. Unknown token → raises. Consumed by `deliver_item` (Task 5).

- [ ] **Step 1: Write the failing tests**

Create `services/pipeline/tests/test_delivery_path.py`:

```python
import pytest

from pipeline.delivery.path import DeliveryPathError, render_path


def _item(**props):
    return {"id": "scene-1", "collection": "col", "properties": props}


def test_renders_simple_tokens():
    tmpl = "{collection}/{item_id}/{filename}"
    assert render_path(tmpl, _item(), "a.tif") == "col/scene-1/a.tif"


def test_renders_date_tokens_from_datetime():
    tmpl = "{collection}/{yyyy}/{mm}/{dd}/{item_id}/{filename}"
    item = _item(datetime="2026-03-05T12:00:00Z")
    assert render_path(tmpl, item, "a.tif") == "col/2026/03/05/scene-1/a.tif"


def test_date_tokens_fall_back_to_start_datetime():
    tmpl = "{yyyy}-{mm}-{dd}/{filename}"
    item = _item(datetime=None, start_datetime="2025-12-31T00:00:00+00:00")
    assert render_path(tmpl, item, "a.tif") == "2025-12-31/a.tif"


def test_missing_datetime_with_date_token_raises():
    with pytest.raises(DeliveryPathError):
        render_path("{yyyy}/{filename}", _item(), "a.tif")


def test_no_date_token_needs_no_datetime():
    # an item with no datetime is fine when the template uses none.
    assert render_path("{item_id}/{filename}", _item(), "a.tif") == "scene-1/a.tif"


def test_unknown_token_raises():
    with pytest.raises(DeliveryPathError):
        render_path("{collection}/{bogus}", _item(), "a.tif")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/pipeline && python -m pytest tests/test_delivery_path.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.delivery.path'`

- [ ] **Step 3: Write the implementation**

Create `services/pipeline/src/pipeline/delivery/path.py`:

```python
"""Render a delivery association's ``path_template`` for one asset (ROADMAP §6.4).

Pure and I/O-free. Tokens (documented in ``app/.../associations/schemas.ts``):
``{collection} {item_id} {filename} {yyyy} {mm} {dd}``. Date tokens resolve from
the item's ``properties.datetime`` (falling back to ``start_datetime``); if a
template references a date token and the item has neither, rendering raises so
delivery fails loudly rather than writing to a wrong path.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

_TOKEN_RE = re.compile(r"\{(\w+)\}")
_DATE_TOKENS = ("{yyyy}", "{mm}", "{dd}")


class DeliveryPathError(ValueError):
    """The path template could not be rendered for this item."""


def _item_datetime(item: dict[str, Any]) -> dt.datetime:
    props = item.get("properties") or {}
    raw = props.get("datetime") or props.get("start_datetime")
    if not raw:
        raise DeliveryPathError(
            "path template uses a date token but the item has no "
            "datetime/start_datetime"
        )
    text = str(raw).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError as exc:  # malformed datetime string
        raise DeliveryPathError(f"unparseable item datetime: {raw!r}") from exc


def render_path(template: str, item: dict[str, Any], filename: str) -> str:
    """Render ``template`` into a destination-relative path for one asset."""
    tokens: dict[str, str] = {
        "collection": str(item.get("collection", "")),
        "item_id": str(item.get("id", "")),
        "filename": filename,
    }
    if any(tok in template for tok in _DATE_TOKENS):
        when = _item_datetime(item)
        tokens["yyyy"] = f"{when.year:04d}"
        tokens["mm"] = f"{when.month:02d}"
        tokens["dd"] = f"{when.day:02d}"

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in tokens:
            raise DeliveryPathError(f"unknown path-template token: {{{name}}}")
        return tokens[name]

    return _TOKEN_RE.sub(_sub, template)
```

Also create an empty package marker if `services/pipeline/src/pipeline/delivery/__init__.py` is missing (it already exists from Slice A — skip if present).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/pipeline && python -m pytest tests/test_delivery_path.py -q && ruff check src/pipeline/delivery/path.py tests/test_delivery_path.py`
Expected: PASS (6 passed), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/delivery/path.py services/pipeline/tests/test_delivery_path.py
git commit -m "feat(phase5): delivery path-template renderer (Slice B-i)"
```

---

## Task 3: Adapter `move` + `put_atomic` (atomic visibility)

**Files:**
- Modify: `services/pipeline/src/pipeline/connections/adapters/base.py`
- Modify: `services/pipeline/src/pipeline/connections/adapters/s3.py`
- Modify: `services/pipeline/src/pipeline/connections/adapters/sftp.py`
- Modify: `services/pipeline/src/pipeline/connections/adapters/ftp.py`
- Modify: `services/pipeline/tests/test_adapters.py` (the `Dummy` subclass must implement the new abstract method)
- Test: `services/pipeline/tests/test_adapter_move.py` (new)

**Interfaces:**
- Produces: `StorageAdapter.move(self, src: str, dst: str) -> None` (abstract) and `StorageAdapter.put_atomic(self, path: str, data: bytes) -> None` (concrete default = `put(path + ".part")` then `move(...)`). `S3Adapter.put_atomic` overrides to a direct `put`. Consumed by `deliver_item` (Task 5).

- [ ] **Step 1: Write the failing tests**

Create `services/pipeline/tests/test_adapter_move.py`:

```python
import pytest

from pipeline.connections.adapters.base import StorageAdapter

pytestmark = pytest.mark.asyncio


class _RecordingAdapter(StorageAdapter):
    """Concrete adapter that records put/move calls (exercises the base
    put_atomic default)."""

    protocol = "rec"

    def __init__(self):
        self.calls: list[tuple] = []

    async def test(self): ...
    async def list(self, prefix=""):
        return []
    async def get(self, path):
        return b""
    async def put(self, path, data):
        self.calls.append(("put", path, data))
    async def delete(self, path): ...
    async def move(self, src, dst):
        self.calls.append(("move", src, dst))


async def test_base_put_atomic_writes_part_then_moves():
    a = _RecordingAdapter()
    await a.put_atomic("dir/file.tif", b"xyz")
    assert a.calls == [
        ("put", "dir/file.tif.part", b"xyz"),
        ("move", "dir/file.tif.part", "dir/file.tif"),
    ]


async def test_s3_put_atomic_is_direct_put(monkeypatch):
    from pipeline.connections.adapters.s3 import S3Adapter

    a = S3Adapter({"bucket": "b"}, {"access_key_id": "k", "secret_access_key": "s"})
    puts: list[tuple] = []

    async def _fake_put(path, data):
        puts.append((path, data))

    monkeypatch.setattr(a, "put", _fake_put)
    await a.put_atomic("k/scene.tif", b"abc")
    # S3 objects appear atomically on PUT — no .part dance.
    assert puts == [("k/scene.tif", b"abc")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/pipeline && python -m pytest tests/test_adapter_move.py -q`
Expected: FAIL — `_RecordingAdapter` still abstract / `put_atomic` undefined (`TypeError: Can't instantiate abstract class` or `AttributeError`).

- [ ] **Step 3: Add `move` + `put_atomic` to the base**

In `services/pipeline/src/pipeline/connections/adapters/base.py`, add these two methods to `StorageAdapter` (after `delete`, before `public_object_url`):

```python
    @abc.abstractmethod
    async def move(self, src: str, dst: str) -> None:
        """Rename ``src`` to ``dst`` on the destination (atomic where the
        protocol supports it). SFTP/FTP use a server-side rename; S3 has no
        rename primitive, so ``S3Adapter`` implements copy + delete."""

    async def put_atomic(self, path: str, data: bytes) -> None:
        """Write ``data`` so ``path`` never appears partially written.

        Default: write to ``path + ".part"`` then ``move`` it into place — the
        atomic-visibility pattern for streaming protocols (§6.4). ``S3Adapter``
        overrides this with a direct ``put`` (S3 objects become visible
        atomically on PUT, so the rename would only cost an extra copy+delete).
        """
        tmp = f"{path}.part"
        await self.put(tmp, data)
        await self.move(tmp, path)
```

- [ ] **Step 4: Implement `move` + override `put_atomic` on `S3Adapter`**

In `services/pipeline/src/pipeline/connections/adapters/s3.py`, add these methods to `S3Adapter` (after `delete`):

```python
    async def move(self, src: str, dst: str) -> None:
        endpoint_url = self._pinned_endpoint()

        def _move() -> None:
            client = self._make_client(endpoint_url)
            client.copy_object(
                Bucket=self._bucket,
                Key=dst,
                CopySource={"Bucket": self._bucket, "Key": src},
            )
            client.delete_object(Bucket=self._bucket, Key=src)

        await asyncio.to_thread(_move)

    async def put_atomic(self, path: str, data: bytes) -> None:
        # S3 PUT is atomically visible; skip the base .part+move dance.
        await self.put(path, data)
```

- [ ] **Step 5: Implement `move` on `SftpAdapter`**

In `services/pipeline/src/pipeline/connections/adapters/sftp.py`, add to `SftpAdapter` (after `delete`):

```python
    async def move(self, src: str, dst: str) -> None:
        src_t, dst_t = self._resolve(src), self._resolve(dst)
        async with await self._connect() as conn, conn.start_sftp_client() as sftp:
            # posix_rename atomically overwrites an existing dst where the server
            # supports the openssh extension (our target servers do).
            await sftp.posix_rename(src_t, dst_t)
```

- [ ] **Step 6: Implement `move` on `FtpAdapter`**

In `services/pipeline/src/pipeline/connections/adapters/ftp.py`, add to `FtpAdapter` (after `delete`, before the module-level `_safe_quit`):

```python
    async def move(self, src: str, dst: str) -> None:
        client = await self._connect_client()
        try:
            await client.rename(self._resolve(src), self._resolve(dst))
        finally:
            await _safe_quit(client)
```

(`FtpsAdapter` inherits this unchanged.)

- [ ] **Step 7: Fix the `Dummy` adapter in `test_adapters.py`**

In `services/pipeline/tests/test_adapters.py`, the `Dummy(StorageAdapter)` at ~line 194 now misses the abstract `move`. Add one line inside the class body (after `async def delete(self, path): ...`):

```python
        async def move(self, src, dst): ...
```

- [ ] **Step 8: Run the adapter tests**

Run: `cd services/pipeline && python -m pytest tests/test_adapter_move.py tests/test_adapters.py -q && ruff check src/pipeline/connections/adapters/ tests/test_adapter_move.py`
Expected: PASS (all adapter tests green), ruff clean.

- [ ] **Step 9: Commit**

```bash
git add services/pipeline/src/pipeline/connections/adapters/ services/pipeline/tests/test_adapter_move.py services/pipeline/tests/test_adapters.py
git commit -m "feat(phase5): adapter move() + put_atomic() for delivery atomic visibility (Slice B-i)"
```

---

## Task 4: Delivery repo + fake (`delivery/repo.py`)

**Files:**
- Create: `services/pipeline/src/pipeline/delivery/repo.py`
- Create: `services/pipeline/tests/_delivery_fake.py`
- Test: `services/pipeline/tests/test_delivery_repo_fake.py`

**Interfaces:**
- Consumes: `ConnectionRow`, `_to_connection_row` from `pipeline.connections.repo`.
- Produces:
  - `@dataclass DeliverTarget(id: str, collection_id: str, config: dict, connection: ConnectionRow)`
  - `class DeliveryRepo(abc.ABC)` with `load_target(association_id) -> DeliverTarget | None`, `get_item(collection_id, item_id) -> dict | None`, `upsert_pending(association_id, item_id, item_created_at: str | None) -> str`, `mark_delivering(row_id) -> None`, `mark_delivered(row_id, byte_count: int) -> None`, `mark_failed(row_id, error: str) -> None`.
  - `class PgDeliveryRepo(DeliveryRepo)` (psycopg, `# pragma: no cover`).
  - `FakeDeliveryRepo` (in `_delivery_fake.py`) with a `rows: dict[str, dict]` store, used by Tasks 5.

- [ ] **Step 1: Write the failing tests (fake contract)**

Create `services/pipeline/tests/test_delivery_repo_fake.py`:

```python
import pytest

from _delivery_fake import FakeDeliveryRepo

pytestmark = pytest.mark.asyncio


async def test_upsert_pending_is_idempotent_per_association_item():
    repo = FakeDeliveryRepo()
    r1 = await repo.upsert_pending("a1", "i1", "2026-01-01T00:00:00+00:00")
    r2 = await repo.upsert_pending("a1", "i1", "2026-01-02T00:00:00+00:00")
    assert r1 == r2  # same (association, item) → same row
    assert repo.rows[r1]["status"] == "pending"
    # a different item gets its own row
    r3 = await repo.upsert_pending("a1", "i2", None)
    assert r3 != r1


async def test_mark_transitions_and_bytes():
    repo = FakeDeliveryRepo()
    rid = await repo.upsert_pending("a1", "i1", None)
    await repo.mark_delivering(rid)
    assert repo.rows[rid]["status"] == "delivering"
    assert repo.rows[rid]["attempts"] == 1
    await repo.mark_delivered(rid, 2048)
    assert repo.rows[rid]["status"] == "delivered"
    assert repo.rows[rid]["bytes"] == 2048


async def test_mark_failed_records_error():
    repo = FakeDeliveryRepo()
    rid = await repo.upsert_pending("a1", "i1", None)
    await repo.mark_failed(rid, "boom")
    assert repo.rows[rid]["status"] == "failed"
    assert repo.rows[rid]["error"] == "boom"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/pipeline && python -m pytest tests/test_delivery_repo_fake.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_delivery_fake'`

- [ ] **Step 3: Write the repo module**

Create `services/pipeline/src/pipeline/delivery/repo.py`:

```python
"""Repository seam over ``stac_higher.delivery_log`` + the destination
association/connection + pgstac items (ROADMAP §5, §6.4).

Mirrors ``ingest/repo.py`` / ``dispatcher/repo.py``: a ``DeliveryRepo`` ABC the
worker + deliver job depend on (unit-tested against ``FakeDeliveryRepo``) plus a
psycopg ``PgDeliveryRepo`` for production. Pg methods open a short-lived
connection and are ``# pragma: no cover`` — exercised by the live verification.

Ownership (ADR 0001): reads ``collection_connections``/``connections`` and pgstac
items; INSERT/UPDATEs only ``delivery_log``. Never runs DDL.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any

from pipeline.connections.repo import ConnectionRow, _to_connection_row


@dataclass
class DeliverTarget:
    """An enabled ``direction='deliver'`` association with its destination
    connection loaded (so the worker can ``build_adapter``). ``config`` is the raw
    §5.1 delivery jsonb (parsed by ``delivery.config.parse_delivery_config``)."""

    id: str
    collection_id: str
    config: dict[str, Any]
    connection: ConnectionRow


class DeliveryRepo(abc.ABC):
    @abc.abstractmethod
    async def load_target(self, association_id: str) -> DeliverTarget | None:
        """Load one enabled deliver association + its connection, or ``None`` if
        it is gone/disabled (a job that arrives after disable must no-op)."""

    @abc.abstractmethod
    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        """The full STAC item from pgstac, or ``None`` if not present."""

    @abc.abstractmethod
    async def upsert_pending(
        self, association_id: str, item_id: str, item_created_at: str | None
    ) -> str:
        """Insert (or reset to pending) the (association, item) delivery_log row;
        return its id. ISO-8601 ``item_created_at`` or ``None``."""

    @abc.abstractmethod
    async def mark_delivering(self, row_id: str) -> None:
        """Flip to delivering and increment attempts."""

    @abc.abstractmethod
    async def mark_delivered(self, row_id: str, byte_count: int) -> None:
        """Flip to delivered; record bytes + delivered_at; clear error."""

    @abc.abstractmethod
    async def mark_failed(self, row_id: str, error: str) -> None:
        """Flip to failed; record the error message."""


_TARGET_COLUMNS = "cc.id, cc.collection_id, cc.config"
_CONNECTION_COLUMNS = "c.id, c.name, c.protocol, c.config, c.credentials, c.host_key, c.enabled"


@dataclass
class PgDeliveryRepo(DeliveryRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def load_target(  # pragma: no cover
        self, association_id: str
    ) -> DeliverTarget | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_TARGET_COLUMNS}, {_CONNECTION_COLUMNS}"
                " FROM stac_higher.collection_connections cc"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE cc.id = %s AND cc.direction = 'deliver'"
                " AND cc.enabled = true AND c.enabled = true",
                (association_id,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        cc_id, collection_id, config = row[:3]
        return DeliverTarget(
            id=str(cc_id),
            collection_id=collection_id,
            config=dict(config) if config else {},
            connection=_to_connection_row(row[3:]),
        )

    async def get_item(  # pragma: no cover
        self, collection_id: str, item_id: str
    ) -> dict[str, Any] | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT pgstac.get_item(%s, %s)", (item_id, collection_id)
            )
            row = await cur.fetchone()
        return dict(row[0]) if row and row[0] else None

    async def upsert_pending(  # pragma: no cover
        self, association_id: str, item_id: str, item_created_at: str | None
    ) -> str:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO stac_higher.delivery_log"
                " (association_id, item_id, item_created_at, status, attempts)"
                " VALUES (%s, %s, %s, 'pending', 0)"
                " ON CONFLICT (association_id, item_id) DO UPDATE"
                " SET status = 'pending',"
                "     item_created_at = EXCLUDED.item_created_at,"
                "     updated_at = now()"
                " RETURNING id",
                (association_id, item_id, item_created_at),
            )
            row = await cur.fetchone()
            await conn.commit()
        return str(row[0])

    async def mark_delivering(self, row_id: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = 'delivering', attempts = attempts + 1, updated_at = now()"
                " WHERE id = %s",
                (row_id,),
            )
            await conn.commit()

    async def mark_delivered(self, row_id: str, byte_count: int) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = 'delivered', bytes = %s, error = NULL,"
                "     delivered_at = now(), updated_at = now()"
                " WHERE id = %s",
                (byte_count, row_id),
            )
            await conn.commit()

    async def mark_failed(self, row_id: str, error: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = 'failed', error = %s, updated_at = now()"
                " WHERE id = %s",
                (error, row_id),
            )
            await conn.commit()
```

- [ ] **Step 4: Write the fake**

Create `services/pipeline/tests/_delivery_fake.py`:

```python
"""In-memory DeliveryRepo for worker + deliver-job unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline.delivery.repo import DeliverTarget, DeliveryRepo


@dataclass
class FakeDeliveryRepo(DeliveryRepo):
    targets: dict[str, DeliverTarget] = field(default_factory=dict)
    items: dict[tuple[str, str], dict] = field(default_factory=dict)
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    _seq: int = 0

    async def load_target(self, association_id: str) -> DeliverTarget | None:
        return self.targets.get(association_id)

    async def get_item(self, collection_id: str, item_id: str) -> dict | None:
        return self.items.get((collection_id, item_id))

    async def upsert_pending(
        self, association_id: str, item_id: str, item_created_at: str | None
    ) -> str:
        for rid, rec in self.rows.items():
            if (rec["association_id"], rec["item_id"]) == (association_id, item_id):
                rec.update(status="pending", item_created_at=item_created_at)
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
        }
        return rid

    async def mark_delivering(self, row_id: str) -> None:
        rec = self.rows[row_id]
        rec["status"] = "delivering"
        rec["attempts"] += 1

    async def mark_delivered(self, row_id: str, byte_count: int) -> None:
        rec = self.rows[row_id]
        rec.update(status="delivered", bytes=byte_count, error=None)

    async def mark_failed(self, row_id: str, error: str) -> None:
        rec = self.rows[row_id]
        rec.update(status="failed", error=error)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/pipeline && python -m pytest tests/test_delivery_repo_fake.py -q && ruff check src/pipeline/delivery/repo.py tests/_delivery_fake.py`
Expected: PASS (3 passed), ruff clean.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/delivery/repo.py services/pipeline/tests/_delivery_fake.py services/pipeline/tests/test_delivery_repo_fake.py
git commit -m "feat(phase5): delivery repo (delivery_log + target load) + fake (Slice B-i)"
```

---

## Task 5: Delivery worker (`delivery/worker.py`)

**Files:**
- Create: `services/pipeline/src/pipeline/delivery/worker.py`
- Test: `services/pipeline/tests/test_delivery_worker.py`

**Interfaces:**
- Consumes: `DeliveryRepo`/`DeliverTarget` (Task 4), `StorageAdapter.put_atomic` (Task 3), `render_path` (Task 2), `platform.get_object`/`platform.S3Like` + `canonical_asset_key`, `DeliveryConfig` (`delivery/config.py`, exists).
- Produces: `async def deliver_item(repo, adapter, s3_client, bucket, *, target: DeliverTarget, config: DeliveryConfig, item: dict, asset_keys: list[str], item_created_at: str | None) -> None`. Consumed by the deliver job (Task 7).

- [ ] **Step 1: Write the failing tests**

Create `services/pipeline/tests/test_delivery_worker.py`:

```python
import pytest

from _delivery_fake import FakeDeliveryRepo
from pipeline.delivery.config import parse_delivery_config
from pipeline.delivery.repo import DeliverTarget
from pipeline.delivery.worker import deliver_item

pytestmark = pytest.mark.asyncio


class _FakeAdapter:
    def __init__(self):
        self.puts: list[tuple[str, bytes]] = []

    async def put_atomic(self, path, data):
        self.puts.append((path, data))


class _FakeS3:
    """Only get_object is used; keyed by (bucket, key)."""

    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 kwarg names
        import io

        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def _target():
    return DeliverTarget(id="a1", collection_id="col", config={}, connection=None)


def _item(assets):
    return {"id": "scene", "collection": "col", "properties": {}, "assets": assets}


async def test_delivers_asset_bytes_and_records_row():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = parse_delivery_config({"path_template": "{collection}/{item_id}/{filename}"})

    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["data"], item_created_at=None,
    )

    assert adapter.puts == [("col/scene/a.tif", b"IMGDATA")]
    (rid, rec), = repo.rows.items()
    assert rec["status"] == "delivered"
    assert rec["bytes"] == len(b"IMGDATA")


async def test_multiple_assets_sum_bytes():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({
        ("bucket", "assets/col/scene/a.tif"): b"AAA",
        ("bucket", "assets/col/scene/b.tif"): b"BBBB",
    })
    item = _item({
        "a": {"href": "/api/assets/col/scene/a.tif"},
        "b": {"href": "/api/assets/col/scene/b.tif"},
    })
    config = parse_delivery_config({"path_template": "{filename}"})

    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["a", "b"], item_created_at=None,
    )
    (rec,) = repo.rows.values()
    assert rec["bytes"] == 7
    assert {p for p, _ in adapter.puts} == {"a.tif", "b.tif"}


async def test_missing_asset_key_is_skipped():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"AAA"})
    item = _item({"a": {"href": "/api/assets/col/scene/a.tif"}})
    config = parse_delivery_config({"path_template": "{filename}"})

    # "gone" isn't in the item's assets — skip it, deliver "a".
    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["a", "gone"], item_created_at=None,
    )
    assert [p for p, _ in adapter.puts] == ["a.tif"]
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"


async def test_transfer_failure_marks_failed_without_raising():
    repo = FakeDeliveryRepo()

    class _BoomAdapter(_FakeAdapter):
        async def put_atomic(self, path, data):
            raise RuntimeError("dest down")

    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"AAA"})
    item = _item({"a": {"href": "/api/assets/col/scene/a.tif"}})
    config = parse_delivery_config({"path_template": "{filename}"})

    await deliver_item(
        repo, _BoomAdapter(), s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["a"], item_created_at=None,
    )
    (rec,) = repo.rows.values()
    assert rec["status"] == "failed"
    assert "dest down" in rec["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/pipeline && python -m pytest tests/test_delivery_worker.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.delivery.worker'`

- [ ] **Step 3: Write the worker**

Create `services/pipeline/src/pipeline/delivery/worker.py`:

```python
"""Deliver one item's assets to a destination (ROADMAP §6.4, Slice B-i).

For each requested asset key: read the **canonical** bytes from the platform
bucket (``assets/{collection}/{item_id}/{filename}``), render the destination
path from the association's ``path_template``, and write atomically via the
adapter (``put_atomic``). Records one ``delivery_log`` row per (association,
item), moving pending → delivering → delivered (or failed).

B-i scope: canonical-bytes stream only. Reference-mode source resolution +
S3→S3 server-side copy are B-ii; payload sidecars, on_update/overwrite, and
retry are B-ii/B-iii. A transfer failure marks the row ``failed`` and does NOT
re-raise, so one item's failure never aborts the rest of the batch job; the
B-iii retry sweep re-drives ``failed`` rows.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import unquote

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.delivery.config import DeliveryConfig
from pipeline.delivery.path import render_path
from pipeline.delivery.repo import DeliverTarget, DeliveryRepo
from pipeline.storage import platform
from pipeline.storage.keys import canonical_asset_key

logger = logging.getLogger(__name__)


def _asset_filename(asset: dict[str, Any]) -> str:
    """The canonical object filename for an asset — the last path segment of its
    ``href`` (which the ingest/upload paths set to ``/api/assets/.../{filename}``)."""
    href = asset.get("href")
    if not href:
        raise ValueError("asset has no href")
    return unquote(str(href).rstrip("/").rsplit("/", 1)[-1])


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
) -> None:
    item_id = str(item["id"])
    row_id = await repo.upsert_pending(target.id, item_id, item_created_at)
    await repo.mark_delivering(row_id)
    try:
        assets = item.get("assets") or {}
        total = 0
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
            dest = render_path(config.path_template, item, filename)
            await adapter.put_atomic(dest, data)
            total += len(data)
        await repo.mark_delivered(row_id, total)
        logger.info(
            "delivery complete",
            extra={"association_id": target.id, "item_id": item_id, "bytes": total},
        )
    except Exception as exc:  # noqa: BLE001 - record + continue, retry is B-iii
        await repo.mark_failed(row_id, str(exc))
        logger.exception(
            "delivery failed",
            extra={"association_id": target.id, "item_id": item_id},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/pipeline && python -m pytest tests/test_delivery_worker.py -q && ruff check src/pipeline/delivery/worker.py tests/test_delivery_worker.py`
Expected: PASS (4 passed), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/delivery/worker.py services/pipeline/tests/test_delivery_worker.py
git commit -m "feat(phase5): delivery worker — canonical bytes → destination (Slice B-i)"
```

---

## Task 6: Dispatcher grouping + enqueue (`dispatcher/loop.py`, `dispatcher/repo.py`)

**Files:**
- Modify: `services/pipeline/src/pipeline/dispatcher/repo.py` — add `occurred_at` to `ItemEvent` + claim query.
- Modify: `services/pipeline/src/pipeline/dispatcher/loop.py` — take an `enqueue` callback, group matches by association, enqueue before marking processed.
- Modify: `services/pipeline/tests/test_dispatch_loop.py` — pass the new `enqueue` arg; add grouping/ordering tests.

**Interfaces:**
- Consumes: `match_item`/`Match`/`DeliverAssociation` (exist).
- Produces: `dispatch_once(repo: DispatchRepo, enqueue: Callable[[list[dict]], Awaitable[None]], *, batch_size: int = 100) -> list[Match]`. Each enqueued batch dict = `{"association_id": str, "items": [{"item_id": str, "asset_keys": list[str], "item_created_at": str | None}]}`. `ItemEvent` gains `occurred_at: datetime | None = None`. Consumed by `jobs/dispatch.py` (Task 7).

- [ ] **Step 1: Add `occurred_at` to `ItemEvent` + claim query**

In `services/pipeline/src/pipeline/dispatcher/repo.py`:

Add the import at the top (with the other imports):
```python
import datetime as dt
```

Extend the `ItemEvent` dataclass:
```python
@dataclass(frozen=True)
class ItemEvent:
    id: int
    collection_id: str
    item_id: str
    op: str
    occurred_at: dt.datetime | None = None
```

Update `PgDispatchRepo.claim_pending_events` to SELECT and map `occurred_at`:
```python
    async def claim_pending_events(self, limit: int) -> list[ItemEvent]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, collection_id, item_id, op, occurred_at"
                " FROM stac_higher.item_events"
                " WHERE processed_at IS NULL ORDER BY id"
                " FOR UPDATE SKIP LOCKED LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [
            ItemEvent(
                id=int(r[0]), collection_id=r[1], item_id=r[2], op=r[3], occurred_at=r[4]
            )
            for r in rows
        ]
```

- [ ] **Step 2: Write the failing loop tests**

Replace the body of `services/pipeline/tests/test_dispatch_loop.py` with (adds an `enqueue` collector to every call and two new tests):

```python
import pytest

# NOTE: this test module has no `tests` package (no __init__.py); pytest's
# rootdir-insertion import mode puts `tests/` itself on sys.path, so sibling
# modules import bare (`_dispatch_fake`), matching the established pattern in
# test_ingest_fetch.py — not as `tests._dispatch_fake`.
from _dispatch_fake import FakeDispatchRepo
from pipeline.delivery.matcher import DeliverAssociation
from pipeline.dispatcher.loop import dispatch_once
from pipeline.dispatcher.repo import ItemEvent

pytestmark = pytest.mark.asyncio


def _item(item_id):
    return {"id": item_id, "collection": "c", "properties": {}, "assets": {"data": {}}}


def _collector():
    """Return (enqueue_callable, captured_batches_list)."""
    captured: list[list[dict]] = []

    async def _enqueue(batches):
        captured.append(batches)

    return _enqueue, captured


async def test_matches_and_drains_outbox():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1")},
    )
    enqueue, captured = _collector()
    matches = await dispatch_once(repo, enqueue)
    assert [m.association_id for m in matches] == ["a1"]
    assert repo.processed == [1]
    # one batch for association a1 carrying item i1's single asset.
    assert captured == [[{
        "association_id": "a1",
        "items": [{"item_id": "i1", "asset_keys": ["data"], "item_created_at": None}],
    }]]


async def test_associations_queried_once_per_collection_in_batch():
    repo = FakeDispatchRepo(
        events=[
            ItemEvent(id=1, collection_id="c", item_id="i1", op="insert"),
            ItemEvent(id=2, collection_id="c", item_id="i2", op="insert"),
        ],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1"), ("c", "i2"): _item("i2")},
    )
    enqueue, captured = _collector()
    matches = await dispatch_once(repo, enqueue)
    assert [m.association_id for m in matches] == ["a1", "a1"]
    assert repo.assoc_calls == 1
    assert repo.processed == [1, 2]
    # both items grouped under ONE association batch (batch-oriented jobs).
    assert captured == [[{
        "association_id": "a1",
        "items": [
            {"item_id": "i1", "asset_keys": ["data"], "item_created_at": None},
            {"item_id": "i2", "asset_keys": ["data"], "item_created_at": None},
        ],
    }]]


async def test_delete_event_is_drained_without_matching():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=2, collection_id="c", item_id="gone", op="delete")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
    )
    enqueue, captured = _collector()
    matches = await dispatch_once(repo, enqueue)
    assert matches == []
    assert repo.processed == [2]  # deletions never propagate, but the row drains
    assert captured == []  # nothing enqueued


async def test_missing_item_drains_without_crashing():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=3, collection_id="c", item_id="race", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={},
    )
    enqueue, captured = _collector()
    matches = await dispatch_once(repo, enqueue)
    assert matches == []
    assert repo.processed == [3]
    assert captured == []


async def test_enqueue_happens_before_mark_processed():
    # If enqueue fails, the outbox rows must NOT be marked processed (at-least-once).
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1")},
    )

    async def _boom(_batches):
        raise RuntimeError("queue down")

    with pytest.raises(RuntimeError):
        await dispatch_once(repo, _boom)
    assert repo.processed == []  # not drained — a redrive will retry
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd services/pipeline && python -m pytest tests/test_dispatch_loop.py -q`
Expected: FAIL — `dispatch_once()` missing the `enqueue` positional arg / grouping assertions unmet.

- [ ] **Step 4: Rewrite `dispatch_once`**

Replace the body of `services/pipeline/src/pipeline/dispatcher/loop.py` from the imports down with:

```python
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pipeline.delivery.matcher import DeliverAssociation, Match, match_item
from pipeline.dispatcher.repo import DispatchRepo

logger = logging.getLogger(__name__)

#: enqueue callback: given the grouped delivery batches, hand them to the queue.
EnqueueDeliveries = Callable[[list[dict[str, Any]]], Awaitable[None]]


async def dispatch_once(
    repo: DispatchRepo, enqueue: EnqueueDeliveries, *, batch_size: int = 100
) -> list[Match]:
    """Claim a batch of outbox rows, match each non-delete item against its
    collection's delivery associations, group matches into per-association
    delivery batches, hand them to ``enqueue``, THEN drain the outbox.

    Enqueue-before-drain gives at-least-once delivery: if ``enqueue`` raises, the
    outbox rows stay pending and a later tick re-drives them.
    """
    events = await repo.claim_pending_events(batch_size)
    if not events:
        return []

    # Cache deliver associations per collection for this batch (a bulk upsert of
    # N items into one collection shares collection_id → one lookup, not N).
    assoc_cache: dict[str, list[DeliverAssociation]] = {}
    matches: list[Match] = []
    # association_id → batch payload (preserves association + item order).
    batches: dict[str, dict[str, Any]] = {}

    for event in events:
        # Deletions never propagate to destinations (ROADMAP §6.4) — drain only.
        if event.op == "delete":
            continue
        item = await repo.get_item(event.collection_id, event.item_id)
        if item is None:
            # Race: the outbox row beat the item's visibility. Best-effort skip;
            # a subsequent event re-drives it.
            logger.warning(
                "dispatch: item not found for event",
                extra={"collection_id": event.collection_id, "item_id": event.item_id},
            )
            continue
        if event.collection_id not in assoc_cache:
            assoc_cache[event.collection_id] = await repo.list_deliver_associations(
                event.collection_id
            )
        occurred = event.occurred_at.isoformat() if event.occurred_at else None
        item_matches = match_item(item, assoc_cache[event.collection_id])
        for m in item_matches:
            batch = batches.setdefault(
                m.association_id,
                {"association_id": m.association_id, "items": []},
            )
            batch["items"].append(
                {
                    "item_id": m.item_id,
                    "asset_keys": list(m.asset_keys),
                    "item_created_at": occurred,
                }
            )
        matches.extend(item_matches)

    if batches:
        await enqueue(list(batches.values()))
    await repo.mark_processed([e.id for e in events])
    return matches
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/pipeline && python -m pytest tests/test_dispatch_loop.py -q && ruff check src/pipeline/dispatcher/`
Expected: PASS (5 passed), ruff clean.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/dispatcher/loop.py services/pipeline/src/pipeline/dispatcher/repo.py services/pipeline/tests/test_dispatch_loop.py
git commit -m "feat(phase5): dispatcher groups matches + enqueues delivery batches (Slice B-i)"
```

---

## Task 7: Deliver job handler + wiring (`jobs/dispatch.py`)

**Files:**
- Modify: `services/pipeline/src/pipeline/jobs/dispatch.py` — real enqueue in `dispatch_poll`; new `pipeline.deliver` task handler.
- Test: `services/pipeline/tests/test_delivery_jobs.py` (new).

**Interfaces:**
- Consumes: `dispatch_once` (Task 6), `PgDeliveryRepo`/`DeliverTarget` (Task 4), `deliver_item` (Task 5), `build_adapter`/`AdapterBuildError`, `build_platform_client`, `parse_delivery_config`, `load_key_or_skip`.
- Produces: module constant `JOB_DELIVER = "pipeline.deliver"`; the `deliver(association_id, items)` handler registered on the queue. Consumed by `main.build_queue` (already calls `dispatch.register`).

- [ ] **Step 1: Write the failing tests**

Create `services/pipeline/tests/test_delivery_jobs.py`:

```python
import pytest

from pipeline.config import Settings
from pipeline.delivery.repo import DeliverTarget
from pipeline.jobs import dispatch
from pipeline.jobs.dispatch import JOB_DELIVER, JOB_DISPATCH_POLL
from pipeline.main import build_queue
from pipeline.queue.memory import InMemoryQueue

pytestmark = pytest.mark.asyncio


def test_register_wires_dispatch_poll_and_deliver_task():
    queue = InMemoryQueue()
    dispatch.register(queue, Settings.from_env(env={}))
    assert JOB_DISPATCH_POLL in queue.periodic
    assert JOB_DELIVER in queue.tasks


def test_build_queue_includes_deliver_task():
    queue = build_queue(Settings.from_env(env={}))
    assert JOB_DELIVER in set(queue.app.tasks)


async def test_deliver_handler_calls_worker_per_item(monkeypatch):
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    dispatch.register(queue, settings)

    target = DeliverTarget(id="a1", collection_id="col", config={"path_template": "{filename}"}, connection=object())

    class _Repo:
        def __init__(self, _url): ...
        async def load_target(self, _aid):
            return target
        async def get_item(self, _c, item_id):
            return {"id": item_id, "collection": "col", "properties": {}, "assets": {}}

    calls: list[str] = []

    async def _fake_deliver_item(_repo, _adapter, _s3, _bucket, *, target, config, item, asset_keys, item_created_at):
        calls.append(item["id"])

    monkeypatch.setattr(dispatch, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", _Repo)
    monkeypatch.setattr(dispatch, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(dispatch, "build_platform_client", lambda _s: object())
    monkeypatch.setattr(dispatch, "deliver_item", _fake_deliver_item)

    await queue.tasks[JOB_DELIVER](
        association_id="a1",
        items=[
            {"item_id": "i1", "asset_keys": ["data"], "item_created_at": None},
            {"item_id": "i2", "asset_keys": ["data"], "item_created_at": None},
        ],
    )
    assert calls == ["i1", "i2"]


async def test_deliver_handler_noops_when_target_gone(monkeypatch):
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    dispatch.register(queue, settings)

    class _Repo:
        def __init__(self, _url): ...
        async def load_target(self, _aid):
            return None  # disabled/deleted between dispatch and delivery

    monkeypatch.setattr(dispatch, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", _Repo)

    # must not raise
    await queue.tasks[JOB_DELIVER](association_id="a1", items=[{"item_id": "i1", "asset_keys": [], "item_created_at": None}])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/pipeline && python -m pytest tests/test_delivery_jobs.py -q`
Expected: FAIL — `cannot import name 'JOB_DELIVER'` / `deliver` task not registered.

- [ ] **Step 3: Rewrite `jobs/dispatch.py`**

Replace the full contents of `services/pipeline/src/pipeline/jobs/dispatch.py` with:

```python
"""Delivery dispatch wiring (Slice B-i: poll-driven, real byte transfer).

``dispatch_poll`` drains the item_events outbox each minute via ``dispatch_once``,
which groups matches per association and enqueues a batched ``pipeline.deliver``
job. The ``deliver`` handler loads the destination connection, builds its adapter,
and runs each item through ``deliver_item`` (canonical bytes → destination,
recorded in ``delivery_log``). Slice C swaps the poll for a LISTEN-woken loop;
retry → dead-letter and payload/on_update policy are B-ii/B-iii.
"""

from __future__ import annotations

import logging
from typing import Any

from pipeline.config import Settings
from pipeline.connections.build import AdapterBuildError, build_adapter
from pipeline.delivery.config import parse_delivery_config
from pipeline.delivery.repo import PgDeliveryRepo
from pipeline.delivery.worker import deliver_item
from pipeline.dispatcher.loop import dispatch_once
from pipeline.dispatcher.repo import PgDispatchRepo
from pipeline.jobs._common import load_key_or_skip
from pipeline.queue.interface import QueueBackend
from pipeline.storage.platform import build_platform_client

logger = logging.getLogger(__name__)

JOB_DISPATCH_POLL = "pipeline.dispatch_poll"
JOB_DELIVER = "pipeline.deliver"
CRON = "* * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def dispatch_poll(timestamp: int) -> None:
        repo = PgDispatchRepo(settings.database_url)

        async def _enqueue(batches: list[dict[str, Any]]) -> None:
            await queue.enqueue_batch(JOB_DELIVER, batches)

        matches = await dispatch_once(repo, _enqueue)
        if matches:
            logger.info(
                "dispatch poll enqueued delivery batches",
                extra={"matches": len(matches), "scheduled_timestamp": timestamp},
            )

    async def deliver(association_id: str, items: list[dict[str, Any]]) -> None:
        master_key = load_key_or_skip(settings, JOB_DELIVER)
        if master_key is None:
            return
        repo = PgDeliveryRepo(settings.database_url)
        target = await repo.load_target(association_id)
        if target is None:
            # Association disabled/deleted between dispatch and delivery — no-op.
            return
        try:
            adapter = build_adapter(
                target.connection, master_key, settings.egress_allow_hosts
            )
        except AdapterBuildError:
            logger.exception(
                "deliver: adapter build failed",
                extra={"association_id": association_id},
            )
            return
        config = parse_delivery_config(target.config)
        s3_client = build_platform_client(settings)
        for entry in items:
            item = await repo.get_item(target.collection_id, entry["item_id"])
            if item is None:
                continue
            await deliver_item(
                repo,
                adapter,
                s3_client,
                settings.staging_bucket,
                target=target,
                config=config,
                item=item,
                asset_keys=entry["asset_keys"],
                item_created_at=entry.get("item_created_at"),
            )

    queue.register_periodic(dispatch_poll, name=JOB_DISPATCH_POLL, cron=CRON)
    queue.register_task(deliver, name=JOB_DELIVER)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/pipeline && python -m pytest tests/test_delivery_jobs.py tests/test_main_jobs.py -q && ruff check src/pipeline/jobs/dispatch.py tests/test_delivery_jobs.py`
Expected: PASS (test_delivery_jobs 4 passed; test_main_jobs still green — its assertions use `<=`/`in`, unaffected), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/jobs/dispatch.py services/pipeline/tests/test_delivery_jobs.py
git commit -m "feat(phase5): deliver job handler — dispatch enqueues real transfers (Slice B-i)"
```

---

## Task 8: Full verification, docs, and merge

**Files:**
- Modify: `ROADMAP.md` (Phase 5 status row + Slice B line).
- Modify: `docs/FEATURES.md` (delivery entry).
- Modify: `docs/ISSUES.md` (log B-i deferrals as tracked items).
- Modify: `docs/decisions/` (only if a new ADR is warranted — see Step 4).

- [ ] **Step 1: Run the full pipeline unit suite**

Run: `cd services/pipeline && python -m pytest -q && ruff check src tests`
Expected: PASS — all prior tests plus the new delivery suites green (the Slice A count was 234 passed / 2 skipped; this slice adds path/adapter-move/repo/worker/dispatch/jobs tests). ruff clean.

- [ ] **Step 2: Run the app verify gate**

Run (repo root): `npm run verify`
Expected: PASS (app build + vitest). Covers migration 008 compiling into `migrate.ts`.

- [ ] **Step 3: Live end-to-end verification**

This is the done-when gate. It needs the Docker stack (the lead/human runs it — singleton backend). Sequence:

```bash
# 1. Bring up the stack (from repo root)
docker compose up -d --wait
# 2. In the app, create an S3/MinIO *destination* connection (protocol s3,
#    bucket e.g. "delivery-dest", the MinIO endpoint) and a `deliver` association
#    on a built-in-catalog collection that already has an itemized item
#    (reuse the Phase-4 raster_auto item), path_template
#    "{collection}/{yyyy}/{mm}/{dd}/{item_id}/{filename}", asset_keys null.
#    (Create the destination bucket in MinIO first.)
# 3. Touch the item so the outbox fires (re-upsert via the pipeline OR a no-op
#    PUT through the catalog), OR insert a fresh item.
# 4. Within one dispatch poll cycle, confirm:
```

Assert:
- The MinIO destination bucket contains the asset object at the rendered key (`{collection}/{yyyy}/{mm}/{dd}/{item_id}/{filename}`), byte-identical to the canonical object (compare sha256).
- `SELECT status, bytes, attempts FROM stac_higher.delivery_log` shows one row `status='delivered'`, `bytes` = the asset size, `attempts=1`.
- A second event for the same item UPSERTs the same row (no duplicate; `attempts` increments).

Record the observed evidence (object key, byte count, row) in the ROADMAP note. If a live gap surfaces, log it as an ISSUES item and fix on this branch before merge.

- [ ] **Step 4: Update docs**

- `ROADMAP.md`: flip the Phase 5 Slice B line to note **Slice B-i done** (canonical-bytes S3 delivery, `delivery_log`, path templates, atomic `put_atomic`, dispatcher→worker fan-out), list what B-ii/B-iii still carry (payloads, on_update/overwrite, reference/S3→S3 copy, retry/dead-letter, concurrency, live SFTP/FTP), and record the live-verification evidence + date.
- `docs/FEATURES.md`: add a Phase 5 delivery entry with entry points (`delivery/worker.py`, `delivery/path.py`, `jobs/dispatch.py`, migration 008).
- `docs/ISSUES.md`: log the B-i deferrals as tracked items (reference-mode delivery source, S3→S3 copy, retry/dead-letter, per-connection concurrency, live SFTP/FTP destination coverage, date-token-less items failing date templates).
- No new ADR is required (Slice B-i introduces no cross-cutting reversible decision beyond ADR 0007). Add one only if the live run forces a mechanism change.

- [ ] **Step 5: Commit docs**

```bash
git add ROADMAP.md docs/FEATURES.md docs/ISSUES.md
git commit -m "docs(phase5): record Slice B-i delivery worker — done + deferrals"
```

- [ ] **Step 6: Merge to `ai/main`**

From the main checkout (not the worktree), after verify passes:

```bash
git checkout ai/main
git merge ai/phase5-slice-bi --no-ff -m "Merge Slice B-i: Phase 5 delivery worker (canonical → S3 destination)"
npm run verify   # re-run on the merge commit
```

Then remove the worktree + branch per AGENTS.md, and (per memory `keep-ai-main-local`) do NOT push `ai/main` to origin.

---

## Self-Review

**Spec coverage:**
- delivery_log table → Task 1. ✓
- Path templates (`{collection}{item_id}{filename}{yyyy}{mm}{dd}`, date-token fail-loud) → Task 2. ✓
- Adapter `move` + `put_atomic` (S3 direct PUT, SFTP/FTP rename) → Task 3. ✓
- Delivery repo + fake → Task 4. ✓
- Worker (canonical bytes → dest, delivery_log transitions, per-item failure isolation) → Task 5. ✓
- Dispatcher grouping/enqueue (batch-oriented, enqueue-before-drain) → Task 6. ✓
- Deliver job handler + wiring → Task 7. ✓
- Done-when live verification + docs + merge → Task 8. ✓
- Deferrals (payloads, on_update/overwrite, reference/S3→S3, retry, concurrency, live SFTP/FTP) — explicitly NOT built, logged in Task 8 Step 4. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code and exact commands. ✓

**Type consistency:** `deliver_item` signature (Task 5) matches the call in Task 7. `DeliveryRepo` methods (Task 4) match fake (Task 4) and worker/handler usage (Tasks 5, 7). `dispatch_once(repo, enqueue, *, batch_size)` (Task 6) matches the caller in Task 7. Batch payload shape `{association_id, items:[{item_id, asset_keys, item_created_at}]}` is identical in Task 6 (produced), Task 6 tests, and Task 7 handler (consumed). `ItemEvent.occurred_at` default `None` keeps existing `FakeDispatchRepo`/tests valid. ✓
