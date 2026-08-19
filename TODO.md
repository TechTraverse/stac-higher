# TODO — M2 work queue

The solo agent loop (AGENTS.md) works this file top-down: pick the **first
unchecked item**, one task per iteration, worktree off `ai/main`, `npm run
verify` (+ pipeline `pytest`/`ruff` when the pipeline is touched) before merge.

Scope source: **`docs/superpowers/specs/2026-08-18-m2-operable-platform-design.md`**
(approved 2026-08-18), which derives from ROADMAP §9 M2 / Phase 6, §5.1, §6.5,
§6.6, §8. Read the spec section named in a task before starting it.

**M2 gate (Phase 6 done-when):** stopping a source's data flow raises an alert
within the declared expectation window and notifies the group's channels; an
expired item leaves the catalog and, after the grace window, object storage.

M1 is closed — its queue is in git history at `fd8135c`; the rehearsal evidence
is in ROADMAP §9 M1.

## Warm-up — carried-forward durability fix

- [ ] **M2-0 · deliver pre-record durability fix** (spec §9). Deliver's
      pre-record blind spot is covered by queue retry only (~4 min window): if
      the DB stays down through all attempts the delivery is lost invisibly —
      the outbox row is claimed but no `delivery_log` row exists for the retry
      sweep to find. Hoist `upsert_pending` ahead of the fallible
      `load_target`/`get_item` work (dispatch time, or the top of the deliver
      handler) so `delivery_retry_sweep` owns recovery, matching the ingest
      stored-stall sweep. Behavior change — deferred out of the I-55 /simplify
      pass, now due. Unit-test the pre-record crash path re-driving to
      `delivered`.

## Alerting (spec §3, §4)

- [ ] **M2-A · flow telemetry substrate** (spec §2, §7). Pipeline writes
      `collection_connections.flow_stats` on ingest settle/itemize and on every
      delivery terminal transition (files, bytes, `last_activity_at`,
      `last_error_at`, latency, per-status counts). Move `listDeliveries`'
      per-status counts onto the rollup — today it aggregates the association's
      WHOLE `delivery_log` on a 15s-polled path (negligible now, unbounded by
      M3). Make the §5.1 `expectation` editable in **both** Data-flow halves
      (the explicit Slice D deferral) with a shared contract fixture.
- [ ] **M2-B · alerts core** (spec §3). Migration 013 (`alerts`,
      `notification_channels`); periodic `pipeline.flow_monitor` evaluating
      expectations against `flow_stats`; the three §6.6 sources (`flow`,
      `health` on the existing sweep's ok→error transition, `job_failure` on
      dead deliveries / ingest retry-cap / failed backfills); dedup on
      `(source, connection_id, association_id, kind)` with `last_seen` re-fire
      and **auto-resolve** when the condition clears; `GET /api/alerts`
      (member+, group-scoped) and audited operator+ `ack`/`resolve` routes.
- [ ] **M2-C · notification channels** (spec §4, ADR **0010**). In-app (alerts
      row + per-user read state) and webhook; per-group channel CRUD. Webhook
      dispatch lives **pipeline-side** next to the connections egress policy —
      do NOT widen the app's `safeFetch` private/loopback guard; retry through
      the queue, and record terminal webhook failure as a `job_failure` alert.
      Golden fixture for the webhook `config` shape (new cross-runtime
      contract).
- [ ] **M2-D · `/monitoring` page + header alert bell** (spec §7). Per-association
      flow timelines, delivery latency, alert list with ack/resolve; unread
      firing count in the Header island. e2e coverage.

## Retention & GC (spec §5)

