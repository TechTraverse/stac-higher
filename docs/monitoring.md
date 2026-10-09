# Monitoring, alerting & retention (Phase 6 / M2)

The operable-platform surface delivered by M2: flow telemetry, the alerts
lifecycle, notification channels, the `/monitoring` UI, and retention/GC.
Decisions: [ADR 0010](decisions/0010-alerting-notifications.md) (alerting &
notifications), [ADR 0011](decisions/0011-retention-gc.md) (retention & GC),
[ADR 0012](decisions/0012-table-hygiene.md) (table hygiene). Residuals:
`ISSUES.md` I-58, I-59.

## Flow telemetry (M2-A)

`collection_connections.flow_stats` is the per-association rollup the monitor
and UI read (`pipeline/flow/stats.py` holds the pure math; Pg repos and test
fakes share it):

- **Ingest hooks** (settle/itemize in the discover/itemize job handlers) bump
  cumulative `files`/`bytes`/`items`/`failed` plus `last_activity_at`,
  `last_error_at`, `last_latency_seconds`.
- **Delivery transitions** maintain a live per-status `counts` snapshot as
  deltas **in the same transaction** as each `delivery_log` change, seeded
  from a one-time recompute when the `counts` key is missing.
  `listDeliveries` serves counts from the snapshot; the app's redeliver flip
  carries its own dead→failed delta.

The §5.1 `expectation` is split per direction —
`expect_activity_within_seconds` (ingest: data must flow) and
`deliver_within_seconds` (delivery SLO) — editable in both Data-flow halves.
Cross-runtime contract: `tests/contract-fixtures/{ingest,delivery}-expectation.json`
against `pipeline/flow/expectation.py` (lenient readers).

## Alerts (M2-B)

`stac_higher.alerts` (migration 014; app-owned DDL per ADR 0001) is
reconciled each minute by the pipeline's `flow_monitor`
(`pipeline/flow/monitor.py`): gather every currently-true condition →
`sync_alerts` raises new rows, bumps `last_seen` on re-observation, and
auto-resolves its own kinds when the condition clears — one transaction per
tick, deduped by a partial unique index on
`(source, kind, connection_id, association_id, channel_id, collection_id)`
over open rows (the `collection_id` anchor joined in Phase 7's migration 021).

| Source | Raised when | Kinds |
|---|---|---|
| `flow` | A declared §5.1 expectation is breached against `flow_stats` | `ingest_inactivity`, `delivery_slo` |
| `health` | A connection sits in `status='error'` (state-observed per tick; deleted connections filtered) | `connection_error` |
| `job_failure` | Dead deliveries, retry-cap ingest failures, latest-backfill-failed | per failure class |
| `job_failure` (Phase 7) | Recent `rejected` `staged_uploads` rows for a collection (`PUSH_ALERT_LOOKBACK_SECONDS`, default 24 h; a resolved-at floor keeps a manual resolve stuck until a NEW rejection) — collection-anchored; group via `collection_settings.group_id` (unowned → admin-only) | `push_rejected` |
| `health` (C-4) | A live, enabled process's CURRENT revision snapshots a user image that is `flagged`, `revoked`, gone or stale (not scanned inside the image policy's `scan_window_days`) — process-anchored; written by `pipeline/images/alerts.py` every minute from `pipeline.image_scan_drain` (before the drain), outside `MONITOR_KINDS` (`alert-kinds.json` `image_kinds`) | `process_image_flagged` (auto-resolved when the image is approved and fresh again or the process deploys off it) |
| (notify) | A channel's webhook dead-letters — written by the notify sweep, outside `MONITOR_KINDS`, so the monitor never clobbers it | `webhook_failed` (channel-anchored; auto-resolved by the next successful delivery) |

`process_image_flagged` (C-4, container-images spec §10) routes like `process_failed`: to the process's group channels (the notify fan-out derives a process-anchored alert's group from `processes.group_id`). The process health verdict reads it as **degraded, not failing**, because a flagged image blocks new deploys while its runs continue. Only a new alert row notifies: a later rescan that adds findings to an image that is already flagged updates the open alert's message and does not re-notify (ISSUES I-141). `declared_kinds` is empty again.

Lifecycle: `firing → acknowledged → resolved`. **Ack suppresses notification,
not detection** — the monitor keeps bumping `last_seen`. A manual resolve with
the condition still true re-fires as a NEW row, which is what re-notifies.
Group scoping is derived (alert → connection | channel | collection | process
→ group, coalesced in that order; the process leg since C-4), never stored.
Webhook payloads carry `process_id` for process-anchored alerts.

