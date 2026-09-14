# stac-higher-pipeline

The data-plane pipeline service for STAC Higher (ROADMAP §3). Phase 0 scope:
package skeleton, a queue interface with a Procrastinate (PostgreSQL) backend,
a no-op scheduled heartbeat job, and a `/health` endpoint.

Phases 1–5 add the connection test/health jobs, the ingest scheduler/workers,
and the delivery dispatcher/workers behind the same queue interface; retention
GC and the flow monitor land in Phase 6.

## Layout

```
src/pipeline/
  config.py                    # env-driven settings (see contract below)
  log.py                       # structured JSON logging (stdlib only)
  health.py                    # FastAPI /health app factory
  main.py                      # entrypoint: schema setup + worker + health server
  queue/
    interface.py               # QueueBackend ABC — business logic depends on this
    memory.py                  # in-memory backend for unit tests
    procrastinate_backend.py   # Procrastinate (LISTEN/NOTIFY) backend
  jobs/
    heartbeat.py               # periodic no-op heartbeat job
```

## Queue interface

Business logic never imports Procrastinate. It registers handlers and enqueues
work through `pipeline.queue.interface.QueueBackend`:

- `register_task(func, name=...)` / `register_periodic(func, name=..., cron=...)`
- `enqueue(job_name, payload)` / `enqueue_batch(job_name, payloads)` — jobs are
  batch-oriented (one job = N files/items) per the roadmap's topology decision
- `setup()` — idempotent one-time infrastructure prep (Procrastinate: apply its
  schema; SQS in Phase 8: validate queues)
- `run_worker()` — consume and execute jobs
- `check_connection()` — raises `QueueConnectionError` when the backend is down

The Procrastinate backend installs its objects into a dedicated `procrastinate`
PostgreSQL schema (see `docs/decisions/0001-migration-ownership.md`). An SQS
backend lands in Phase 8 as a second implementation of the same ABC.

