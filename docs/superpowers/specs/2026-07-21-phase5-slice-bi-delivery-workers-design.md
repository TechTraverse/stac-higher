# Phase 5 Slice B-i — Delivery worker (thin vertical slice)

**Date:** 2026-07-21
**Branch:** `ai/phase5-slice-bi` (off `ai/main`)
**Status:** design approved, pre-implementation

## Context

Phase 5 delivers the **delivery pipeline** (ROADMAP §6.4). Slice A (merged,
`7e0aab7`) shipped the *event outbox + dispatcher skeleton*: the durable
`item_events` outbox (migration 007 + a row-level trigger on `pgstac.items`),
plus `dispatcher/` which claims outbox rows in id order → reads the item via
`pgstac.get_item` → matches enabled `direction='deliver'` associations → applies
the CQL2 `item_filter` + `asset_keys`, and **logs the matched (item ×
association) pairs — no bytes move yet**. The delivery `config` (§5.1) already
ships as a cross-runtime contract (Zod `deliveryConfigSchema` +
`delivery/config.py`), and delivery associations are creatable through the
existing `/api/collections/[id]/connections` route.

Slice B as written in the ROADMAP is large (path templates, all payload options,
`on_update`/`overwrite`, S3→S3 server-side copy, `.part`→rename, per-connection
concurrency, retry→dead-letter, across S3 + SFTP + FTP destinations). Per the
delivery-by-slices norm, we take it as a **thin vertical slice first (B-i)**,
live-verified, then layer the rest as B-ii/B-iii.

## Goal (B-i)

A single item's asset(s) land on an **S3/MinIO destination** connection at the
templated path and are recorded in `delivery_log`, driven end-to-end through the
real dispatcher — replacing Slice A's "log the match" with an actual byte move.

### Done-when

An ingest- or UI-created item fires the outbox → the dispatcher enqueues a
`deliver` job → the worker copies its asset(s) to an S3/MinIO destination
connection at the rendered path → `delivery_log` shows one `delivered` row with a
byte count. Live-verified against `docker compose up` + a MinIO destination
connection.

## Approach

**Aligned thin slice** (chosen over a minimal one-job-per-item variant and an
ingest-style multi-stage chain):

- Respect the locked **batch-oriented jobs** decision (ROADMAP §1, §6.4) from the
  start — the dispatcher enqueues **one delivery job per (association, N items)**,
  not one per item.
- Atomic visibility via a new `move()` on the adapter interface; `S3Adapter`
  overrides `put_atomic` to a direct atomic PUT (no `.part` dance for S3).
- `delivery_log` shaped to the §5 ERD so B-ii/B-iii add *code*, not migrations.

Rationale: barely more scaffolding than the minimal variant, and it avoids
re-touching the dispatcher→worker handoff and re-migrating `delivery_log` when
retry/concurrency/redelivery land.

## Components

### 1. Migration 008 — `stac_higher.delivery_log`

App owns the DDL (ADR 0001); the pipeline only writes rows. Matches the §5 ERD:

- `id uuid PK`, `association_id uuid NOT NULL REFERENCES collection_connections(id) ON DELETE CASCADE`
- `item_id text NOT NULL`
- `status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','delivering','delivered','failed','dead'))`
- `attempts int NOT NULL DEFAULT 0`
- `bytes bigint`
- `error text` (observability for `failed`)
- `item_created_at timestamptz`
- `delivered_at timestamptz`
- `created_at`, `updated_at timestamptz NOT NULL DEFAULT now()`
- `UNIQUE (association_id, item_id)` — the idempotency key. A later event for the
  same item UPSERTs this row; **this is how B-ii derives first-delivery vs.
  redelivery** (Slice A note: never from the outbox `op`, since pgstac updates
  surface as delete+insert).

Indexes: `(association_id)`; a partial index on `status` reserved for the B-iii
retry sweep. Phase-6 time-partitioning is deferred with the same comment as
`ingest_files` / `audit_log`. `next_attempt_at` is **not** added here — it lands
with the B-iii retry sweep (additive migration, mirrors 006 adding `source_href`).

### 2. `delivery/path.py` — path-template renderer

Pure `render_path(template, item, filename) -> str` over the tokens documented in
`deliveryConfigSchema`: `{collection} {item_id} {filename} {yyyy} {mm} {dd}`.
Date tokens resolve from the item's `properties.datetime`, falling back to
`start_datetime` (both parsed as UTC). If a template references a date token and
the item has neither, rendering **raises** (delivery fails loudly rather than
writing to a wrong path). No I/O; fully unit-tested.

