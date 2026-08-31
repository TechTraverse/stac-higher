# ADR 0012 — High-volume table hygiene: partition two, sweep three

**Status:** accepted (M2-G); amended P7-H — `staged_uploads` joins the swept
tables (see the sweep list below)

## Context

ROADMAP §5 and issues I-36/I-11 promised time-partitioning for all four
high-volume `stac_higher` tables. Two of them cannot be partitioned as
designed: `delivery_log`'s `UNIQUE (association_id, item_id)` is the
redelivery idempotency key the whole Slice B-ii model rests on, and
`ingest_files`' `UNIQUE (association_id, source_path, version)` is the
mutable ledger DISCOVER upserts against. A partitioned table's unique indexes
must include the partition key — time-partitioning either table breaks its
upsert. Both are current-state tables wearing a history table's clothes.

## Decision

**Partition the two pure append-only tables** — `item_events` (outbox) and
`audit_log` — monthly by their timestamp column, via **attach-don't-copy**
(migration 018): rename the existing table, create a partitioned parent under
the original name (full current column set; PK grows the partition key —
`(id, occurred_at)` / `(id, at)` — nothing FKs these tables and ids stay
sequence-unique in practice), `ATTACH` the old table as a bounded legacy
partition covering `(MINVALUE, start of next month)`. No data copy, no long
lock — correct at 0 rows or 10M. Mechanics the migration depends on, learned
against a real Postgres:

- The legacy table's PK must be **dropped before ATTACH** (the parent PK
  propagates as the partition's PK and two PKs collide); the parent's check
  constraints must **match the legacy's names**; the sequences are re-owned
  by the parents so a future legacy DROP can't take them along.
- `audit_log`'s append-only **row** triggers move to the partitioned parent
  (they clone to every partition; the legacy's own copies are dropped first
  to avoid the name collision). The **TRUNCATE** guard is parent-level only —
  statement triggers don't propagate — which is deliberate: retention drops
  whole partitions via `DETACH` + `DROP`, the escape hatch the migration-003
  comment demanded. Partition drop is the ONLY sanctioned way audit rows die.
- Monthly partitions are provisioned by an idempotent **reconcile** on every
  `runMigrations()` (next month + the one after; the earliest reconciled
  month starts exactly at the legacy bound, so overlap is impossible). The
  pipeline's outbox trigger inserts through the same parent, so a >2-month
  gap with zero app requests would surface as insert errors until any app
  request runs — accepted (compose runs both services together).

**Sweep the other three** (`pipeline.history_retention`, hourly):

- `connection_checks`: delete rows older than
  `CONNECTION_CHECKS_RETENTION_DAYS` (default 30); flip checks stranded
  `pending`/`running` on soft-deleted connections to `failed` (closes I-12
  and the M1 carry-forward — the drain skips deleted connections, so nothing
  else would ever finish them).
- `ingest_files` / `delivery_log`: delete only rows of **soft-deleted
  associations** past `HISTORY_RETENTION_DAYS` (default 365). A live
  association keeps its whole ledger — it is the DISCOVER dedup source;
  pruning it would re-ingest unchanged files.
- `delivery_log` additionally: terminal rows (`delivered`/`dead`) past the
  window whose **item no longer exists** in pgstac (GC'd — the row is
  provenance for nothing; ties the sweep to ADR 0011 as the spec intended).
- `staged_uploads` (P7-H amendment, Phase 7 §11 — migration 020's ledger):
  **terminal rows only** (`finalized`/`rejected`/`expired`) whose verdict
  (`finalized_at`, falling back to `created_at`) is older than
  `HISTORY_RETENTION_DAYS`. Not partitioned — it is the push client's poll
  target (`GET /api/uploads/{uploadId}`) keyed by `id`. The window dwarfs
  both the poll horizon and the `push_rejected` monitor's lookback
  (`PUSH_ALERT_LOOKBACK_SECONDS`, default 24 h), so pruning never hides a
  live alert condition. `pending`/`finalizing` rows belong to the finalize
  sweep's TTL/stale-claim clocks and are never touched here.

The rejected alternative — splitting `delivery_log` into current-state +
partitioned `delivery_attempts` history — preserves the blanket promise but
is more change than M2 needs. Revisit at M3 if sweeps prove insufficient.

## Consequences

- I-36 and I-11 are amended (partitioning claims corrected), I-12 closed.
- Old `item_events`/`audit_log` rows age out by dropping partitions —
  a manual/operator action until a compliance retention policy exists
  (I-11's remaining amber half).
- `id` alone is no longer a declared unique key on the partitioned tables;
  all existing access paths (dispatcher claim by id, audit reads by `at`)
  are unaffected and were re-verified against a real database.
- Pruned `delivery_log` rows for GC'd items mean redelivery idempotency for
  those items is forgotten — by then there is no item to redeliver.