## Environment contract

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://username:password@localhost:5433/postgis` | Postgres for the job queue **and** `stac_higher.connections`/`connection_checks` (compose-internal value: `postgresql://username:password@database:5432/postgis`) |
| `HEALTH_PORT` | `8083` | Port for the `/health` HTTP server |
| `QUEUE_SCHEMA` | `procrastinate` | PostgreSQL schema owned by Procrastinate |
| `LOG_LEVEL` | `INFO` | Root log level |
| `CREDENTIALS_MASTER_KEY` | _(unset)_ | base64-encoded 32-byte AES-256-GCM key, **identical to the app's**. Decrypts connection credentials. Absent at startup is tolerated — the connection drain/health-sweep ticks fail loudly (logged) instead of killing the process. |
| `EGRESS_ALLOW_HOSTS` | _(empty)_ | Comma-separated hostnames the egress policy permits even when they resolve to private/loopback addresses (e.g. the compose-internal test servers). Matched case-insensitively. |
| `ASSET_HREF_BASE` | `/api/assets` | Root-relative base path ITEMIZE uses when building an item's asset `href`s (`{ASSET_HREF_BASE}/{collection}/{item}/{filename}`) — must match the app's asset route (ADR 0005). |
| `INGEST_MAX_WINDOW_PREFIXES` | `1000` | The most prefixes one DISCOVER tick may expand an ingest `path_template` into (W-1). Exceeding it fails the tick loudly rather than listing for an hour — a 90-day window at hourly granularity is a configuration mistake, not a workload. |
| `CONNECTION_CHECKS_RETENTION_DAYS` | `30` | Age after which connection_checks rows are deleted by the hourly history sweep (M2-G). |
| `HISTORY_RETENTION_DAYS` | `365` | Window for pruning delivery_log/ingest_files rows of soft-deleted associations (and itemless terminal deliveries). |
| `GC_BATCH_ITEMS` | `500` | Max items one retention/collect sweep tick processes per collection (M2-F; backlogs drain across ticks). |
| `PGSTAC_QUEUE_DRAINER` | `pipeline` | Who runs `pgstac.query_queue` (M3-A). `pipeline`: the `pipeline.pgstac_queue_drain` tick `CALL`s `pgstac.run_queued_queries()` every minute. `database`: pg_cron owns the drain (RDS/Aurora — not in the local pgstac image) and the tick only samples depth/age, so the two never fight. `use_queue` and `update_collection_extent` are SESSION GUCs set automatically — the OPPOSITE pairing on each connection type: the writer's pool carries `use_queue` ON (queues the write); the drainer's connection carries `update_collection_extent` ON and `use_queue` explicitly FALSE (the nested extent refresh runs in whichever session drains the queue, not the one that writes). Neither pairing needs configuration anywhere. A new partition (new collection, or a new month on a `partition_trunc` collection) is invisible to datetime-ordered STAC search until this drain runs — a bounded ~1-minute blind window normally, unbounded if the drainer stops (I-114). |
| `PGSTAC_QUEUE_STALE_SECONDS` | `300` | The queue's oldest entry older than this logs a WARNING — the staleness bound on partition statistics; a rising `pipeline_pgstac_query_queue_oldest_seconds` means whichever drainer is configured has stopped. |
| `PGSTAC_QUEUE_HISTORY_DAYS` | `7` | `pgstac.query_queue_history` rows older than this are deleted by the same tick (pgstac never prunes that table). |
| `FINALIZE_STALE_SECONDS` | `1800` | A `staged_uploads` row stranded `finalizing` this long is presumed crashed — the finalize sweep flips it back to `pending` and re-enqueues the job (Phase 7 §6.4). |
| `WEBHOOK_MAX_ATTEMPTS` | `5` | Webhook notification attempts (including the first) before a `notification_deliveries` row dead-letters and raises a `webhook_failed` alert (M2-C, ADR 0010). |
| `WEBHOOK_RETRY_SECONDS` | `60` | Cool-off before the notify sweep re-enqueues a `failed` webhook delivery. |
| `WEBHOOK_TIMEOUT_SECONDS` | `10` | Per-POST webhook timeout. |
| `WEBHOOK_STALL_SECONDS` | `900` | A webhook delivery stranded `delivering` this long is presumed crashed and re-enters the retry path (counts as an attempt). |
| `PROCESS_RUNTIME_IMAGE` | `stac-higher-process-runtime:local` | The platform runtime image every `inline_python` run executes on (ADR 0013) — `runtime.runtime_image: "default"`. |
| `PROCESS_RUNTIME_IMAGE_STACTOOLS` | `stac-higher-process-runtime-stactools:local` | The image behind `runtime.runtime_image: "stactools"` (X-3, the built-in extractor library). **Empty** declares the deployment ships no such image: a run asking for the alias dies naming this variable rather than launching on the base image. |
| `DB_POOL_MIN` | `2` | Connections the process-wide async pool keeps warm (M3-B). The pool grows on demand and trims back after `max_idle` (600 s), so a mostly-idle deployment holds two backends, not `DB_POOL_MAX`. |
| `DB_POOL_MAX` | `16` | Ceiling on concurrent checkouts. **Size it as at least `WORKER_CONCURRENCY + 4`** — the worker's job concurrency (M3-D default 12) plus the periodic ticks that can overlap a job (dispatch poll, flow monitor, history sweep, GC). Too small does not error immediately: a caller waits `pool.timeout` (30 s) and then raises `psycopg_pool.PoolTimeout`, which surfaces as a failed job with a queue retry. Watch `requests_waiting` on `/health` — persistently non-zero means the pool is undersized. |

## Connections (Phase 2)

The pipeline is the only runtime that decrypts connection credentials and the
only one with the protocol adapters + egress policy (ROADMAP §5.2, ADR 0004).
All of this lives under `src/pipeline/connections/`:

