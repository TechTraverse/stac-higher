# Phase 5 Slice B-ii — delivery payloads, update/overwrite policy, reference source, S3→S3 copy

**Date:** 2026-07-22
**Status:** Approved
**Builds on:** Slice B-i (`2026-07-21-phase5-slice-bi-delivery-workers-design.md`), ADR 0007, ROADMAP §5.1/§6.4

## Goal

Make the delivery worker honor the parts of the §5.1 delivery config that B-i
parsed but ignored — `payload` sidecars, `on_update`, `overwrite` — and extend
the byte-moving path to reference-mode sources and S3→S3 server-side copy.
B-i delivered every event, overwrite-always, canonical-bytes-only, assets-only.

## Scope

**In:**
- Payload sidecars: STAC item JSON, per-file checksums, completion marker.
- `on_update: redeliver | ignore` enforcement (item-level gate).
- `overwrite: never | always | if_newer` enforcement (per-asset gate).
- Per-asset delivered fingerprints in `delivery_log` (the change-detection
  substrate for both gates).
- Reference-mode source: deliver assets whose bytes live at the ingest source
  (`ingest_files.source_href`), not in canonical storage.
- S3→S3 server-side copy (canonical → s3 destination, same endpoint), with
  streaming fallback.
- Opportunistic: reset `attempts = 0` on redelivery upsert (**resolves I-44**).

**Out (B-iii):** retry sweep → dead-letter, `next_attempt_at`, `max_attempts`
enforcement, per-connection concurrency caps (`max_concurrent_transfers`),
live SFTP/FTP destination runs (I-45).
**Out (C/D):** NOTIFY-woken dispatch, backfill, Data-flow delivery UI.

## Decisions (settled in brainstorming)

1. **Change detection — delivery_log fingerprints.** A new
   `delivered_assets` jsonb column on `delivery_log` stores per-asset
   fingerprints captured at delivery time. Works for all write paths (ingest,
   UI, direct API) and never depends on the outbox `op` (I-37/I-46) or the
   ingest ledger.
2. **Overwrite semantics — log-based.** All three modes decide from our own
   `delivered_assets`, never from a destination round-trip. Consistent with
   the "destination drift is accepted" design stance; a consumer-deleted file
   is not re-sent under `never`/unchanged-`if_newer`.
3. **Reference source — ledger-first lookup, read via the source adapter.**
   Query `ingest_files` by `item_id` before fetching: a row with `source_href`
   ⇒ reference asset, whose bytes are read through the **ingest source
   connection's adapter** (`build_adapter` → `adapter.get`, the Phase-4
   `SourceAdapterByteSource` pattern — reuses egress policy + credentials; the
   pipeline deliberately has no HTTP client). Else canonical `get_object`.
   Deterministic, no 404-driven control flow. *(Amended from "HTTP GET of
   source_href" during planning: the established reference-read mechanism is
   the source adapter; `source_href` serves as the reference-mode flag.)*
4. **S3→S3 copy — same-endpoint gate with fallback.** Attempt `CopyObject`
   only when the destination is `s3` and its normalized endpoint equals the
   platform `S3_ENDPOINT`; any failure falls back to streaming.

## Design

### Schema — migration 009 (app DDL, ADR 0001)

```sql
ALTER TABLE stac_higher.delivery_log
  ADD COLUMN delivered_assets jsonb NOT NULL DEFAULT '{}';
```

Shape: `{asset_key: {"fingerprint": "...", "size": n, "filename": "..."}}`
(`filename` recorded so the completion manifest can list current-but-skipped
assets without re-resolving hrefs).

- `fingerprint` is `sha256:<hex>` when the worker streamed the bytes,
  `etag:<etag>/<size>` when it server-side copied (from `head_object` on the
  canonical object).
- Fingerprints of different kinds compare as *changed* — worst case a
  redundant redeliver, consistent with at-least-once (I-43).

`upsert_pending`'s `ON CONFLICT` branch additionally sets `attempts = 0`: a
new event starts a new attempt cycle. The B-iii retry sweep re-drives `failed`
rows without passing through `upsert_pending`, so retry counting is
unaffected. **Resolves I-44.**

### Worker flow (`deliver_item`)

1. **Read prior state** — new repo method `get_row(association_id, item_id)`
   returning prior status + `delivered_assets` (or `None`). Called *before*
   `upsert_pending`, which resets status.
2. **`on_update` gate** — prior row exists with status `delivered` and
   `on_update: ignore` → consume the event, write nothing, leave the row
   untouched (delivery is fire-once-per-item). Prior `pending`/`failed` always
   proceeds — the item never fully delivered. First delivery (no prior row)
   always proceeds.
3. **Per asset** (for each matched asset key):
   a. **Resolve source** (ledger-first): `ingest_files` row with `source_href`
      ⇒ reference source — build the ingest connection's adapter and
      `adapter.get` the source path (adapters built once per distinct
      connection per item); else canonical
      (`assets/{collection}/{item_id}/{filename}`).
   b. **Fingerprint** the current source: sha256 over streamed bytes, or
      canonical `head_object` ETag+size on the server-side-copy path.
   c. **`overwrite` gate**: `always` → write; `never` → skip if the asset key
      appears in prior `delivered_assets`; `if_newer` → write only if the
      fingerprint differs from the prior one.
   d. **Transfer**: server-side `CopyObject` when (destination protocol is
      `s3`) ∧ (destination endpoint == platform `S3_ENDPOINT`, normalized
      scheme/host/port) ∧ (source is canonical) ∧ (no checksum sidecar
      requires streamed bytes — see payload rules); on copy failure, log +
      fall back to streaming `put_atomic`. All other cases stream as in B-i.
