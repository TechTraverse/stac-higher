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

- [x] **M2-0 · deliver pre-record durability fix** (spec §9). Deliver's
      pre-record blind spot is covered by queue retry only (~4 min window): if
      the DB stays down through all attempts the delivery is lost invisibly —
      the outbox row is claimed but no `delivery_log` row exists for the retry
      sweep to find. Hoist `upsert_pending` ahead of the fallible
      `load_target`/`get_item` work (dispatch time, or the top of the deliver
      handler) so `delivery_retry_sweep` owns recovery, matching the ingest
      stored-stall sweep. Behavior change — deferred out of the I-55 /simplify
      pass, now due. Unit-test the pre-record crash path re-driving to
      `delivered`.
      **Done 2026-08-18** — `pre_record` is INSERT-only rather than a hoisted
      `upsert_pending` (hoisting it verbatim would clobber the prior row state
      the `on_update`/overwrite gates read — proved by the guard tests), plus
      `discard_pre_records` for the disabled-association no-op, failure
      recording for config/adapter errors that used to vanish, and
      `sweep_stalled_deliveries` (`DELIVERY_STALL_SECONDS`) for rows stranded
      in `pending`/`delivering`. Full story: ISSUES **I-56**.

## Alerting (spec §3, §4)

- [x] **M2-A · flow telemetry substrate** (spec §2, §7). Pipeline writes
      `collection_connections.flow_stats` on ingest settle/itemize and on every
      delivery terminal transition (files, bytes, `last_activity_at`,
      `last_error_at`, latency, per-status counts). Move `listDeliveries`'
      per-status counts onto the rollup — today it aggregates the association's
      WHOLE `delivery_log` on a 15s-polled path (negligible now, unbounded by
      M3). Make the §5.1 `expectation` editable in **both** Data-flow halves
      (the explicit Slice D deferral) with a shared contract fixture.
      **Done 2026-08-18** — pure rollup math in `pipeline/flow/stats.py`
      shared by the Pg repos and the test fakes; delivery counts are a live
      per-status snapshot maintained as deltas **in the same transaction** as
      each `delivery_log` transition (seeded from a one-time recompute when
      the `counts` key is missing); ingest hooks in the discover/itemize job
      handlers; `listDeliveries` reads the snapshot (legacy aggregate only as
      the pre-seed fallback); the app's redeliver flip carries its dead→failed
      delta in one CTE statement. Expectation split per direction
      (`expect_activity_within_seconds` / `deliver_within_seconds`), editable
      in both dialogs, with `pipeline/flow/expectation.py` lenient readers and
      two new golden fixtures. Follow-ups below.
