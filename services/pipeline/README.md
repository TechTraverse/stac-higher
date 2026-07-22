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

Both jobs only UPDATE `status`/`last_checked_at`/`last_error`/`host_key`/
`host_key_pinned_at` — **never `connections.updated_at`** (that means "user last
edited") — and never create the tables (ADR 0001).

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
  adapter, and runs each item through `deliver_item`: read the canonical asset
  bytes (`platform.get_object`), render the destination path (`delivery/path.py`
  — `{collection} {item_id} {filename} {yyyy} {mm} {dd}` tokens, UTC dates),
  write atomically via `adapter.put_atomic` (S3 direct PUT; SFTP/FTP
  `.part`→`move`), and record a `stac_higher.delivery_log` row
  (`pending`→`delivering`→`delivered`, or `failed`). A per-item failure marks
  that row `failed` without aborting the batch (retry → dead-letter is B-iii).

Ownership (ADR 0001): the pipeline reads `collection_connections`/`connections`
+ pgstac items and writes only `delivery_log`; the app owns the DDL (migrations
007 + 008).

**Slice B-i scope (current):** canonical bytes → S3/MinIO destination,
live-verified. Deferred to later slices: payload sidecars (item JSON / checksums
/ completion marker), `on_update`/`overwrite` policy (B-i delivers on every
event, overwrite-always), reference-mode source + S3→S3 server-side copy, retry
→ dead-letter, per-connection concurrency, and live SFTP/FTP destination runs.
See [`../../docs/ISSUES.md`](../../docs/ISSUES.md) I-43…I-46.

## Develop

Requires [uv](https://docs.astral.sh/uv/) (falls back to `python3 -m venv` +
`pip install -e ".[dev]"`).

```sh
cd services/pipeline
uv sync --extra dev      # create .venv and install
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
  "status": "ok",
  "queue": { "backend": "procrastinate", "reachable": true, "error": null },
  "heartbeat": { "count": 3, "last_run_at": "2026-07-14T12:00:00+00:00" }
}
```

## Docker

The `Dockerfile` builds a multi-stage image whose entrypoint applies the
Procrastinate schema idempotently, then runs the worker (with the periodic
heartbeat) and the health server in one process. The compose service is owned
by the docker-compose workstream; this package only ships the image.