Routes: `GET /api/alerts` (member+, own groups; admin all;
`?state=firing|acknowledged|resolved|open`, `?limit`); operator+ audited
`POST /api/alerts/[id]/ack` and `.../resolve`.

## Notification channels (M2-C, ADR 0010)

Per-group `stac_higher.notification_channels` (`in_app` | `webhook`; email is
a deferred third kind), CRUD at `/api/channels*` (operator+ mutations audited;
the webhook signing secret is write-only — responses expose `has_secret`).

- **In-app** delivery is the alerts row itself plus the per-user
  `alert_reads` watermark: `GET /api/alerts/unread` (the header bell's count)
  and `POST /api/alerts/read` (advances the caller's watermark — member+,
  deliberately ungated/unaudited personal UI state). An `in_app` channel row
  is declarative-only (I-58).
- **Webhook** dispatch is **pipeline-side** (`pipeline/notify/`), behind the
  connections egress policy (`resolve_pinned`, pinned-IP dial) — the app's
  `safeFetch` is never widened for it. Durability is ledger-shaped
  (`notification_deliveries`, migration 015): the minute `notify_sweep` fans
  out firing un-notified alerts (keyed off the durable `alerts.notified_at`
  watermark), retries with cool-off, revives stalls; `webhook_notify` claims
  a row and POSTs `{"event":"alert.firing","alert":{…}}`, HMAC-SHA256-signed
  (`X-StacHigher-Signature`) when the channel config has a `secret`.
  Terminal failure dead-letters and raises the channel-anchored
  `webhook_failed` alert. Delivery is at-least-once — consumers dedupe on
  `alert.id`. Webhook `config` is a cross-runtime contract
  (`tests/contract-fixtures/webhook-channel-config.json`).

## `/monitoring` UI + header bell (M2-D)

One island (`app/src/components/monitoring/`), three cards — all visible to
members (read-only; verbs are operator+), group-scoped like `/connections`:

- **Alerts** — open/resolved views, audited ack/resolve, advances the read
  watermark on open.
- **Data flows** — `GET /api/monitoring/flows` (cross-collection association
  list: direction, connection health, files/items/bytes, activity recency,
  latency, per-status counts). The late/on-time hint is derived from the open
  alerts list, so it cannot disagree with the alert rows (it lags the
  monitor's minute tick by design).
- **Notification channels** — add/remove; secrets never displayed.

The Header's `AlertBell` polls `/api/alerts/unread` every 30 s and links to
`/monitoring`. e2e: `app/e2e/monitoring.spec.ts` (needs the Docker stack —
the routes hit `stac_higher.*`).

## Collection settings, retention & GC (M2-E/M2-F, ADR 0011)

The **Settings** tab on built-in-catalog collection pages exposes
`collection_settings`: owning group (ADR 0003 rules), `externally_writable`,
`retention_days` (null = keep forever), `retention_max_items` (null = no
count cap; keep the newest N by item datetime — W-2, migration 026; the two
rules union, `archived` overrides both), `gc_grace_days`, `archived`
(migration 016). `GET`/`PUT /api/collections/[id]/settings` (PUT operator+,
audited, full-document — every field including `retention_max_items` is
required); `GET .../settings/impact?retention_days=N&retention_max_items=N`
is the counted dry-run behind the warn-and-proceed dialog.

`stac_higher.asset_gc` (migration 017) is the **only** path by which canonical
bytes are deleted — marks are §5.3 key prefixes with
`collect_after = now() + gc_grace_days`; mark-FIRST-then-delete is the
crash-safety invariant. Pipeline sweeps (`pipeline/gc/`, five-minute):
`retention_gc` expires items per settings (archive expires everything — the
ADR 0009 "delete the data, keep the record" state; delete events never
propagate to destinations) and `asset_collect` deletes due prefixes. The app
marks on BFF item/collection deletes; `archived` refuses item writes and new
associations (409). Nothing is deleted on an unconfigured platform.
Residuals (grace-window item-id reuse, datetime-keyed retention): I-59.

## Cube repositories (Z-6, ADR 0022)

A virtual cube's Icechunk repository at `assets/{cube}/_cube/` is the one
place bytes are deleted outside `asset_gc`. The deleter is Icechunk's own GC,
and it runs only on a sink with a `window`. `pipeline.cube_maintain`
(`23 * * * *`) enqueues one `pipeline.cube_maintain_sink` per **enabled**
sink. Each job takes the sink's `lock` (`cube:{id}`), so it never runs
alongside that sink's appends. A dead worker's maintenance job is recovered
by `cube_kick`, like an append. One run (`pipeline/cubes/maintain.py`):

1. deletes the sink's terminal ledger rows (`cube_appends`) last updated more
   than 7 days ago; `pending` rows are kept;
2. opens the repository, if it exists (maintenance never creates one);
3. **only with a window**:
   - trims by age when the source has stopped: if nothing is pending and
     the tip is the recorded snapshot, it drops the steps the window no
     longer holds in one commit and records it (`last_snapshot_id`) (a trim whose record was lost is recorded on the next run);
   - runs `expire_snapshots` and then `garbage_collect`, both with the cutoff `now − CUBE_SNAPSHOT_RETENTION_SECONDS` (default 1 h), lowered so that every snapshot that was the tip within that window survives (`expiry_cutoff`): a reader still on the previous tip keeps working, and the recorded snapshot (the one the collection asset's `version` names) always survives;
4. republishes the recorded tip on the cube collection (Z-5's writer,
   idempotent);
5. lists the prefix and records object counts and bytes per kind.

The result goes into `cube_sinks.last_maintenance` on every run.
`last_maintained_at` is set only on success.

| `last_maintenance` key | Meaning |
|---|---|
| `status` | `ok`, `attention` (see `attention`) or `failed` (see `error`) |
| `attention` | Reasons: `repo_size` (total ≥ `CUBE_REPO_WARN_BYTES`, default 1 GiB) `gc_delete_failures` (GC could not delete some objects) and `unrecorded_tip` (the tip is not the recorded snapshot and nothing is pending, so no append is coming to record it). Each one also logs a WARNING. |
| `started_at`, `finished_at`, `durations_ms` | ISO times; per-phase milliseconds (`trim`, `expire`, `gc`, `list`) |
| `window`, `repository` | Whether the sink has a window, and whether its repository exists |
| `healed` | `true` when this run found a maintenance trim that was committed but never recorded, and recorded it |
| `trimmed`, `published` | Steps the age trim dropped, and whether the collection asset was republished |
| `expired_snapshots`, `gc` | Snapshots expired, and Icechunk's `GCSummary` counters (`snapshots_deleted`, `manifests_deleted`, `chunks_deleted`, `transaction_logs_deleted`, `attributes_deleted`, `bytes_deleted`, `objects_failed_to_delete`, the first 5 `delete_errors`). Both are `null` without a window. |
| `sizes`, `total_objects`, `total_bytes`, `warn_bytes` | Per kind `{objects, bytes}` for `transactions`, `overwritten`, `manifests`, `snapshots`, `chunks` and `other` (the `repo` object, config) |
| `ledger_pruned` | Ledger rows deleted |

Expiry and GC keep snapshots, manifests and chunks flat. They never delete
`transactions/` or `overwritten/`, which grow by one object each per commit
(I-143, about 8 MB a day per cube). Those two kinds in `sizes` are the ones to
watch. At the 1 GiB default, a 5-minute cube reaches `attention` after about
four months. A sink without a window is never trimmed or garbage-collected,
so its `snapshots` and `manifests` grow too.

## Table hygiene (M2-G, ADR 0012) & metrics (M2-H)

`item_events` and `audit_log` are monthly-partitioned (migration 018,
attach-don't-copy; `runMigrations()` reconciles partitions two months ahead);
`delivery_log`/`ingest_files`/`connection_checks` are deliberately not (their
UNIQUE keys are the upsert model) — the hourly `history_retention` sweep
prunes them conservatively. Audit rows die only by partition DETACH+DROP; no
automated partition-drop policy yet (I-11). Since C-4 it also runs the scan retention leg (`pipeline/images/retention.py`): each image's current SBOM pair and its ten newest scans' objects stay, every other object under `scans/` older than 24 h goes (dedup orphans included), older rows' refs are nulled, and `image_scans` rows past `HISTORY_RETENTION_DAYS` are pruned, never an image's `last_scan_id`.

`GET :8083/metrics` (`pipeline/metrics.py`, Prometheus exposition): per-job
runs/duration/outcome wrapped centrally at Procrastinate registration, ingest
stage counters on the flow-stats hooks, delivery terminal counters + latency
histogram, webhook/alert counters. No scraper ships in compose. Note:
`pipeline_ingest_bytes_total` counts per stage (settle AND itemize) — don't
sum stages. Pipeline log data goes in `extra={...}` structured fields, never
interpolated into messages.
