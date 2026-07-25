# ADR 0009 — Deletion semantics: soft-delete, retained history, warn-and-proceed

- **Status:** accepted (decision); implementation pending — see
  [ISSUES.md](../ISSUES.md) I-51
- **Owners:** app control plane (connections/associations routes, migrations,
  UI dialogs) + pipeline (scheduler/dispatcher filters, GC in Phase 6)
- **Related:** ADR 0003 (pre-existing collections), ADR 0005 (asset service),
  ROADMAP §6.5 (retention & GC); I-32 (private-source reference), I-50

## Context

The 2026-07-22 architecture review confirmed that the current `ON DELETE
CASCADE` chains (connections → `collection_connections` → `ingest_files` +
`delivery_log`, migrations 005/008) make a routine, one-call operator DELETE
silently destructive:

- **Provenance is destroyed.** Per-file checksums, versions, source paths, and
  delivery history — records the platform's compliance posture treats as
  durable — vanish with the connection. They are also quietly *functional*:
  `delivered_assets` fingerprints drive delivery change-detection, and a prior
  `delivery_log` row is how `on_update` distinguishes first-delivery from
  redelivery.
- **Reference-mode items break.** Their assets resolve at read time from
  `ingest_files.source_href` (`lookupReferenceHref`); cascading the ledger away
  leaves live catalog items whose asset route falls through to presigning a
  canonical object that never existed.

Two clarifications shaped the decision:

1. **Reference reads do not need the connection.** `source_href` is a stable,
   credential-free URL; the asset route never touches the connection or its
   credentials for reads (today — private-source reference, I-32, would change
   this). What connection deletion genuinely ends for reference-backed items is
   the *managed relationship*: no more polling/version updates, no delivery
   (delivery reads source bytes through the connection's credentials), and no
   guarantee the source keeps the files.
2. **Reference mode is per-association/per-item, not per-collection.** A
   collection can hold canonical-backed and reference-backed items at once, so
   impact must be counted in items, not labeled on collections.

The product owner's direction (2026-07-24): deletion stays one easy action;
history is kept; **warn-and-proceed** everywhere (no require-cleanup-first
gates); reference-backed items are **removed, not left dangling** — for
mission-critical subscribers, best-effort dead links are worse than honest
absence.

## Decision

1. **Connections and associations soft-delete.** DELETE sets `deleted_at`
   (rows leave all listings and API reads); nothing cascades into history
   tables. The stored **credential envelope and host-key pin are scrubbed
   immediately** on soft-delete — record-keeping never justifies retaining
   secrets. Belt-and-braces: the `ingest_files`/`delivery_log` FKs change
   `CASCADE` → `RESTRICT` so a future hard-delete path cannot silently destroy
   history either.
2. **History rows are passive records and are retained.** `ingest_files`,
   `delivery_log`, and `connection_checks` rows survive deletion of their
   parents. No per-row disabling is needed: all data movement is driven by
   live, enabled associations — the scheduler, dispatcher, and delivery
   reference-source loader must filter on *not-deleted AND enabled*, so
   activity stops the moment the parent is soft-deleted. Row retention/pruning
   remains the Phase 6 partitioning/GC concern (I-11, I-12, I-36).
3. **Deleting a connection with reference-backed items removes those items —
   after an informed warning.** The impact preview counts them per collection
   ("312 of 40,000 items in `goes-west` are reference-backed through this
   connection; they will be removed from the catalog"). On proceed, those
   items are deleted from pgstac (they have no canonical bytes, so no storage
   cleanup is needed; their ledger rows are retained as provenance, marked so
   the asset route no longer resolves them). Copy-mode items are untouched —
   the platform owns their bytes and keeps serving them. Deleting a
   *delivery*-direction connection stops future deliveries to that
   destination; **already-delivered files are never removed** (§6.4 — drift is
   accepted by design).
4. **Deleting a collection warns with the real data impact.** The dialog
   states what is actually destroyed: "N items and M data files (X GB) in
   platform storage will be deleted." On proceed, items leave the catalog and
   canonical assets follow the §6.5 marked-then-collected GC path (removed
   after the grace window). Until Phase 6 GC lands, collection deletion
   orphans canonical bytes in object storage — tracked in I-51 so the warning
   is not shipped as a promise the platform can't yet keep.
5. **Collections gain an `archived` state** (`collection_settings.archived`,
   Phase 6): items and canonical assets are removed via the same GC path, but
   the collection's metadata, settings, audit trail, and provenance ledger are
   retained, and no new associations may target it. Archive = "delete the
   data, keep the record"; delete = "remove the collection entirely."
6. **Warn-and-proceed is the uniform policy.** Every destructive action shows
   a concrete, counted impact preview and then does what it said. Hard
   blocks are reserved for cases where proceeding would corrupt state rather
   than remove it — none exist today; private-source reference (I-32) would
   introduce one and must revisit this ADR.

## Rejected alternatives

- **Keep the CASCADE chains.** Routine operations silently destroying
  provenance and breaking live catalog items is exactly what the review
  flagged; incompatible with the append-only audit posture.
- **Require-cleanup-first (409 until dependents are removed).** Considered and
  initially favored, then rejected by the product owner: deletion should stay
  one decisive action, with the warning carrying the informed-consent weight.
- **Leave reference-backed items serving best-effort after connection
  deletion.** Technically viable (reads survive on the recorded public URL)
  but rejected: unmanaged items with no update path and no source guarantee
  are dead links waiting to happen in front of mission-critical subscribers.
- **Hard-delete with an archive table.** Copying history into shadow tables
  duplicates schema and loses referential simplicity; `deleted_at` filters are
  cheaper and keep one source of truth.

## Consequences

- **Migrations:** `deleted_at` on `connections` and `collection_connections`;
  FK changes CASCADE → RESTRICT; a marker on `ingest_files` rows whose items
  were removed with their connection (so reference resolution excludes them).
- **Routes:** connection/association DELETE becomes soft-delete + credential
  scrub; DELETE responses (and a pre-flight impact endpoint for the UI) return
  the counted blast radius; all list/read queries filter `deleted_at IS NULL`.
- **Pipeline:** scheduler, dispatcher matcher, and the delivery
  reference-source loader add not-deleted filters (enabled-gating already
  exists); reference-item removal on connection delete needs a small bulk
  delete through pgstac (emits outbox `delete` events, which drain without
  propagating — consistent with §6.4).
- **UI:** confirm dialogs gain impact previews (counts, byte totals) for
  connection, association, and collection deletion.
- **Unique constraints** that could collide with soft-deleted rows (e.g. the
  `(collection, connection, direction)` association uniqueness) must become
  partial indexes on `deleted_at IS NULL` so a replacement can be created
  after a delete.
- Names/configs of soft-deleted rows remain queryable for audit
  reconstruction; secrets do not (scrubbed at delete time).

## Revisit

- **Private-source reference (I-32):** reads would then depend on connection
  credentials, so connection deletion would break reads immediately — that
  path warrants the hard gate this ADR otherwise avoids.
- **Phase 6:** `archived` collections, the marked-then-collected GC that makes
  the collection-delete warning true, and retention pruning of soft-deleted
  rows and history tables.