### 3. Adapter interface — `move(src, dst)` + `put_atomic`

`StorageAdapter` grows:

- `async def move(self, src: str, dst: str) -> None` (abstract): SFTP/FTP
  implement server-side rename; `S3Adapter` implements copy + delete.
- `async def put_atomic(self, path: str, data: bytes) -> None` (concrete default):
  `put(path + ".part")` then `move(path + ".part", path)`. `S3Adapter` overrides
  it to a direct `put(path, data)` — S3 objects become visible atomically on PUT,
  so the `.part` rename is unnecessary and would cost an extra copy+delete.

B-i exercises only the S3 `put_atomic` path live; SFTP/FTP get correct `move`
implementations now (unit-tested), live-verified in B-ii/B-iii.

### 4. `delivery/repo.py` — delivery persistence + association load

`DeliveryRepo` ABC (with an in-memory fake for unit tests) + `PgDeliveryRepo`:

- `upsert_pending(association_id, item_id, item_created_at) -> row_id`
- `mark_delivering(row_id)`
- `mark_delivered(row_id, byte_count)`
- `mark_failed(row_id, error)`
- `load_association_with_connection(association_id)` — mirrors ingest's
  `_load_association` so the worker can `build_adapter` for the **destination**
  connection (the dispatcher's `list_deliver_associations` returns config only).

Pg methods open a short-lived `AsyncConnection`, `# pragma: no cover`, exercised
by the live verification (as with `PgDispatchRepo`).

### 5. `delivery/worker.py` — the byte move

Orchestrates one `(association, item, asset_keys)` delivery:

1. `upsert_pending` → `mark_delivering`.
2. For each asset key: read **canonical** bytes via
   `platform.get_object(s3, bucket, canonical_asset_key(collection, item_id, filename))`,
   `render_path(config.path_template, item, filename)`, then
   `adapter.put_atomic(dest_path, data)`; accumulate byte count.
3. `mark_delivered(bytes)`; any exception → `mark_failed(error)`.

**B-i is canonical-bytes-stream only.** Reference-mode source resolution and
S3→S3 server-side copy are B-ii.

### 6. Dispatcher → worker wiring

`dispatch_once` stops logging matches and instead **groups matches by
`association_id`** and enqueues one `pipeline.deliver` job per association,
carrying `[{item_id, asset_keys, item_created_at}]`. The outbox batch is marked
processed **only after** the enqueue succeeds. A new `deliver` task handler
(loads association+connection, builds the destination adapter + platform client,
loops its items through the worker) is registered in `jobs/dispatch.py` and wired
in `main.py`.

## Explicitly deferred (B-ii / B-iii)

- Payload options: item JSON sidecar, per-file checksums, completion marker.
- `on_update` (redeliver changed-checksum assets only) / `overwrite`
  (never|always|if_newer). **B-i delivers on every event, overwrite-always.**
- Reference-mode source resolution + S3→S3 server-side copy.
- Retry → dead-letter (app-managed sweep over `delivery_log`; `next_attempt_at`
  column added then). The `QueueBackend` interface exposes no retry primitive, so
  retry is deliberately app-managed and backend-agnostic (survives the Phase 8
  SQS backend), mirroring the `connection_checks` drain.
- Per-connection `max_concurrent_transfers` caps.
- Live SFTP + FTP destination verification (the `move` code lands in B-i, unit
  tested; live runs are B-ii/B-iii).

## Testing / verification

- App: `npm run verify` (build + unit) — covers migration 008 applying cleanly.
- Pipeline: `pytest` unit suite — pure `render_path`, worker over the fake repo +
  a fake adapter/S3 client, dispatcher grouping/enqueue, adapter `move`/`put_atomic`.
- Live: `docker compose up`, create a MinIO **destination** connection + a
  `deliver` association, drive an item through, assert the object appears at the
  templated key and `delivery_log` has one `delivered` row with the byte count.

## Risks / notes

- Date-token rendering depends on item datetime; items with neither `datetime`
  nor `start_datetime` fail a date-token template by design — surface clearly.
- One `delivery_log` row per (association, item) means redelivery history is not
  retained (attempts counter + `delivered_at` overwrite in place). This matches
  the §5 ERD; per-attempt history is not a done-when requirement.
- Worker buffers whole assets in memory (same as ingest FETCH, ISSUES I-19);
  streaming/multipart stays deferred.
