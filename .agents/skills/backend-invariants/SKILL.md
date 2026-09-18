---
name: backend-invariants
description: The cross-cutting backend rules that hold on every task — schema ownership, cross-runtime contract fixtures, the app→pipeline bridge, catalog write paths, RBAC + audit, egress, process-run isolation, byte deletion, pipeline logging — plus the backend gotchas. Read before touching services/pipeline, app/src/pages/api, migrations, tests/contract-fixtures, storage/GC, process runs, Docker images or the compose stack.
---

# Backend invariants

These hold on every task. The per-area design lives in `docs/` (index:
`docs/README.md`; route table + env: `docs/backend.md`). Each rule names the
ADR that decided it — read the ADR before arguing with the rule.

## Rules

- **Schema ownership**: the app owns every `stac_higher.*` DDL (migrations
  run on the first API request; append to `MIGRATIONS` in
  `app/src/lib/db/migrate.ts`, next number, never reorder). The pipeline
  reads and writes rows, never DDL (ADR 0001). Partitioned tables are
  reconciled by `runMigrations()` (ADR 0012). Migration numbers are reserved
  on the GitHub issue (`migration` label) — check before taking one.
- **Cross-runtime contracts**: any shape shared by app and pipeline
  (association `config`, channel config, manifests, status strings, alert
  kinds) has a golden fixture in `tests/contract-fixtures/` consumed by
  **both** vitest and pytest. A new or changed shape ⇒ a new/updated fixture
  (that directory's README has the format; the `new-test` skill has the
  test pattern).
- **App → pipeline** requests are rows the pipeline drains
  (`connection_checks`, `process_checks`, `delivery_backfills`, …), never a
  direct call (ADR 0004).
- **Catalog writes** go only to the built-in catalog, through the BFF
  `/api/catalog/*` (ADR 0008); `stacFetch` refuses writes to any other
  catalog — external catalogs are read-only (I-89). Direct-to-proxy writes
  are gated by the ADR 0015 policy.
- **RBAC & audit**: API mutations need `operator`/`admin` (guard in
  `src/middleware.ts`; register the route in `app/src/lib/authz/permissions.ts`)
  and write one append-only `audit_log` row each; the dev-bypass identity is
  an operator. Group ownership is enforced inside the route (a row outside
  the caller's groups is a 404). Credentials and secrets are write-only in
  every API.
- **Egress**: server fetches go through `safeFetch` (blocks
  private/loopback; dev allow-list in `docs/backend.md`). Webhook dispatch is
  pipeline-side behind the connections egress policy — never widen
  `safeFetch` for it.
- **Process runs** see only their revision's env, run-scoped STS credentials
  and their code — never the DB URL, master key or platform keys (ADR 0013).
  Author contract: `docs/processes.md`.
- **Byte deletion** happens only through the `asset_gc` mark-then-collect
  queue (ADR 0011); nothing is deleted on an unconfigured platform.
- **pgstac session GUCs** are opposite on the writer and the drainer
  (ADR 0020): the writer pool carries `use_queue` + `update_collection_extent`
  ON; the queue drainer runs `CALL pgstac.run_queued_queries()` on a
  short-lived autocommit connection with the opposite pairing. Do not "fix"
  one side to match the other.
- **Pipeline logging**: data goes in `extra={...}` structured fields, never
  interpolated into the message.

## Gates

Whenever the pipeline is touched: `uv run pytest` and `uv run ruff check .`
from `services/pipeline/` (`DATABASE_URL=…` enables the DB-gated tests;
reference: `services/pipeline/README.md`), in addition to `npm run verify`.
Docker, the load harness and the standing GOES demo are lead-only
singletons.

## Gotchas

- The repo-root `.dockerignore` excludes `infra/`, `services/`, `docs/` and
  `tests/`. A new repo-root-context derived image (pattern:
  `infra/proxy-policy`, `infra/titiler`) must re-include exactly the path it
  `COPY`s or its build fails with `"/<path>": not found`. Cross-runtime
  fixtures reach images as a named build context (`fixtures`), never a
  vendored copy.
- Keycloak realm import is skipped once the realm exists in the persisted
  volume — realm-file edits need `docker compose down -v` (never run that on
  a stack carrying the standing demo without the lead).
- A pipeline container built before the current migrations spams errors
  into the shared DB's logs — after merging migration-bearing work,
  `docker compose build pipeline && docker compose up -d pipeline`.
- Probes against the load harness are always labelled
  (`pipeline.loadgen --label <id>`) and always torn down; restart the
  pipeline process before measuring RSS (a long-lived process's high-water
  mark hides the per-item transient).
