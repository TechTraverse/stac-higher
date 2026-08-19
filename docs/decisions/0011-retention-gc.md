# ADR 0011 — Retention & GC: one marked-then-collected queue

**Status:** accepted (M2-F)

## Context

ROADMAP §6.5 promises that an expired item "leaves the catalog and, after the
grace window, object storage", and ADR 0009 shipped collection-delete warnings
whose GC half could not yet be true (I-51: deleting a collection orphaned its
canonical bytes). Four paths can schedule canonical asset bytes for deletion —
retention expiry, manual item delete, collection delete, and ADR 0009's
`archived` state — and before M2-F none of them actually removed bytes.

## Decision

**One queue.** `stac_higher.asset_gc` (migration 017) is the single
marked-then-collected ledger for every path: `object_key` (a key **prefix**
under the platform bucket — the §5.3 layout nests each item's assets under
`assets/{collection}/{item}/`, so one row covers an item, and
`assets/{collection}/` covers a whole collection), `reason ∈ {retention,
item_delete, collection_delete, archive}`, `collect_after = mark time + the
collection's gc_grace_days`, `collected_at`, `error`. A partial unique index
on open keys makes re-marking idempotent; a collected key can be marked again
(item re-created, re-deleted). Both runtimes write rows; only the app writes
DDL (ADR 0001).

**Two pipeline sweeps** (five-minute cadence, batched by `GC_BATCH_ITEMS`):

- `pipeline.retention_gc` — for each collection with declared
  `retention_days` (or `archived`): **mark first, then delete** each expired
  item (`pgstac.delete_item`, the same item-data call the app's deletion path
  makes). Mark-first means a crash never orphans bytes — the reverse order is
  exactly the I-51 bug. The emitted `delete` outbox events are dropped by the
  dispatcher without matching (§6.4: deletions never propagate to delivery
  destinations — delivered files are the consumer's copy).
- `pipeline.asset_collect` — deletes everything under each due mark's prefix
  (`delete_prefix`, which refuses unscoped prefixes) and stamps
  `collected_at`. Storage errors keep the mark open for retry; an empty
  prefix (reference-mode item) is a normal zero.

**App-side marks at the source of truth.** The BFF catalog route marks after
each successful delete: item delete → the item prefix (`item_delete`),
collection delete → the collection prefix (`collection_delete` — this closes
I-51's GC half). Marks are best-effort *after* upstream success: the catalog
delete cannot be rolled back, so a failed mark logs loudly instead of failing
the request.

**Safety (spec §5.3).** Nothing is deleted on an unconfigured platform
(`retention_days` null + not archived ⇒ the sweeps never touch the
collection). The Settings tab shows a **counted dry-run**
(`GET /api/collections/[id]/settings/impact`) in a warn-and-proceed dialog
before any save that starts deleting — enabling/tightening retention, or
archiving. The grace window keeps oops-deletes recoverable; `gc_grace_days =
0` is allowed (the rehearsal's shortened-grace path). Settings PUTs are
audited (M2-E).

**Archived, made concrete** (ADR 0009 §5: "delete the data, keep the
record"): the retention sweep expires **every** item of an archived
collection (reason `archive`, age irrelevant); the BFF refuses new item
writes into it (409) while metadata edits and deletes stay allowed; the
associations route refuses new associations targeting it (409).

**Reference-mode ASSOCIATION delete, settled** (the ADR 0009 open question):
deleting a reference-mode ingest association now **removes its
reference-backed items** — the same "unmanaged dead links" rationale as
connection delete: with the flow gone there is no update path and no source
guarantee. The delete dialog counts them and points at disable-instead-of-
delete for keeping them. Reference items store no canonical bytes, so no GC
mark is involved.

## Consequences

- Retention keys on `pgstac.items.datetime` — observation time, not ingest
  time. An item without a usable datetime never expires by age.
- Prefix-based marks mean per-asset granularity does not exist; the unit of
  collection is the item (or collection). That matches the asset route's
  §5.3 layout and keeps the queue small.
- The collector deletes under prefixes in the PLATFORM bucket only; nothing
  ever touches per-connection (destination) storage.
- `asset_gc` rows are retained after collection as a small audit trail of
  byte deletions; M2-G's retention sweeps may prune them later.
- Restoring an item within the grace window = re-ingest or re-create it; the
  open mark for its prefix will still collect the OLD bytes when due, and
  newly written objects under the same prefix would be deleted with them —
  operators should clear the mark (or wait past collection) before re-using
  an item id inside the grace window. Recorded as a residual in ISSUES.