- [ ] **M2-E · collection Settings tab** (spec §7). Group ownership,
      `externally_writable`, `retention_days`, `gc_grace_days`, `archived`
      (ADR 0009's archived state). The retention columns exist since migration
      003 and have never been readable or writable. e2e coverage.
- [ ] **M2-F · retention & GC** (spec §5, ADR **0011**). Migration 014:
      `asset_gc` (the single marked-then-collected queue for retention /
      item-delete / collection-delete / archive) + `collection_settings.archived`.
      `pipeline.retention_gc` bulk-expires per `retention_days` → deletes from
      pgstac (delete events do NOT propagate to destinations, §6.4) → marks
      assets with `collect_after = now() + gc_grace_days`;
      `pipeline.asset_collect` deletes past the grace. Counted **dry-run
      preview** in the UI before any apply, audited. Closes **I-51's GC half**
      (collection delete no longer orphans canonical bytes) and settles the
      ADR 0009 open question below on reference-mode association delete.

## Hygiene & telemetry (spec §6, §8)

- [ ] **M2-G · high-volume table hygiene** (spec §6, ADR **0012**). Migration
      015: partition `item_events` and `audit_log` via **attach-don't-copy**
      (rename → partitioned parent under the original name → `ATTACH` the old
      table as a bounded legacy partition); handle `audit_log`'s append-only
      triggers per partition (partition drop is DELETE-shaped). Retention
      **sweeps** — not partitioning — for `delivery_log`, `ingest_files`, and
      `connection_checks`: both of the first two carry natural UNIQUE keys
      (`(association_id, item_id)`, `(association_id, source_path, version)`)
      that a time-partitioned unique index would break. Amend **I-36** and
      **I-11** rather than leaving them promising something the schema won't
      do; closes **I-12**. Fold in the stranded-`running` `connection_checks`
      cleanup noted below.
- [ ] **M2-H · service telemetry** (spec §8). Prometheus exposition on the
      pipeline (`:8083/metrics`) with counters/histograms across the ingest and
      delivery stages, plus a structured-JSON logging consistency pass
      (`log.py` exists; usage is uneven). No scraper in docker-compose.

## M2 gate

- [ ] **M2-I · M2 demo rehearsal** (lead only: dev server + Docker + e2e). On
      the auth-enforced stack: stop a source mid-flow → alert fires within the
      declared expectation window → webhook + bell notify → ack → recovery
      auto-resolves; set `retention_days` on a collection → expired item leaves
      the catalog → after a shortened grace window its bytes leave MinIO.
      Record evidence in ROADMAP §9 M2; then promote `ai/main → main` via PR.

## Discovered follow-ups

(append here during iterations)

### Carried forward from M1

- ADR 0009 leaves ASSOCIATION-delete reference semantics implicit: deleting a
  reference-mode ingest association (connection kept) leaves its items serving
  from `source_href` with no update path — the same "unmanaged dead links"
  argument that justified removal on connection delete. **Decide in M2-F**
  (either remove on delete, or push the dialog toward disable-instead-of-delete).
- Deleted-connection `connection_checks` claims skip the scrubbed row
  (`deleted_at IS NULL` in the drain's connection load), stranding such a check
  at `running`. Harmless (the app can no longer poll it — the parent 404s) but
  **fold the cleanup into M2-G**'s `connection_checks` sweep.
- Unit-test gotcha: `api-assets.test.ts` once 500'd with the Docker stack down
  because the Phase 4 reference seam added an unmocked Postgres query ahead of
  the offline presign path (now stubs `lookupReferenceHref`). Watch for the
  same pattern whenever a unit-tested route grows a DB lookup — M2-B/M2-F add
  several.
- Infra gotcha: `docker compose build` can exit 0 while the build FAILED
  (BuildKit registry `DeadlineExceeded` resolving base-image metadata). After
  rebuilding the pipeline image, confirm the container actually has the new
  code, or grep the build output for `ERROR`; pre-pulling the base images
  clears the timeout.
- /simplify note (Slice D): the Data-flow split is lopsided — delivery got
  `DeliverySection`/`DeliveryFormDialog` while the ~500-line ingest half stays
  inline in `DataFlowTab.tsx` with its own form-seeding pattern. **M2-A edits
  both halves** (expectation fields), so it is the natural moment to extract a
  mirroring `IngestSection` (+ form dialog, mount-per-open seeding,
  schema-parsed `formFromAssociation`); migrating both dialogs to the repo's
  RHF+Zod form pattern is the full-depth version.
- Shared-package note: `EmptyState` requires an `icon` prop that was easy to
  omit (it crashed empty Data-flow tabs until fixed). Consider making `icon`
  optional with a default in a future shared-package pass.
