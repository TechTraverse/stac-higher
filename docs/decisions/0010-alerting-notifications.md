# ADR 0010 — Alerting & notification model

**Status:** accepted (M2-B implemented the alerting half, M2-C the
notification half)

## Context

Phase 6's gate requires that a stopped data flow "raises an alert within the
declared expectation window **and notifies the group's channels**" (ROADMAP
§6.6, M2 spec §3–§4). M2-B landed detection: the pipeline's `flow_monitor`
reconciles `stac_higher.alerts` each tick (raise / `last_seen` re-fire /
auto-resolve, deduped per condition). What remained open was how a raised
alert *reaches* people, and where webhook egress may live given that the
app's `safeFetch` deliberately blocks private/loopback targets.

## Decision

**Channels.** `notification_channels` rows are per group, `kind ∈ {in_app,
webhook}` (email/SMTP deferred; the shape admits it later). The app owns the
DDL (migrations 014/015) and the CRUD (`/api/channels*`, operator+ mutations,
audited, group-owned; the webhook signing secret is write-only — responses
carry `has_secret`).

**In-app needs no transport.** The alerts row itself plus a per-user read
watermark (`alert_reads`, one row per user) IS the in-app delivery. Unread =
firing alerts first *raised* after the caller's watermark, so a `last_seen`
bump does not re-unread an already-seen condition but a resolve→re-fire cycle
(a new row) does. `POST /api/alerts/read` moves only the caller's own
watermark and is therefore NOT operator-gated or audited; per-row triage
stays on the audited `ack`/`resolve` verbs.

**Webhook egress lives pipeline-side.** A webhook target is
operator-declared configuration — the same trust class as a connection
endpoint, not a request-time user-supplied URL — so dispatch sits next to the
connections egress policy and reuses `resolve_pinned` verbatim: resolve and
range-check once, dial the pinned IP (SNI/Host keep the hostname), fail
closed on private/loopback/metadata targets unless `EGRESS_ALLOW_HOSTS`
vouches for the host. The app's `safeFetch` guard is NOT widened. POSTs are
JSON (`{"event": "alert.firing", "alert": {…}}`), optionally signed with
HMAC-SHA256 over the raw body (`X-StacHigher-Signature: sha256=<hex>`) when
the channel config carries a `secret`. Non-2xx (redirects included) is a
failure — following a redirect would re-open the egress question at a new
host.

**Durability is ledger-shaped, like every other transfer.** Fan-out and
retry mirror `delivery_log`, not queue-level retry:

1. The periodic `pipeline.notify_sweep` fans each firing, un-notified alert
   (`alerts.notified_at IS NULL`) out into `notification_deliveries` — one
   row per (alert, group webhook channel), `UNIQUE (alert_id, channel_id)`
   making the fan-out idempotent — then stamps `notified_at` (at-least-once
   across a crash).
2. One `pipeline.webhook_notify` job per dispatchable row claims it
   (`pending|failed → delivering`) and POSTs. Outcomes are *recorded*, never
   re-raised: success → `delivered`, failure → `failed` with the error and
   an attempt count. The sweep re-enqueues `failed` rows after
   `WEBHOOK_RETRY_SECONDS` and revives rows stranded `delivering` past
   `WEBHOOK_STALL_SECONDS`.
3. At `WEBHOOK_MAX_ATTEMPTS` the row dead-letters and raises a
   `job_failure`/`webhook_failed` alert **anchored to the channel**
   (`alerts.channel_id`, the third anchor leg added in migration 015) — a
   channel that cannot notify must itself be visible. Fan-out never routes an
   alert through the channel it is about, and dedup bounds the cascade to
   one open alert per channel. The next successful delivery to that channel
   auto-resolves it.

**Only NEW rows notify** (spec §3.3): a `last_seen` bump is detection
continuing, not news; an `acknowledged` alert was ack'd before fan-out saw it
— ack suppresses notification, not detection; manual resolve of a persisting
condition produces a new row on the next tick, which re-notifies.

**Contract.** The webhook channel `config` (`{url, secret?}`) is a
cross-runtime contract: Zod (`app/src/lib/notifications/schemas.ts`, strict,
http/https only) writes it; `pipeline/notify/config.py` (lenient; scheme and
egress enforced at dispatch) reads it. Golden fixture:
`tests/contract-fixtures/webhook-channel-config.json`.

## Consequences

- Notification inherits the platform's one recovery idiom (ledger + sweep +
  dead-letter + alert) — no second retry mechanism to reason about, and the
  M2-I rehearsal exercises the same shape everywhere.
- Webhooks are at-least-once: a stall revival or duplicate enqueue can
  deliver twice. Consumers must treat the POST as idempotent per alert id.
- The alerts dedup identity is now `(source, kind, connection_id,
  association_id, channel_id)`; the pipeline's upsert conflict target and the
  migration-015 partial index must change in lockstep.
- Auto-resolve and manual resolve do not notify ("recovered" webhooks are a
  possible later addition; the `event` field in the payload leaves room).
- Email lands later as a third `kind` plus a dispatcher — the ledger,
  fan-out, and CRUD need no change.
