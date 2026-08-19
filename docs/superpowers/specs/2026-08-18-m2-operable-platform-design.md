# M2 — Operable platform (Phase 6) — design

**Date:** 2026-08-18
**Status:** approved (user sign-off 2026-08-18)
**Scope source:** ROADMAP §9 "Named milestones" (M2), Phase 6, §5 data model,
§5.1 expectations, §6.5 retention & GC, §6.6 observability, §8 UI surface.
**Issues closed:** I-11, I-12, I-36, I-51 (GC half), plus two TODO
"Discovered follow-ups" (deliver pre-record recovery; `listDeliveries`
unbounded per-status aggregate).

---

## 1. Gate

Phase 6's done-when, verbatim, is M2's gate:

> Stopping a source's data flow raises an alert within the declared expectation
> window and notifies the group's channels; an expired item leaves the catalog
> and, after the grace window, object storage.

M2 closes with a **live demo rehearsal** on the auth-enforced stack (same shape
as M1, which found two real bugs — I-54/I-55), evidence recorded in ROADMAP §9,
then the `ai/main → main` promotion PR.

## 2. What M2 inherits

Three schema surfaces already exist and are **dead wiring**. Much of M2 is the
act of connecting them rather than new invention:

| Exists today | Written by | Read by | Connected in |
|---|---|---|---|
| `collection_connections.expectation` (§5.1 shape) | nobody (not editable in either Data-flow form — Slice D deferral) | nobody | M2-A (edit) + M2-B (evaluate) |
| `collection_connections.flow_stats` | nobody (`associations/storage.ts` explicitly never writes it) | nobody | M2-A |
| `collection_settings.retention_days`, `gc_grace_days` (migration 003) | nobody | nobody | M2-E (edit) + M2-F (honor) |

New app-owned DDL (ADR 0001 — the app owns all `stac_higher` DDL, the pipeline
reads/writes rows and never migrates):

- migration 013 — `alerts`, `notification_channels`
- migration 014 — `asset_gc`, `collection_settings.archived`
- migration 015 — partitioning of `item_events` + `audit_log`

## 3. Alerting

No new coordination mechanism: the app owns DDL and the operator-facing verbs,
the pipeline owns evaluation and dispatch, exactly as ADR 0001/0004 established.

### 3.1 Ownership

- **App** — `alerts` / `notification_channels` migrations; `GET /api/alerts`
  (member+, group-scoped like the connections list); `POST /api/alerts/[id]/ack`
  and `POST /api/alerts/[id]/resolve` (operator+, audited). `ack` is already in
  the §5 audit action vocabulary; `resolve` joins it.
- **Pipeline** — a periodic `pipeline.flow_monitor` job evaluates expectations
  against `flow_stats` and raises/clears `alerts` rows; a `pipeline.notify` job
  fans a raised alert out to the owning group's channels.

### 3.2 Sources

Per §6.6, `alerts.source` is one of:

- `flow` — a declared §5.1 expectation is violated:
  `expect_activity_within_seconds` (ingest: no activity in the window) and
  `deliver_within_seconds` (delivery: NRT SLO breached). Absence-of-data is
  only detectable against a declared expectation; an association with no
  `expectation` is never flow-alerted, because an empty poll may be normal.
- `health` — the existing connection health sweep already computes an ok→error
  transition and writes `status`/`last_error`; it simply doesn't record an
  alert. M2 records one on the transition (and resolves on error→ok).
- `job_failure` — a `delivery_log` row reaching `dead`, an `ingest_files` row
  reaching the `INGEST_MAX_RETRIES` cap, or a backfill failing.

### 3.3 Lifecycle

`firing → acknowledged → resolved`, with dedup on
`(source, connection_id, association_id, kind)`:

- A re-observed condition bumps `last_seen` on the existing row rather than
  creating a second row.
- **Auto-resolve** when the condition clears — a flow that recovers, a
  connection that goes healthy again, a dead delivery that is redelivered. An
  operator should not have to close an alert the system can see is over.
- `acknowledged` suppresses *notification*, not detection: the row keeps
  tracking `last_seen`, and a resolve→re-fire cycle notifies again.

## 4. Notification channels

M2 ships **in-app** and **webhook** (email/SMTP deferred; the channel
abstraction is built so it drops in).