- [x] **M2-B · alerts core** (spec §3). Migration 013 (`alerts`,
      `notification_channels`); periodic `pipeline.flow_monitor` evaluating
      expectations against `flow_stats`; the three §6.6 sources (`flow`,
      `health` on the existing sweep's ok→error transition, `job_failure` on
      dead deliveries / ingest retry-cap / failed backfills); dedup on
      `(source, connection_id, association_id, kind)` with `last_seen` re-fire
      and **auto-resolve** when the condition clears; `GET /api/alerts`
      (member+, group-scoped) and audited operator+ `ack`/`resolve` routes.
      **Done 2026-08-18** — landed as migration **014** (the CI slice took
      013; the spec's 013/014/015 are 014/015/016 on disk). Monitor is a
      single sync per tick (`pipeline/flow/monitor.py`): gather every
      currently-true condition → `sync_alerts` raises / bumps `last_seen` /
      auto-resolves in one transaction, with the resolve scope limited to
      `MONITOR_KINDS` so future writers (M2-C webhook failures) are never
      clobbered. `health` observes connection `status='error'` as state each
      tick (dedup makes that equivalent to the ok→error transition hook, and
      it also catches error states set outside the sweep). Group scoping is
      DERIVED (alert → connection → group), not stored. Follow-ups below.
- [x] **M2-C · notification channels** (spec §4, ADR **0010**). In-app (alerts
      row + per-user read state) and webhook; per-group channel CRUD. Webhook
      dispatch lives **pipeline-side** next to the connections egress policy —
      do NOT widen the app's `safeFetch` private/loopback guard; retry through
      the queue, and record terminal webhook failure as a `job_failure` alert.
      Golden fixture for the webhook `config` shape (new cross-runtime
      contract).
      **Done 2026-08-19** — migration 015 (`notification_deliveries` ledger,
      `alert_reads` watermark, `alerts.channel_id`+`notified_at`, dedup index
      grown to the channel leg — `sync_alerts`' conflict target moved in
      lockstep). "Retry through the queue" landed as the repo's standard
      ledger+sweep shape (delivery_log model): `pipeline.notify_sweep` fans
      out + retries with cool-off + revives stalls; `pipeline.webhook_notify`
      claims and POSTs via `resolve_pinned` (pinned-IP dial, optional HMAC
      `X-StacHigher-Signature`); dead-letter raises a CHANNEL-anchored
      `webhook_failed` alert, auto-resolved on the next success. App:
      `/api/channels*` CRUD (secret write-only), `/api/alerts/unread` +
      `/api/alerts/read` (member+, ungated). Residuals: ISSUES **I-58**.
- [x] **M2-D · `/monitoring` page + header alert bell** (spec §7). Per-association
      flow timelines, delivery latency, alert list with ack/resolve; unread
      firing count in the Header island. e2e coverage.
      **Done 2026-08-19** — `/monitoring` island with Alerts (open/resolved,
      audited ack/resolve, advances the read watermark on open), Data flows
      (new `GET /api/monitoring/flows` cross-collection list; activity
      recency, latency, per-status counts, late/on-time hint vs the §5.1
      window — display-only, the alert row stays authoritative), and
      Notification channels (add/remove; the channels-UI question from the
      M2-C follow-up settled: it lives here). `AlertBell` in the Header
      (30s poll of `/api/alerts/unread`). Full e2e suite green (29 passed)
      incl. 4 new monitoring specs. Follow-ups below.

## Retention & GC (spec §5)

- [x] **M2-E · collection Settings tab** (spec §7). Group ownership,
      `externally_writable`, `retention_days`, `gc_grace_days`, `archived`
      (ADR 0009's archived state). The retention columns exist since migration
      003 and have never been readable or writable. e2e coverage.
      **Done 2026-08-19** — Settings tab on built-in-catalog collection pages
      (read-only for members, editable operator+); migration **016** adds
      `collection_settings.archived` (pulled forward from M2-F's migration so
      the tab ships the full §7 field set — DECLARATIVE until M2-F enforces
      it, the UI copy says so). `GET`/`PUT /api/collections/[id]/settings`:
      PUT is guard-audited (`collection_settings`), current-owner rule via
      `canManageCollection` (ADR 0003: unowned = any operator), transfer rule
      = target group must be the caller's (admin excepted). e2e green
      (31 passed). Follow-ups below.
- [x] **M2-F · retention & GC** (spec §5, ADR **0011**). Migration 014:
      `asset_gc` (the single marked-then-collected queue for retention /
      item-delete / collection-delete / archive) + `collection_settings.archived`.
      `pipeline.retention_gc` bulk-expires per `retention_days` → deletes from
      pgstac (delete events do NOT propagate to destinations, §6.4) → marks
      assets with `collect_after = now() + gc_grace_days`;
      `pipeline.asset_collect` deletes past the grace. Counted **dry-run
      preview** in the UI before any apply, audited. Closes **I-51's GC half**
      (collection delete no longer orphans canonical bytes) and settles the
      ADR 0009 open question below on reference-mode association delete.
      **Done 2026-08-19** — landed as migration **017** (numbering note
      below). `asset_gc` marks are key PREFIXES (one row per item /
      collection — §5.3 nests assets per item); mark-FIRST-then-delete is the
      crash-safety invariant. Pipeline `pipeline/gc/`: `retention_gc` +
      `asset_collect` (five-minute sweeps, `GC_BATCH_ITEMS`); archive expires
      everything per ADR 0009 ("delete the data, keep the record"). App: BFF
      deletes mark prefixes; archived refuses item writes + new associations
      (409s); Settings warn-and-proceed dialog with the counted dry-run
      (`/settings/impact`). Reference-mode ASSOCIATION delete now removes its
      items (question settled; dialog counts them). I-51 closed; residuals:
      ISSUES **I-59**.

## Hygiene & telemetry (spec §6, §8)

- [x] **M2-G · high-volume table hygiene** (spec §6, ADR **0012**). Migration
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
      **Done 2026-08-19** — landed as migration **018**. The attach was
      validated against a REAL Postgres before merge (scratch DB + the dev
      DB's live rows via e2e); three non-obvious mechanics are documented in
      ADR 0012: legacy PKs must drop before ATTACH, parent checks must match
      legacy constraint names, legacy audit triggers must drop so the
      parent's clones can land. `RECONCILE_PARTITIONS_SQL` provisions months
      m+1/m+2 on every runMigrations() (ranges can never overlap the legacy
      bound by construction). Hourly `pipeline.history_retention` sweeps the
      three UNIQUE-keyed tables conservatively; stranded-running checks on
      deleted connections flip to failed (M1 carry-forward folded in).
      I-36/I-11 amended, I-12 closed. Follow-ups below.
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

### From M2-0

- The pre-record cannot help when Postgres is wholly unreachable (it is itself
  a DB write) — see I-56's residual note. If M3 wants coverage for that case,
  the only real answer is not claiming the outbox row until the delivery is
  durably recorded, which is a dispatcher-side change, not a handler-side one.
- `sweep_stalled_deliveries` uses one fixed window for both `pending` and
  `delivering`. A deployment with legitimately long single transfers must widen
  it globally; if that becomes awkward, split the window per state (a `pending`
  row is never legitimately slow — only a `delivering` one is).
- The new `PgDeliveryRepo` SQL is `# pragma: no cover` per repo convention, so
  the three statements are unverified against a real Postgres until the **M2-I**
  rehearsal. Worth an explicit step there: kill a worker mid-transfer and watch
  the stall sweep recover the row.

### From M2-A

- The delivery `counts` snapshot is delta-maintained and can in principle
  drift under a pathological race (a concurrent first-ever `upsert_pending`
  for the same (association, item) with no pre-existing row — production paths
  always `pre_record` first, so the row exists and the FOR UPDATE serializes).
  The clamp-at-zero in `apply_count_delta` keeps drift from going negative.
  **M2-I should verify** `flow_stats->counts` equals a `GROUP BY status` over
  `delivery_log` after the rehearsal's delivery sequence; if M3's multi-worker
  load ever shows drift, add a periodic reconcile to the retry-sweep tick.
- Every delivery transition (pending→delivering→terminal) is now also a
  `collection_connections` row update — ~3 extra row-locked writes per
  delivered item on one association row. Fine at M2 scale; at M3 (30 items/s
  per association) consider batching the delta per deliver job instead of per
  transition.
- The new flow_stats SQL in `delivery/repo.py` / `ingest/repo.py` is
  `# pragma: no cover` per repo convention — exercised first at the **M2-I**
  rehearsal (same boat as the M2-0 statements; one live delivery + one live
  ingest tick covers both).
- The ingest half's expectation field went into the existing inline dialog;
  the `IngestSection` extraction (mirroring `DeliverySection`) noted below
  remains open — M2-A chose the minimal diff because another agent had
  in-flight edits to `DataFlowTab.tsx`.

### From M2-B

- Migration numbering drift: the spec's 013/014/015 are **014/015/016** on
  disk (the CI slice landed `013_backfill_one_open_per_association` first).
  M2-F and M2-G must use 015/016.
- The delivery-SLO breach check treats a stale `last_latency_seconds` above
  the window as still-breaching until a faster delivery lands — an idle
  association whose LAST delivery was slow keeps its alert open. Defensible
  (last known state), but if operators find it noisy, add a recency cutoff.
- `sync_alerts` upserts conditions one statement at a time inside one
  transaction — fine at alert cardinality (dozens), not built for thousands
  of simultaneous conditions. Revisit only if M3-scale fan-out ever produces
  that many distinct firing conditions.
- The monitor's Pg SQL (incl. the expression `ON CONFLICT` against the
  partial dedup index) is `# pragma: no cover` — **M2-I must exercise**: raise
  → re-fire (last_seen bump, no duplicate) → ack → auto-resolve → re-fire as
  a new row, against real Postgres.
- M2-C hooks left ready: `notification_channels` DDL exists (no CRUD yet);
  `sync_alerts` returns `(newly_raised, auto_resolved)` so the notify job can
  key off newly-raised rows; `MONITOR_KINDS` is the ownership boundary a
  webhook-failure alert writer must stay outside of.
  *(M2-C note: fan-out keys off the durable `alerts.notified_at` watermark,
  not the tick's return counts — a crash between raise and notify would have
  lost the in-memory signal.)*

### From M2-C

- Accepted-simplification bundle is ISSUES **I-58**: in_app rows are
  declarative-only, no `alert.resolved`/recovery webhook events, at-least-once
  POST delivery, plaintext HMAC secret in `config`, and `PgNotifyRepo` SQL
  unexercised until **M2-I** (add a webhook leg to the rehearsal: alert fires
  → webhook lands signed → kill the worker mid-POST → stall revival retries →
  dead-letter raises `webhook_failed` → next success auto-resolves it).
- **M2-D contract**: the bell reads `GET /api/alerts/unread` and calls
  `POST /api/alerts/read` on open; both exist and are member+-ungated. The
  channels CRUD has no UI yet — M2-D's `/monitoring` page is the natural home
  for a channels management panel (spec §7 doesn't name one; decide there).
- The migration-015 CHECK/index rebuild assumes the migration-014 unnamed
  anchor CHECK auto-named `alerts_check` (single unnamed table-level CHECK —
  deterministic in practice, `IF EXISTS`-guarded regardless). If a deployment
  ever reports the old CHECK surviving, that assumption is why.
- `create_pending_deliveries` inserts row-per-channel in a loop — fine at
  channel cardinality (a handful per group), not a bulk path.

### From M2-D

- **Local-stack maintenance**: migration 015 changes the alerts dedup index,
  so a pipeline container built before M2-C fails its flow_monitor upsert
  (`ON CONFLICT` no longer matches an index) every minute once the app
  migrates the shared DB. After merging M2-C+, rebuild:
  `docker compose build pipeline && docker compose up -d pipeline` (watch the
  I-54-era gotcha: `build` can exit 0 on a failed BuildKit pull — grep for
  `ERROR`).
- **e2e gotcha**: something else may own :4321 (this run: the user's Cursor
  editor listens there), which makes Playwright's webServer time out. The
  config honors `E2E_PORT` — run `E2E_PORT=4399 npm run test:e2e:ci`.
- The flows card's late/on-time hint re-implements a display-level
  approximation of the monitor's evaluation (`isLate` ignores the
  edited_at fallback and the outstanding-delivery breach check). Kept simple
  deliberately — if the hint and the alert list ever visibly disagree,
  either derive the hint from the open alerts instead or expose the
  monitor's verdict on the flows API.
- The bell polls `/api/alerts/unread` every 30s from EVERY page's header —
  at envelope scale that's one cheap indexed count per user per 30s, fine;
  if it ever matters, piggyback the count onto an existing poll.
- `/monitoring` shows all three cards to members (read-only, verbs hidden).
  Group-scoping means members only see their groups' data — same posture as
  /connections.

### From M2-E

- **Migration numbering for M2-F/G**: `archived` took **016**, so M2-F's
  `asset_gc` migration is **017** and M2-G's partitioning is **018** (the
  M2-B note about 015/016 is superseded).
- Ownership transfer edge: an ADMIN can assign a collection to ANY group
  (string is not validated against known groups — groups only exist as
  claims). A typo'd group id silently makes the collection unmanageable by
  everyone but admins. Low risk; a groups directory would fix it properly.
- `archived` currently changes nothing but a badge. M2-F must wire: archived
  → read-only in the BFF write path (decide exact scope there — ADR 0011)
  and assets → `asset_gc`. The Settings copy promises "read-only + assets
  scheduled for collection"; keep M2-F honest against it or amend both.
- The tab's form is local useState, not the repo's RHF+Zod pattern (5 flat
  fields; RHF adds no value at this size). If it grows (dry-run preview,
  per-field audit hints), migrate then.

### From M2-F

- **Grace-window item-id reuse** (I-59's sharpest edge): an open prefix mark
  collects NEW bytes written under a re-used item id. If M3's flows re-create
  ids routinely, snapshot key lists at mark time instead of prefixes.
- The retention sweep's `list_expired_items` orders archived-collection
  deletion by id with no per-collection progress cursor — fine at 500/tick;
  a 10M-item archive takes ~14 days of ticks. Acceptable; note for M3.
- e2e does NOT cover the GC loop (needs the pipeline's five-minute sweeps —
  M2-I exercises it live with gc_grace_days=0). The vitest/pytest suites
  cover marking, ordering, and collection logic.
- M2-E's archived copy said "read-only + assets scheduled for collection";
  implemented as: item writes refused, metadata edits/deletes allowed, sweep
  expires all items via the archive reason. Settings copy still accurate.

### From M2-G

- **No automated partition-drop retention yet** (I-11's remaining amber
  half): monthly partitions accumulate until an operator DETACH+DROPs them.
  A compliance window policy (env or per-deployment) + a pipeline job could
  automate it, but audit-retention duration is a human decision — deferred.
- The two-month partition cushion assumes the app runs at least once every
  ~2 months (runMigrations reconciles on every API request). A totally idle
  app + active pipeline crossing 2 month boundaries would fail outbox
  inserts until any app request lands. Documented in ADR 0012; acceptable.
- `history_retention` never prunes live-association ledger rows by design —
  a decade-old live flow keeps a decade of ingest_files. If M3 volumes make
  that a problem, the current-state/history split (rejected here) is the
  revisit path.
- Deleted-connection checks now flip to `failed`, which the flow monitor's
  health source ignores (deleted connections filtered) — no alert noise.

### Carried forward from M1

- ~~ADR 0009 leaves ASSOCIATION-delete reference semantics implicit~~
  **Settled in M2-F (ADR 0011)**: association delete removes its
  reference-backed items, mirroring connection delete; the dialog counts them
  and suggests disable-instead-of-delete.
- ~~Deleted-connection `connection_checks` claims stranding at `running`~~
  **Folded into M2-G**: the hourly history sweep flips them to `failed`.
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