- **`envelope.py`** — decrypt/seal the credential envelope (`0x01` version ‖
  12-byte nonce ‖ AES-256-GCM ciphertext+tag), byte-for-byte compatible with
  the app's `crypto.ts`. A cross-runtime known-answer test locks the format.
- **`egress.py`** — deny-by-default SSRF guard: resolves the target host and
  blocks loopback/private/link-local/unique-local/multicast/reserved and the
  cloud metadata IP (v4 + v6, incl. IPv4-mapped forms). `EGRESS_ALLOW_HOSTS`
  is the only escape hatch. Every adapter calls it before opening a socket.
- **`adapters/`** — `StorageAdapter` ABC (`test/list/get/put/delete`) with
  `S3Adapter` (boto3), `SftpAdapter` (asyncssh; serves `ssh` + `sftp`, exposes
  the server host key), `FtpAdapter` / `FtpsAdapter` (aioftp; FTPS does
  implicit or explicit TLS). `adapter_for(row, credentials)` is the factory;
  `stac-api` raises `NotImplementedError`.
- **`adapters/tofu.py`** — pure trust-on-first-use decision: first-pin when no
  key is stored, match, or a hard-fail mismatch.
- **`repo.py`** / **`probe.py`** — the DB seam over the two tables and the
  decrypt→adapter→TOFU pipeline both jobs share.

### Jobs

| Job (periodic name) | Cron | What it does |
|---|---|---|
| `pipeline.connection_check_drain` | `* * * * *` | Drains **all** pending `connection_checks` rows (`FOR UPDATE SKIP LOCKED`), runs `adapter.test()`, writes the check `result` + updates the parent connection's health and TOFU pin. |
| `pipeline.connection_health_sweep` | `*/5 * * * *` | Tests every enabled connection and updates its health columns (no `connection_checks` rows). |
| `pipeline.pgstac_queue_drain` | `* * * * *` | M3-A. Samples `pgstac.query_queue` (depth + oldest age → `/metrics`), `CALL`s `pgstac.run_queued_queries()` when `PGSTAC_QUEUE_DRAINER=pipeline`, prunes `query_queue_history`. The queue holds the partition-statistics refreshes the writer defers through its `use_queue` session GUC; its depth is bounded by partitions written, not items. |

The two connection jobs above only UPDATE `status`/`last_checked_at`/
`last_error`/`host_key`/`host_key_pinned_at` — **never `connections.updated_at`**
(that means "user last edited") — and never create the tables (ADR 0001).

**Drain cadence (accepted deviation):** Procrastinate's periodic scheduler is
1-minute-granular, so ADR 0004's "~10 s" drain target is approximated by a
1-minute tick that clears the whole pending backlog at once. True sub-minute
latency needs a NOTIFY-woken drain — flagged in ADR 0004 "Revisit", not built in
Phase 2.

### Integration test servers

`infra/compose.test-servers.yml` (repo root) stands up throwaway SFTP/FTP/FTPS
servers on the compose network for the **lead** to run adapter integration
against (S3 reuses MinIO). Unit tests here mock every external client and DNS —
no live servers, no Docker.

## Ingest (Phase 4)

Poll-based ingest of files from source connections into built-in-catalog
collections (ROADMAP §6.1). One `IngestAssociation` (`stac_higher.
collection_connections`, `direction = 'ingest'`) runs the pipeline:

```
poll → DISCOVER → GROUP → FETCH → EXTRACT → ITEMIZE → post-ingest
```

- **poll / DISCOVER** (`ingest/scheduler.py`, `ingest/discover.py`) — the
  `ingest_poll` periodic job enqueues one DISCOVER job per enabled association
  every N whole-minute ticks (`config.poll_frequency_seconds`, Procrastinate's
  1-minute granularity). DISCOVER lists the source, normalizes paths relative
  to `source_path`, filters by `include`/`exclude` globs, and runs the
  **settled check**: a file's size/fingerprint must be unchanged across two
  consecutive polls before it's eligible — protects against picking up a
  file mid-upload. A fingerprint change on an already-`itemized` file is a new
  version of the same product (re-ingest).