- `notification_channels` is per group (`kind` = `in_app | webhook`, plus a
  `config` JSONB). In-app needs no config; webhook carries a URL and an
  optional shared secret for signing.
- **In-app** delivery is the `alerts` row itself plus per-user read state — the
  header bell and the `/monitoring` list read it. No separate transport.
- **Webhook egress lives on the pipeline side.** The app's `safeFetch` blocks
  private/loopback targets by design and that guard should not be widened: a
  webhook target is *operator-declared configuration*, a different trust class
  from a user-supplied fetch target at request time. Dispatch therefore sits
  next to the existing connections egress policy in the pipeline, is retried
  through the queue like any other transfer, and records terminal failure as a
  `job_failure` alert (a channel that cannot notify must itself be visible).
- The webhook `config` and the §5.1 `expectation` shapes are **new
  cross-runtime contracts** ⇒ per AGENTS.md each gets a golden fixture in
  `tests/contract-fixtures/`, consumed by both vitest and pytest.

Documented as **ADR 0010 — alerting & notification model**.

## 5. Retention & GC

### 5.1 The missing piece

ROADMAP §6.5 says GC "marks their canonical assets for removal" without saying
where the mark lives. This design introduces one:

```
stac_higher.asset_gc (
  id, object_key, collection_id, item_id,
  reason,        -- retention | item_delete | collection_delete | archive
  marked_at, collect_after, collected_at, error
)
```

A single writer-agnostic queue serving **every** marked-then-collected path, so
there is one place where "bytes are scheduled to die" is true, and one sweep
that makes it happen.

### 5.2 Flow

1. `pipeline.retention_gc` (periodic) selects items past
   `collection_settings.retention_days` in bulk.
2. Deletes them from pgstac. This emits `delete` outbox events, which per §6.4
   **do not propagate to destinations** — delivered files are the consumer's
   copy and drift is accepted by design.
3. Marks their canonical assets in `asset_gc` with
   `collect_after = now() + gc_grace_days` (default 30).
4. `pipeline.asset_collect` (periodic) deletes marked objects whose grace has
   passed and stamps `collected_at`.

### 5.3 Safety

- `retention_days` stays `null` (keep forever) unless an operator sets it —
  nothing is deleted on a platform nobody configured.
- The Settings UI shows a **counted dry-run preview** before the first apply,
  reusing the warn-and-proceed pattern ADR 0009's delete dialogs established.
- The grace window makes oops-deletes recoverable; storage still never leaks.
- Manual item deletion and collection deletion follow the same path, which is
  what closes **I-51's GC half** (collection delete currently orphans canonical
  bytes) and lets us settle the ADR 0009 open question about reference-mode
  *association* delete recorded in TODO's follow-ups.

Documented as **ADR 0011 — retention & GC**.

## 6. High-volume table hygiene — deviation from ROADMAP §5

ROADMAP §5 and issues I-36/I-11 promise time-partitioning for all four
high-volume tables. **Two of them cannot be partitioned as currently designed**,
and the schema is explicit about why:

- `delivery_log` carries `UNIQUE (association_id, item_id)`, commented in
  migration 008 as *"the idempotency key. UPSERTed on redelivery"*. A unique
  index on a partitioned table must include the partition key, so partitioning
  by time breaks the upsert the entire Slice B-ii redelivery model rests on.
- `ingest_files` carries `UNIQUE (association_id, source_path, version)` and is
  a **mutable** ledger that DISCOVER upserts against — the same collision.

Both are current-state tables wearing a history table's clothes. Decision:

- **Partition** `item_events` (pure append-only outbox, `id` PK only, rows
  short-lived) and `audit_log` (pure append-only, compliance-driven retention).
  Both partition cleanly on their timestamp column.
- **Strategy: attach, don't copy.** Rename the existing table, create the
  partitioned parent under the original name, and `ATTACH` the old table as a
  bounded legacy partition. No data copy, no long lock — correct whether a
  deployment holds 0 rows or 10M, and the legacy partition later drops on the
  ordinary retention schedule.
- `audit_log` needs deliberate per-partition handling of its append-only
  UPDATE/DELETE/TRUNCATE triggers (already flagged in the migration 003
  comment): partition drop is itself a DELETE-shaped operation the trigger
  would otherwise reject.
