# ADR 0001 — Migration ownership for the shared Postgres

Status: accepted (Phase 0) · Date: 2026-07-14

## Context

One Postgres instance backs four concerns (ROADMAP §3): pgstac, the
`stac_higher` schema, the Procrastinate job queue, and (from Phase 5) the
`item_events` outbox. Today the Astro app applies `stac_higher` migrations
from middleware on the first API request. The new pipeline service also needs
database objects — Procrastinate's queue tables. The roadmap flags this as a
Phase 0 decision: one owner per schema, not two.

## Decision

Ownership is split **by schema**, with exactly one owner each:

- **`stac_higher`** — the Astro app's migration middleware remains the sole
  owner. When the pipeline needs `stac_higher` tables (Phase 4+:
  `collection_connections`, `ingest_files`, …), those migrations still land in
  the app's middleware mechanism; the pipeline consumes the tables but never
  migrates them.
- **`procrastinate`** (dedicated schema) — Procrastinate owns and applies its
  own DDL. The pipeline entrypoint runs the equivalent of
  `procrastinate schema --apply` idempotently on startup (create schema if
  absent, apply DDL only when `procrastinate_jobs` is missing), isolated via a
  per-connection `search_path`. Procrastinate version upgrades that ship queue
  migrations are applied by the same entrypoint path.
- **pgstac** — owned by the pgstac image/migrations, as today. The Phase 5
  outbox trigger is installed by the app's middleware (it is a `stac_higher`
  concern that references pgstac tables); revisit in Phase 5 if that proves
  awkward.
  *Amended 2026-07-30 (I-54):* the pgstac image installs its schema only via
  initdb on a **fresh** volume — bumping the image tag never migrates a
  persisted volume, which left a v0.9.11 image running a 0.9.10 schema during
  the M1 rehearsal. The compose `pgstac-migrate` one-shot (pipeline image,
  `pypgstac migrate`, gating `api`/`pipeline` startup) is the mechanism that
  makes this bullet true across upgrades: pgstac's own migrations, applied by
  pgstac's own tooling, pinned in lockstep with the pipeline's pypgstac
  loader.

## Consequences

- No cross-service migration races: each schema has one writer of DDL.
- The pipeline can start before/without the app for queue-only work, and the
  app never needs Procrastinate's library or schema knowledge.
- The app's middleware stays the single audit point for platform-entity
  schema changes, matching the compliance posture (reviewable, ordered
  migrations).
- Trade-off: pipeline features needing new `stac_higher` tables require a
  coordinated app release. Accepted — those tables are shared contracts and
  should be reviewed centrally anyway.