- **Date window (W-1, `ingest/window.py`)** — three optional config fields
  bound what a tick takes IN, so an association can point at a public
  archive the size of NODD (`noaa-goes19`, ~250,000 objects per product)
  safely. `window.begin` / `window.end` are each an RFC3339 timestamp or a
  `-<n>[smhd]` offset, re-resolved against *now* on every poll (that is what
  makes a rolling window roll; `end` absent = open to now); only entries whose
  `FileEntry.mtime` falls in the half-open `[begin, end)` are admitted, and
  an entry with **no** mtime is skipped and counted (`undateable`) once a
  window is set. `path_template` (tokens `{Y}` `{m}` `{d}` `{j}` `{H}`,
  zero-padded, UTC; granularity = the finest token present) is appended to
  `source_path` and expanded from the window into the prefixes worth listing,
  so the LISTING is bounded and not just its result — `"ABI-L2-MCMIPC/"` +
  `"{Y}/{j}/{H}/"` + `{begin: "-6h"}` lists six hourly prefixes of ~12 keys
  instead of one prefix of ~250,000. `max_files_per_poll` caps how many NEW
  files one tick admits, oldest first, so a wide window becomes a paced
  backfill (known files never count against it). Counters on the tick log:
  `prefixes_listed`, `out_of_window`, `undateable`, `deferred_by_cap`. The
  window governs what comes in, never what stays — an item whose file ages
  out of the window remains in the catalog under retention alone.
- **GROUP** (`ingest/group.py`) — `grouping.rule: none` itemizes each settled
  file immediately as its own item; `shared_basename` waits for sibling files
  sharing a basename, up to `timeout_seconds`, then applies `on_timeout`
  (`ingest_partial` | `discard`).
- **FETCH** (`ingest/fetch.py`) — copy-mode only: buffered `adapter.get` →
  `platform.put_object` into canonical storage at
  `assets/{collection}/{item}/{filename}`, sha256 checksum recorded, ledger
  row → `stored`. `storage_mode: reference` associations stop at `settled`
  (Slice C consumes them — see [`../../docs/ISSUES.md`](../../docs/ISSUES.md) I-21).
- **EXTRACT** (`ingest/extract.py`) — turns a group's `stored` members into a
  STAC item dict per the association's `metadata.strategy` (§5.1):
  `raster_auto` (rio-stac/pystac over an in-memory `rasterio.MemoryFile` read
  of the primary raster — no GDAL S3 config needed since bytes are already in
  canonical storage), `sidecar` (parse an adjacent XML — via `defusedxml`,
  hardened against XXE and entity-expansion DoS — or JSON sidecar file), or
  `defaults_only` (a null-geometry item from collection defaults). Every
  non-primary member becomes an additional asset; a member sharing the
  primary's filename stem always loses to the `data` asset. Asset hrefs point
  at `{ASSET_HREF_BASE}/{collection}/{item}/{filename}`. A field that can't be
  resolved raises `ExtractError` rather than emitting a bad item.
