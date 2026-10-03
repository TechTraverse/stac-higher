# STAC Higher

A platform for **ingesting, cataloging and disseminating geospatial products**.
Files arrive through connections (S3, SFTP, FTP, push), become
[STAC](https://stacspec.org/) items in a built-in catalog backed by
platform-owned object storage, can be transformed by operator-authored
processes, and are pushed out to delivery destinations in near real time. It
runs locally with one `docker compose up` and is built to be cloud-portable
(AWS first, GovCloud-compatible) for gov/defense-adjacent deployments.

It started as a STAC client (browse, search, edit collections and items across
several catalogs on a map) and that client is still the front door.

## What it does

| Area | In one line | Reference |
|---|---|---|
| Catalog client | Browse, search, create and edit STAC collections and items; map footprints; multi-catalog; custom extensions via JSON Schema forms | [`docs/FEATURES.md`](docs/FEATURES.md) |
| Connections | Group-owned source and destination endpoints with encrypted, write-only credentials, host-key pinning and an egress policy | [`docs/connections.md`](docs/connections.md) |
| Ingest | Poll a connection, group files into products, extract metadata, upsert items; `copy` bytes into platform storage or `reference` them in place; date windows and retention caps | [`docs/FEATURES.md`](docs/FEATURES.md) Phase 4, [`docs/push-ingest.md`](docs/push-ingest.md) |
| Processes | Operator-authored Python that turns input items into output items in an isolated container; built-in extractors from curated stactools packages; lineage links | [`docs/processes.md`](docs/processes.md) |
| Delivery | Event-driven fan-out of assets (plus optional item JSON, checksums, markers) to destinations, single-digit-second latency plus transfer | [`docs/FEATURES.md`](docs/FEATURES.md) Phase 5 |
| Monitoring | Flow telemetry, alerts, notification channels and webhooks, Prometheus metrics, retention and GC | [`docs/monitoring.md`](docs/monitoring.md) |
| Serving | OGC API Tiles and Features for the catalog's rasters and vectors; a map page that stacks products on one time axis | [`docs/serving.md`](docs/serving.md) |
| Auth | OIDC login with pluggable claims mapping, group-scoped RBAC, append-only audit log | [`docs/auth.md`](docs/auth.md) |

The long-term plan, locked decisions and phase status: [`ROADMAP.md`](ROADMAP.md).

## Tech stack

- **App** (`app/`): [Astro 7](https://astro.build/) server-rendered pages, each mounting one [React 19](https://react.dev/) island. State in three tiers: [nanostores](https://github.com/nanostores/nanostores) for cross-island persistence, [TanStack Query](https://tanstack.com/query) for server state, [React Hook Form](https://react-hook-form.com/) + [Zod](https://zod.dev/) for forms. UI from [shadcn/ui](https://ui.shadcn.com/) (Radix + Tailwind), maps with [MapLibre GL](https://maplibre.org/) via `react-map-gl`. The app also hosts the platform's API routes (`app/src/pages/api/`) and owns the database schema.
- **Shared package** (`packages/shared/`): components, hooks, stores, map utilities and the RJSF form theme, with Storybook.
- **Pipeline** (`services/pipeline/`): Python worker and scheduler. [Procrastinate](https://procrastinate.readthedocs.io/) (Postgres LISTEN/NOTIFY) behind a queue interface, [rio-stac](https://github.com/developmentseed/rio-stac) / [pystac](https://pystac.readthedocs.io/) for metadata, [obstore](https://github.com/developmentseed/obstore) and fsspec/paramiko for I/O, GDAL through rasterio.
- **Process runtime** (`services/process-runtime/`): the container image user processes run in, plus a stactools variant.
- **Backend services** (`docker-compose.yml`, `infra/`): [pgstac](https://github.com/stac-utils/pgstac) on PostGIS, [stac-fastapi-pgstac](https://github.com/stac-utils/stac-fastapi-pgstac), [stac-auth-proxy](https://github.com/developmentseed/stac-auth-proxy), Keycloak, MinIO, [titiler-pgstac](https://github.com/stac-utils/titiler-pgstac), [tipg](https://github.com/developmentseed/tipg). Ports and credentials: [`docs/backend.md`](docs/backend.md).

## Architecture in brief

Postgres (pgstac) is the system of record for both the STAC catalog and the platform's own `stac_higher.*` tables. The app owns every migration; the pipeline reads and writes rows. Catalog writes go through the app's BFF routes to the built-in catalog only. Item changes land in a durable outbox table that wakes the delivery dispatcher. Ingest, processing and delivery are batch-oriented jobs on the queue; bytes are copied server-side whenever both ends are object storage. User processes run in isolated containers with run-scoped credentials and never see platform secrets.

- Target architecture, data model and flows: [`ROADMAP.md`](ROADMAP.md) §3–§6
- The rules and the reasoning behind them: [`docs/decisions/`](docs/decisions/README.md) (ADRs)
- Local stack, environment and the full API route table: [`docs/backend.md`](docs/backend.md)
- Conventions for code in this repo: [`.agents/skills/project-conventions/SKILL.md`](.agents/skills/project-conventions/SKILL.md) and [`.agents/skills/backend-invariants/SKILL.md`](.agents/skills/backend-invariants/SKILL.md)

## Quick start

```bash
grep -v '^CREDENTIALS_MASTER_KEY=' .env.example > .env
echo "CREDENTIALS_MASTER_KEY=$(openssl rand -base64 32)" >> .env   # your own key, never a shared one
npm install                 # repo root only — one lockfile, workspace symlinks
docker compose up -d        # pgstac, STAC API, auth proxy, Keycloak, MinIO, pipeline, tilers
cd app && set -a && source ../.env && set +a && npm run dev   # http://localhost:4321
```

The dev server reads its settings from the shell, so start it as above. That
gives it the master key, which encrypts stored connection credentials, and
`SAFE_FETCH_ALLOW_HOSTS`, without which creating a collection 403s. Compose
reads the same `.env` for the pipeline. How to generate the key, check it, and
what breaks when it is wrong:
[`docs/connections.md`](docs/connections.md#the-master-key-credentials_master_key).

`npm run verify` at the repo root runs the CI gates (typecheck, build, unit
tests). Pipeline tests: `uv run pytest` and `uv run ruff check .` from
`services/pipeline/`. E2E: `npm run test:e2e:ci` from `app/` with the stack up.

## Contributing

Work is tracked in GitHub Issues, grouped into queues with a milestone and an
epic each. Start at the pinned **Start here** issue, pick something labelled
`ready`, branch off `main` in a worktree, and open a PR. The mechanics, labels
and the one link rule between docs and issues: [`CONTRIBUTING.md`](CONTRIBUTING.md).

AI coding agents (Claude Code, Codex, opencode, Cursor) read
[`AGENTS.md`](AGENTS.md) and the skills under [`.agents/skills/`](.agents/skills/);
how those pieces fit is in [`docs/AI-STRATEGY.md`](docs/AI-STRATEGY.md).

## Documentation

[`docs/README.md`](docs/README.md) is the index: what's built
([`FEATURES.md`](docs/FEATURES.md)), decisions ([`decisions/`](docs/decisions/README.md)),
known limitations ([`ISSUES.md`](docs/ISSUES.md)), and the dated design specs
and implementation plans ([`docs/superpowers/`](docs/superpowers/)).

## License

See [`LICENSE`](LICENSE).