4. **Payload** (only when something was written this cycle, except item JSON):
   - Per-file checksum sidecar immediately after each written asset.
   - Item JSON rewritten on **every processed event** when enabled — item
     metadata can change with no asset change.
   - Completion marker written **last** (§6.4), only if any file (asset,
     checksum, or item JSON) was written this cycle.
5. **`mark_delivered`** persists the merged `delivered_assets` (skipped assets
   keep their prior entries) plus total bytes written (assets + sidecars).

Failure isolation unchanged from B-i: any exception → `mark_failed`, no
re-raise, batch continues.

### Payload conventions

All sidecars render through the association's `path_template` with the
sidecar's own filename substituted for `{filename}` (same directory as the
assets for the canonical `.../{item_id}/{filename}` templates).

| Sidecar | Filename | Content |
|---|---|---|
| Item JSON | `{item_id}.json` | The pgstac item verbatim — hrefs stay `/api/assets/...` (resolvable through the asset service; no href rewriting this slice). |
| Checksums | `{filename}.sha256` / `{filename}.md5` | Coreutils format `<hex>  <filename>\n` — verifiable with `sha256sum -c` / `md5sum -c`. |
| Completion marker | `{item_id}.done` | JSON manifest: every asset delivered-or-current — `{key, filename, fingerprint, size}` — written last. |

**Checksums × server-side copy:** the copy path never sees the bytes. When
`payload.checksums` is enabled for an s3 destination, that asset streams
instead (honest checksum beats copy efficiency); exception: `md5` + a
single-part canonical ETag (plain MD5) may use the ETag and keep the copy.

### Contract & config

No cross-runtime contract change. `payload`, `on_update`, `overwrite` already
exist in both `deliveryConfigSchema` (Zod) and `delivery/config.py` with
matching defaults; this slice makes the pipeline honor them.
`retry.*` and `max_concurrent_transfers` stay parsed-but-unenforced (B-iii).

### New/changed seams

- `DeliveryRepo.get_row(association_id, item_id)` — prior status +
  `delivered_assets`.
- `DeliveryRepo.mark_delivered(row_id, byte_count, delivered_assets)` —
  extended signature.
- `DeliveryRepo.load_reference_sources(item_id)` over `ingest_files` (latest
  version per source file, `source_href IS NOT NULL`) — returns per-file
  `{filename, fetch_path, ingest connection}` so the worker can build the
  source adapter.
- `delivery/payload.py` — sidecar filename/content builders (pure functions,
  unit-testable without adapters).
- S3 endpoint comparison helper (normalize scheme/host/port) — placement near
  the platform storage config.

## Error handling

- Reference source adapter read failures → per-item `mark_failed` (isolation
  as today); egress policy violations fail loudly inside the adapter.
- `CopyObject` failure → warning log + streaming fallback (not a delivery
  failure).
- A vanished asset key (matched but absent at delivery time) → skip, deliver
  the rest (B-i behavior kept).
- Marker-last ordering guarantees a consumer that sees `{item_id}.done` sees
  every listed file.

## Testing

Unit (extend `FakeDeliveryRepo`, fake adapter/S3, fake ledger):
- `on_update` × prior-status matrix (none/pending/failed/delivered ×
  redeliver/ignore).
- `overwrite` never/always/if_newer × changed/unchanged/new fingerprints.
- delivered_assets merge (skipped assets keep prior entries; attempts reset).
- Reference-source routing (ledger hit vs miss), egress-policy enforcement.
- Server-side-copy gating (endpoint match/mismatch, checksum-forced streaming,
  fallback on copy error).
- Sidecar filenames/contents; ordering (marker last, only when written).

Live verification (before merge, per slice practice):
- MinIO→MinIO destination exercises real `CopyObject` (same endpoint).
- A reference-mode item delivers from its source URL, no canonical read.
- A metadata-only item update rewrites only `{item_id}.json`.
- `sha256sum -c` passes against delivered checksum sidecars.
- `on_update: ignore` association ignores a re-upsert; `overwrite: never`
  skips re-sending an unchanged asset.

Gates: full pipeline suite + `npm run verify` (migration 009 touches the
app); worktree `ai/deliver-b2` off `ai/main`.

## Residual risk / follow-ups

- I-43 (at-least-once, no dedup) unchanged — fingerprints make redundant
  redelivery cheaper but do not dedup concurrent dispatch.
- I-45 (SFTP/FTP `move()` inspection-only) unchanged — B-iii.
- Fingerprint-kind switching (stream ↔ copy) forces a one-time redundant
  redeliver — accepted.
- `attempts` reset lands ahead of its consumer (`max_attempts`, B-iii) —
  benign now, correct later.