- **ITEMIZE** (`ingest/itemize.py`, `stac/pgstac_writer.py`) — `run_itemize`
  re-reads each source file's latest ledger row and acts only on members still
  `stored` (idempotent, restart-safe), calls EXTRACT, validates the item with
  the **core** `stac_pydantic.Item` model (offline, core-structural gate —
  intentionally not `stac_pydantic.api.Item`, which requires a `root` link
  EXTRACT-built items don't carry), then upserts via **pypgstac**
  (`Loader.load_items(..., insert_mode=Methods.upsert)` in a thread) — verified
  to write item data only via temp `ON COMMIT DROP` staging tables and pgstac's
  own upsert functions, no DDL (ADR 0001-compatible). EXTRACT failure or a
  validation failure marks the members `failed`; a missing collection is a
  permanent `failed` (`CollectionMissing`); any other upsert error propagates
  so the job retries. On success the members go `itemized` and post-ingest
  runs. See [ADR 0006](../../docs/decisions/0006-ingest-metadata-and-upsert.md)
  for the library choices (pinned `rio-stac`/`pystac`/`rasterio`/`defusedxml`/
  `stac-pydantic`/`pypgstac[psycopg]`, why the rasterio wheels need **no
  Dockerfile change** (bundled GDAL, no system install), and why the
  `pgstac` image is pinned to `v0.9.11` to stay in lockstep with the pinned
  `pypgstac` client).
- **post-ingest** (`ingest/postingest.py`) — `leave` (default) no-ops,
  `delete` removes the source files, `move:<path>` copies then deletes.
  Non-fatal: a failed source cleanup is logged but never fails the job or
  reverts the ledger (the item is already catalogued).

`jobs/ingest.py` registers each stage as a queue task and chains them,
idempotent against the `ingest_files` ledger throughout.

## Delivery (Phase 5)

Event-driven push of catalog items to delivery destinations (ROADMAP §6.4). A
pgstac item change is captured by the app-owned outbox trigger into
`stac_higher.item_events` (migration 007,
[ADR 0007](../../docs/decisions/0007-outbox-trigger-ownership.md)); the pipeline
drains the outbox, matches delivery associations, and copies asset bytes to the
destination:

```
pgstac item change → item_events (trigger) → dispatch → deliver
```

- **dispatch** (`dispatcher/loop.py`, `dispatcher/repo.py`) — the
  `pipeline.dispatch_poll` periodic job (`* * * * *`) claims pending outbox rows
  (`FOR UPDATE SKIP LOCKED`, id order), skips `delete` events (deletions never
  propagate, §6.4), reads each item via `pgstac.get_item`, matches enabled
  `direction='deliver'` associations (`delivery/matcher.py`: CQL2 `item_filter`
  + `asset_keys`), and groups the matches into one batched `pipeline.deliver`
  job **per association**. It enqueues **before** marking the outbox rows
  processed (at-least-once; a failed enqueue leaves them pending). Slice C swaps
  the poll for a `LISTEN`-woken loop.
- **deliver** (`delivery/worker.py`, `delivery/repo.py`, `jobs/dispatch.py`) —
  the `pipeline.deliver` task loads the destination connection, builds its
  adapter, and runs each item through `deliver_item`: resolve each asset's
  source bytes (canonical `platform.get_object`, the ingest source adapter for
  reference-mode assets, or an S3→S3 server-side copy — see below), render the
  destination path (`delivery/path.py` — `{collection} {item_id} {filename}
  {yyyy} {mm} {dd}` tokens, UTC dates), apply the association's
  `on_update`/`overwrite` policy, write atomically via `adapter.put_atomic`
  (S3 direct PUT; SFTP/FTP `.part`→`move`) or `adapter.copy_object_from` for a
  server-side copy, write any payload sidecars, and record a
  `stac_higher.delivery_log` row (`pending`→`delivering`→`delivered`, or
  `failed`). A per-item failure marks that row `failed` without aborting the
  batch (retry → dead-letter is B-iii).

Ownership (ADR 0001): the pipeline reads `collection_connections`/`connections`
+ pgstac items and writes `delivery_log` plus (M2-A) the
`collection_connections.flow_stats` telemetry column — in the same transaction
as each `delivery_log` status change, never touching `updated_at` (that column
means "user edit"); the app owns the DDL (migrations 007, 008 + 009).

**Slice B-i scope:** canonical bytes → S3/MinIO destination, live-verified
2026-07-21.

**Slice B-ii scope (done; live-verified 23/23 on 2026-07-22):**

- **`delivered_assets`** (migration `009_delivery_log_delivered_assets`) —
  `delivery_log.delivered_assets` jsonb, a per-asset `{fingerprint, size,
  filename}` map. Fingerprints are `sha256:<hex>` (streamed) or
  `etag:<etag>/<size>` (server-side copy) — the two kinds compare unequal, so
  a transfer-path switch costs at most one redundant redeliver (I-47).
  `upsert_pending`'s redelivery conflict branch resets `attempts = 0` —
  resolves I-44.
- **`on_update`/`overwrite`** (`delivery/worker.py`) — an item-level
  `on_update` gate (`ignore` fires once per item, keyed off a prior
  `delivery_log` row's status, never the outbox `op` — I-37) and a per-asset
  log-based `overwrite` policy (`never`/`always`/`if_newer` against
  `delivered_assets`, no destination round-trip).
- **Payload sidecars** (`delivery/payload.py`) — a coreutils-format checksum
  per written asset (`{filename}.{algo}`), the item JSON rewritten on every
  processed event (`{item_id}.json`), and a completion marker
  (`{item_id}.done`, a JSON manifest) written **last**, only when something
  was actually written.
- **Reference-mode source** — bytes read through the ingest source
  connection's adapter: ledger-first (`DeliveryRepo.load_reference_sources`
  over `ingest_files`), the adapter built lazily per connection
  (`build_adapter`, decrypting only when invoked) and cached per item — no
  HTTP client.
- **S3→S3 server-side copy** (`delivery/transfer.py`) — `can_server_side_copy`
  gates on an s3 destination whose endpoint normalizes equal to the
  platform's `STAGING_S3_ENDPOINT` (both `None` = real AWS; a malformed
  endpoint degrades to streaming), computed once per job in
  `jobs/dispatch.py`; `S3Adapter.copy_object_from` performs the `CopyObject`,
  falling back to streaming on failure. A `sha256` payload checksum forces
  streaming (no hash without the bytes); `md5` can ride a single-part
  object's ETag (`platform.head_object`), but a multipart ETag isn't an md5
  and falls back to streaming too.

