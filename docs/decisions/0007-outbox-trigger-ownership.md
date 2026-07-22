# ADR 0007 — Event-outbox trigger ownership + mechanism

- **Status:** accepted (Phase 5, Slice A)
- **Owners:** app control plane (`app/src/lib/db/migrate.ts`) + delivery pipeline
  (`services/pipeline`)
- **Related:** ROADMAP §5.4 (event outbox), §6.4 (delivery flow), §10 (pgstac
  trigger restructuring risk); ADR 0001 (migration ownership)

## Context

Phase 5 delivery is event-driven: an item change in the built-in catalog must
reach the delivery dispatcher within seconds, durably, without losing events
across restarts or blowing the ~8 KB `pg_notify` payload cap on bulk upserts
(§5.4). The chosen bridge is a **durable outbox**: a trigger on `pgstac.items`
writes one row per changed item into `stac_higher.item_events`, and a
payload-less `NOTIFY` wakes the dispatcher (the payload is always the outbox
row, never the notification).

Two questions had to be settled before writing the migration:

1. **Ownership.** ADR 0001 established a single DDL owner per concern: the app
   owns all `stac_higher` DDL; the pipeline runs no DDL; pgstac's schema is
   image-owned. The outbox needs a trigger attached to `pgstac.items` — a table
   *neither* runtime owns. Who applies it?
2. **Mechanism.** `pgstac.items` is **partitioned by collection**, and pgstac
   creates partitions dynamically as collections appear. Statement-level
   triggers with transition tables have historically been restricted on
   partitioned parents. Which trigger form reliably captures *every* write path?

## Spike findings (2026-07-21, pgstac `v0.9.11` on PostgreSQL 17.9)

- `pgstac.items` is a partitioned table (`relkind = 'p'`); inserting one item via
  `pgstac.create_item(...)` routed it into a dynamically-created partition
  (`_items_21`).
- **Both** trigger forms attached to the partitioned **parent** were created
  without error and **both fired** for the partition-routed insert:
  a statement-level `AFTER INSERT ... REFERENCING NEW TABLE ... FOR EACH
  STATEMENT` trigger captured the row via its transition table, and a row-level
  `AFTER INSERT OR UPDATE OR DELETE ... FOR EACH ROW` trigger captured it too.
- `pgstac.get_item(_id text, _collection text)` exists — the dispatcher's item
  reader (Task 6) uses it.

Creation and firing-here do not settle robustness. A statement-level trigger on
the parent fires only for operations issued **against the parent**; PostgreSQL
**clones row-level triggers onto every partition** (present and future), so a
row-level trigger fires regardless of whether pgstac writes through the parent
*or* directly into a partition (bulk / partition-targeted upsert paths). §5.4
requires the outbox to catch **every** write path — the row-level form is the
only one that guarantees this against pgstac's bulk and partition-direct writes.

The row-level form's "one `pg_notify` per row" volume is a non-issue: the payload
is always the empty string on a single channel, and PostgreSQL **coalesces
identical notifications within a transaction into a single delivery**, so a
100k-row bulk upsert in one transaction yields exactly one dispatcher wake.

## Decision

1. **Ownership — the app owns the outbox trigger.** The tracked migration 007
   (in `stac_higher`, applied by the existing middleware mechanism) creates
   `stac_higher.item_events` and the `stac_higher.item_events_capture()` trigger
   function — **no pgstac dependency**, so a pgstac-less DB (unit/CI) still
   migrates cleanly. **Attaching** the trigger to `pgstac.items` is done by a
   separate **idempotent `reconcileOutboxTrigger` step that runs on *every*
   `runMigrations()` call** (guarded `IF EXISTS` on `pgstac.items`), not by the
   once-recorded migration: a migration that ran before pgstac's table existed
   (deploy-ordering race, or an app DB role that could not yet see it) would be
   permanently skipped, silently leaving the outbox unpopulated and delivery a
   no-op forever — so the attach must self-heal on the next boot once pgstac
   appears (found in the Slice A `/code-review`; live-verified). This **extends
   ADR 0001**: *the app may attach a trigger to a pgstac table it does not own,
   provided the trigger writes only into `stac_higher` and the attachment is
   `IF EXISTS`-guarded on the pgstac table.* One migration owner is preserved;
   the pipeline stays DDL-free (it only reads/updates `item_events` rows).
2. **Mechanism — row-level trigger.** `AFTER INSERT OR UPDATE OR DELETE ON
   pgstac.items FOR EACH ROW`, inserting one `item_events` row per changed item
   (`op` ∈ insert/update/delete) and issuing a payload-less
   `pg_notify('item_events','')`.

## Rejected alternatives

- **Pipeline owns the trigger DDL.** Splits DDL ownership across two runtimes —
  exactly what ADR 0001 avoided. The app already owns `stac_higher` and runs a
  transactional migration path; adding a second DDL writer buys nothing.
- **Statement-level trigger with transition tables on the parent.** Fires only
  for parent-targeted operations; would silently miss pgstac writes routed
  directly into a partition, violating §5.4's every-write-path guarantee.
- **Poll pgstac's `pgstac_updated_at` / `items_deleted_log` change feed.**
  Deferred, not chosen: those primitives are still landing upstream (§10). The
  vendored trigger is available today and version-pinned; re-evaluate replacing
  it with pgstac's native feed at Phase 5's delivery hardening or a pgstac bump.

## Update semantics — pgstac updates surface as DELETE + INSERT

Live-verified (2026-07-21): driving `create_item` → `update_item` → `delete_item`
through pgstac produced outbox ops `insert`, then **`delete` + `insert`**, then
`delete` — i.e. **pgstac implements an item update as a delete followed by an
insert** (its partition-upsert mechanism) — for the **stac-fastapi transaction
API** write path.

**The write path matters** (refined by the Slice B-i live run, 2026-07-21 —
[ISSUES.md](../ISSUES.md) I-46): a change written via **pypgstac
`Loader.load_items(upsert)`** — the ingest ITEMIZE path, i.e. how most items are
created/updated — fires a **single `update` op** (a new item is `insert`, a
byte-identical re-upsert is a **no-op** with no event, a delete is `delete`). So
the trigger's `update` branch fires routinely via the ingest path, and the
delete-half only accompanies a transaction-API update. Consequences for outbox
consumers (unchanged either way):

- A genuine delete and the delete-half of an update are indistinguishable at the
  event level — which is fine, because **deletes never propagate** (§6.4): the
  dispatcher drains every `delete` event without dispatching. The insert-half of
  an update re-drives delivery, giving correct redelivery behavior.
- Therefore the outbox `op` field must **not** be used to distinguish a
  first-delivery from a redelivery. Slice B's `on_update` logic derives that from
  `delivery_log` (prior delivery of the item), never from `op`. Logged in
  ISSUES.md so Slice B honors it.
- The trigger keeps its `update` branch anyway (harmless, and correct for any
  future direct-UPDATE path).

## Consequences

- The trigger path is validated against **pgstac `v0.9.11`** (pinned in
  `docker-compose.yml`). Per §10, re-run the spike and upgrade-test the trigger
  on every pgstac bump — pgstac's internal trigger machinery is being
  restructured upstream.
- `item_events` is a plain table now; Phase 6 time-partitions it on
  `occurred_at` and adds a partition-drop retention job (mirrors the audit_log /
  ingest_files deferrals, I-11).
- Deletions are recorded in the outbox for bookkeeping but **never propagate**
  to destinations (§6.4); the dispatcher drains delete events without dispatching.