- **Retention-sweep instead of partition** for `delivery_log`, `ingest_files`,
  and `connection_checks` (I-12). A `delivery_log` row is meaningless once its
  item has been GC'd, which ties the sweep naturally to §5.

The rejected alternative — splitting `delivery_log` into a current-state table
plus a partitioned `delivery_attempts` history — preserves the ROADMAP's blanket
promise but is a larger change than M2 needs. Revisit at M3 if the sweep proves
insufficient under load.

I-36 and I-11 are amended rather than left asserting something the schema will
not do. Documented as **ADR 0012 — high-volume table hygiene**.

## 7. UI surface

Per ROADMAP §8, following existing conventions (Astro thin shell + a single
React island, TanStack Query for server state, shared components in
`packages/shared` where reusable):

- **`/monitoring`** — per-association flow timelines, delivery latency, alert
  list with ack/resolve.
- **Header alert bell** — unread firing-alert count, links to `/monitoring`.
  The Header is already its own island, so this is the one cross-island add.
- **Collection Settings tab** — group ownership, `externally_writable`,
  `retention_days`, `gc_grace_days`, `archived`, with the GC dry-run preview.
- **Data-flow tab** — the §5.1 `expectation` becomes editable in both the
  ingest and delivery halves (the explicit Slice D deferral).

## 8. Telemetry

- Prometheus exposition on the pipeline service (`:8083/metrics`): counters and
  histograms across the ingest and delivery stages. No scraper added to
  docker-compose — the endpoint is curl-verifiable, and the operator-facing
  numbers reach the UI through `flow_stats` rollups instead.
- A structured-JSON logging consistency pass across the pipeline (`log.py`
  exists; usage is uneven).

## 9. Slices

Worked top-down by the solo loop, one task per iteration, worktree off
`ai/main`, `npm run verify` (+ pipeline `pytest`/`ruff` when touched) before
merge.

| # | Slice | Touches |
|---|---|---|
| M2-0 | Deliver **pre-record** durability fix — hoist `upsert_pending` ahead of the fallible `load_target`/`get_item` so `delivery_retry_sweep` owns recovery | pipeline |
| M2-A | Flow telemetry substrate: pipeline writes `flow_stats`; `listDeliveries` counts move onto it; `expectation` editable in both Data-flow halves | pipeline + app + UI |
| M2-B | Alerts core: migration 013, `flow_monitor`, three sources, lifecycle, alert API (ack/resolve, audited) | app DDL + pipeline + app API |
| M2-C | Notification channels: in-app + webhook, per-group CRUD, pipeline dispatch with egress policy + retry — ADR 0010 | app + pipeline |
| M2-D | `/monitoring` page + header alert bell | UI |
| M2-E | Collection Settings tab (ownership, exposure, retention, archived) | app + UI |
| M2-F | Retention & GC: `asset_gc` + migration 014, expiry job, grace collection, dry-run preview, collection-delete/archive impact — ADR 0011, closes I-51 GC half | app DDL + pipeline + UI |
| M2-G | Table hygiene: partition `item_events` + `audit_log`, sweeps for the other three — ADR 0012, closes I-36/I-11/I-12 | app DDL + pipeline |
| M2-H | Service telemetry: `/metrics` + structured-logging pass | pipeline |
| M2-I | M2 demo rehearsal + ROADMAP §9 evidence + promotion PR | lead only |

**Dependency spine:** A→B→C→D (`flow_stats` feeds expectations, which feed
alerts, which feed channels, which feed the bell); E→F (Settings exposes the
knobs GC honors); G and H are independent and may float; I is last.

## 10. Testing & risk

- Per slice: vitest + pytest, `npm run verify` before every merge; e2e for
  M2-D and M2-E (UI flows the suite covers).
- New golden fixtures in `tests/contract-fixtures/` for the `expectation` and
  webhook-channel config shapes — both are new cross-runtime contracts, and
  AGENTS.md requires a shared fixture for each.
- **M2-F is the riskiest slice**: it deletes real bytes. Mitigated by opt-in
  `retention_days`, the dry-run preview, the grace window, and audit rows on
  every apply.
- **M2-G is second**: it rewrites tables on the dispatcher hot path. Mitigated
  by attach-not-copy and by doing it while the tables are still near-empty —
  the same work after M3 load would be a migration under pressure.