Pipeline suite 306 passed/2 skipped, ruff clean. Code done, live verification
pending (a later lead-only task) — not yet claimed live-verified.

Deferred to **Slice B-iii**: retry → dead-letter, per-connection concurrency
caps, and live SFTP/FTP destination runs. See
[`../../docs/ISSUES.md`](../../docs/ISSUES.md) I-43, I-45, I-47.

## Finalize (Phase 7, P7-E)

The ADR 0014 producer-parameterized staging→canonical seam
(`src/pipeline/finalize/`): push-ingest is the first caller, Phase 9 process
runs plug in without a parallel path.

- **Seam** (`seam.py`) — `FinalizeRequest {producer, staging_prefix,
  output_collections, items, provenance}` → `FinalizeResult {upserted,
  rejected}`. The neutral steps (`steps.py`: platform pre-flight → rewrite →
  validate → checksum → copy-verify move → upsert restricted to
  `output_collections` → delete staged originals) contain **no producer
  branching** — a source-level test pins it. Producer differences live in the
  request plus a *resolver*/*recorder* pair registered per producer.
- **Push producer layer** (`push.py`) — the resolver claims the
  `staged_uploads` row (`pending → finalizing`, item-bound; concurrent
  duplicates and already-terminal rows no-op with a `stale_claim`
  metric/log) and enforces the §4.2 admission rules (single session both
  directions, minted-for-this-collection, unbound-or-bound-here); the
  recorder stamps the verdict and applies the §6.3 op-discriminated
  rejection outcome: **insert → delete** (GC-marking the canonical prefix
  first when bytes already moved — ADR 0011), **brokered update → restore**
  the `prior_item` snapshot (`restored: true`), **direct update →
  leave-broken**. A sweep-recovery run (no recorded op) takes the
  conservative branch: restore if a snapshot exists, otherwise leave —
  never delete.
- **Validation** — `stac/validate.py`, the exact gate ITEMIZE uses (lifted
  from `ingest/itemize.py`; §6.2 — pushed and polled items pass identically).
- **Jobs** (`jobs/finalize.py`) — `pipeline.finalize` (payload
  `{upload_id, collection_id, item_id, event_op}`, enqueued by the P7-F
  dispatcher before draining the staged event) and the five-minute
  `pipeline.finalize_sweep` (§6.4: expire `pending` past
  `created_at + STAGING_TTL_SECONDS` — the §4.1 governing clock — and
  requeue stale `finalizing` claims).
- **Ledger clock** — `jobs/staging_cleanup.py` now skips `staging/` prefixes
  whose ledger row is non-terminal and younger than the TTL (no-row prefixes
  keep the mtime rule; an unreadable ledger skips the tick — never delete
  blind).
- **Metrics** (§9) — `pipeline_finalize_items_total{producer, outcome}` and
  `pipeline_finalize_bytes_total{producer}`; the job/sweep get
  run/duration/outcome from the central `instrument_handler` wrap.

## Develop

Requires [uv](https://docs.astral.sh/uv/) (falls back to `python3 -m venv` +
`pip install -e ".[dev]"`).

```sh
cd services/pipeline
uv sync --extra dev      # create .venv and install
uv sync --extra dev --extra stactools   # + the built-in extractor library's eleven
                                        #   packages (X-2): tests/test_stactools_adapters.py
                                        #   skips without them; CI installs them
uv run pytest            # unit tests (no database needed)
uv run ruff check .      # lint
uv run pipeline          # run the service (needs Postgres per DATABASE_URL)
```

DB-integration tests auto-skip unless `DATABASE_URL` is set:

```sh
DATABASE_URL=postgresql://username:password@localhost:5433/postgis uv run pytest
```

## Health endpoint

`GET /health` on `HEALTH_PORT` returns `200` when the queue backend is
reachable, `503` otherwise:

```json
{
  "service": "pipeline",
  "version": "<x.y.z>",
  "status": "ok",
  "queue": { "backend": "procrastinate", "reachable": true, "error": null },
  "heartbeat": { "count": 3, "last_run_at": "2026-07-14T12:00:00+00:00" },
  "db_pool": {
    "database:5432/postgis": {
      "pool_min": 2, "pool_max": 16, "pool_size": 4,
      "pool_available": 3, "requests_waiting": 0
    }
  }
}
```

`db_pool` (M3-B) reports `psycopg_pool` stats per database, keyed by a
redacted DSN identity (`host:port/dbname` — never the user or password). It is
`{}` until the first pooled connection is opened, and it does **not** affect
the 200/503 decision: the queue's own `check_connection()` is the database
liveness signal, and an idle process with no pool is healthy. Cumulative
counters (`connections_num`, `requests_num`, `requests_queued`, `usage_ms`)
appear only once they are non-zero.

## Connection pooling (M3-B)

Every repo statement checks out of a process-wide `psycopg_pool.AsyncConnectionPool`
(`src/pipeline/db/pool.py`), keyed by DSN and opened lazily on first use.
Before M3-B each repo method forked its own backend — 4.19 ms measured, ~14
per ingested item, ~420 connections/s at the M3 budget (scoping notes M3-S-D).

- Each pooled connection runs the two pgstac session GUCs once, when it is
  created (`configure=configure_pgstac_session_async`, spec §4.2; ADR 0020,
  `docs/decisions/0020-pgstac-session-guc-pairings.md`). They are harmless on
  the non-pgstac statements the repos issue.
- `pool.connection()` commits on success and rolls back on exception, exactly
  as a directly-opened connection does — transaction boundaries are unchanged.
- **Not pooled, deliberately:** Procrastinate's `PsycopgConnector` (it owns
  its own pool); the dispatch listener's dedicated autocommit LISTEN
  connection; `ProcrastinateQueue.setup()` / `check_connection()` — the first
  runs before anything else exists, the second *is* the health probe; and the
  pgstac queue drainer (`stac/query_queue.py`), because `CALL
  pgstac.run_queued_queries()` COMMITs inside itself and Postgres refuses that
  inside a transaction block, so it needs an autocommit connection.
- The pgstac UPSERT path keeps a separate **sync** pool (M3-A,
  `writer_pool()` / `WRITER_POOL_MAX = 4` in `stac/pgstac_writer.py`):
  pypgstac is synchronous, runs inside `asyncio.to_thread`, and sets
  `autocommit=True` on its checkouts. Both pools carry the same GUCs through
  the same `db/pgstac_session.py` hook.
- `main.run()` closes both pools in its `finally`, before `queue.aclose()`.
- The pool warms lazily on first use, and that warm-up is bounded by
  `POOL_OPEN_TIMEOUT_SECONDS` (10 s, `pipeline/db/pool.py`). A
  `PoolTimeout("pool initialization incomplete after 10 sec")` raised at first
  use means the database or the `configure` hook is failing (psycopg_pool
  logs the cause at WARNING) — a different thing from the 30 s checkout
  timeout above, which means the pool is undersized.

Measured on the compose stack, `loadgen --label m3b`, 900 items at 30/s,
`--mode copy --metadata defaults_only`, `pg_stat_database.sessions` delta per
catalogued item: **19.5 → 0.10 new backend sessions per item** (2026-09-09
baseline on the pre-pool M3-A build; 2026-09-14 pooled build — 17,570 → 92
sessions for the same 900 items; the pool held its two warm connections for
17,895 checkouts with `requests_waiting: 0`). `ingest_fetch` mean 24 → 12 ms,
`ingest_itemize` 32 → 9 ms; the pipeline kept pace with the 30/s feed where the
baseline lagged at 21–24 items/s. Laptop numbers — they rank the fix, they are
not platform capacity.

## Docker

The `Dockerfile` builds a multi-stage image whose entrypoint applies the
Procrastinate schema idempotently, then runs the worker (with the periodic
heartbeat) and the health server in one process. The compose service is owned
by the docker-compose workstream; this package only ships the image.


## Telemetry (M2-H)

`GET :8083/metrics` serves Prometheus exposition (no scraper ships in
docker-compose — curl-verifiable; ROADMAP §8). Instrument map
(`src/pipeline/metrics.py`):

- `pipeline_job_runs_total{job,outcome}` / `pipeline_job_seconds{job}` — every
  queue task and periodic tick, wrapped centrally at Procrastinate
  registration (new jobs are covered automatically; the in-memory test
  backend stays bare).
- `pipeline_ingest_events_total{stage}` (`settled_file` / `itemized_item` /
  `failed`) + `pipeline_ingest_bytes_total` — incremented at the M2-A
  flow-stats choke points.
- `pipeline_deliveries_total{outcome}` (`delivered`/`failed`/`dead`),
  `pipeline_delivery_bytes_total`, `pipeline_delivery_seconds` — the delivery
  worker's terminal transitions.
- `pipeline_webhook_deliveries_total{outcome}` and
  `pipeline_alerts_total{event}` (`raised`/`auto_resolved`).
- `pipeline_pgstac_query_queue_depth`, `pipeline_pgstac_query_queue_oldest_seconds`
  (gauges, set by the drain tick) and `pipeline_pgstac_query_queue_queries_total{outcome}`
  — the only outside-the-process evidence the writer's session-scoped
  `use_queue` is in effect, and the alarm for a drainer that stopped (M3-A).
  `pipeline_pgstac_query_queue_drain_failures_total` counts ticks whose `CALL`
  did not complete at all, and is deliberately NOT folded into
  `..._queries_total{outcome="error"}`: that label counts individual queued
  STATEMENTS pgstac ran and recorded an error for (a dropped partition, say),
  which is routine, while a drain failure means nothing ran and the queue is
  growing. Alert on the failure counter and on the age gauge; the statement
  errors are a lower-priority signal.

Logging is structured JSON via `log.py` (`configure_logging`); log data
belongs in `extra={...}` fields, not the message string.
