# AGENTS.md

Canonical instructions for all AI coding agents working in this repo, per the
[AGENTS.md](https://agents.md/) open standard. Harness-specific additions live in
that harness's own file (e.g. `CLAUDE.md` for Claude Code) — everything here
applies to every agent.

## Monorepo Layout

npm-workspaces monorepo:

- `app/` — the Astro 6 (SSR) + React 19 STAC client
- `packages/shared/` — `@stac-higher/shared`: reusable components, hooks, types, stores, RJSF theme, and Storybook

Facts:
- Run `npm install` at the **repo root** — not `app/` — to wire workspace symlinks. Single lockfile at the root.
- **Shared components are the source of truth.** UI primitives (Button, Card, Badge, Input, Label, Select, Skeleton, Switch, Textarea, Tooltip), `shared/*` utilities (BboxInput, EmptyState, ErrorBoundary, ErrorState, JsonViewer, LoadingState), layout (ThemeToggle), collection/item cards, all `map/` components, and the RJSF theme live in `packages/shared/` and are imported from `@stac-higher/shared`. Most `app/src/lib/` and `app/src/stores/` files are thin re-export proxies.
- App-only shadcn primitives (dialog, dropdown-menu, popover, separator, sheet, sonner, table, tabs) remain in `app/src/components/ui/`.
- Path aliases: `@/*` → `app/src/*` (app-local); `@shared/*` → `packages/shared/src/*` (used *inside* the shared package only — app code imports from `@stac-higher/shared`).

## Commands

- **Install**: `npm install` (repo root)
- **Verify**: `npm run verify` (repo root — app build + unit tests; **must pass before declaring any task done**)
- **Dev**: `npm run dev` (from `app/`, http://localhost:4321)
- **Build**: `npm run build` (from `app/`, outputs to `app/dist/`)
- **Unit tests**: `npm test` (from `app/`); `npm run test:watch` for watch mode
- **E2E**: `npm run test:e2e:ci` (from `app/` — list reporter, agent-friendly). Read the `run-e2e` skill first; the suite has real preconditions and gotchas.
- **Proxy integration tests**: `npm run test:integration` (repo root — needs the Docker stack in auth-enforced mode, so lead/human only; skips cleanly otherwise. See `tests/integration/README.md`.)
- **Storybook**: `npm run storybook` (from `packages/shared/`, http://localhost:6006)
- **Backend**: `docker compose up -d` (repo root — full local stack: pgstac (:5433), stac-fastapi (:8082), stac-auth-proxy (:8081, pass-through), Keycloak (:8180, admin/admin), MinIO (:9000 API / :9001 console), pipeline service (:8083 `/health`))

## Architecture

**Astro + React islands**: Astro pages (`app/src/pages/*.astro`) are thin routing
shells; each mounts a single React island via `client:only="react"`. The only
cross-island split is Header vs. page content.

**Three-tier state**:
1. **Nanostores** — cross-island persistent state (catalog selection, theme), persisted to localStorage. Catalog state: `app/src/stores/catalogStore.ts` (`$catalogs`, `$activeCatalogId`, `$activeCatalog`).
2. **TanStack Query** — server state. Query keys include the catalog URL, so switching catalogs invalidates all cached data. Key factory: `app/src/lib/query/keys.ts`.
3. **React Hook Form + Zod** — form state. Schemas in `app/src/lib/stac-api/schemas.ts`.

**Data flow**: `useStore($activeCatalog)` → TanStack Query hook → API function
(`app/src/lib/stac-api/*.ts`) → `stacFetch()` → STAC API. Mutations invalidate
query keys; forms redirect via `window.location.href` on success.

**Map**: MapLibre GL JS via `react-map-gl/maplibre`; CartoDB basemaps
(dark-matter / positron). Components: `StacMap`, `FootprintLayer`, `ExtentLayer`,
`ItemGeometryEditor`. Utilities in `packages/shared/src/lib/map/`.

For the full conventions (island pattern rationale, form pattern, import rules),
read the `project-conventions` skill before any non-trivial change.

## Backend & API Routes

docker-compose runs the full local platform stack:

- **pgstac** (PostgreSQL + PostGIS, host :5433) and **stac-fastapi-pgstac** with
  the Transaction extension (full CRUD) at `http://localhost:8082`.
- **stac-auth-proxy** at `http://localhost:8081` in front of stac-fastapi —
  pass-through by default (`DEFAULT_PUBLIC=true`, no login needed). Opt-in
  enforcement (authenticated transactions + audience check, reads still
  public) via `docker compose -f docker-compose.yml -f
  infra/compose.auth-enforced.yml up -d --wait` — see
  `docs/decisions/0002-auth-proxy-enforcement.md`.
  The client's **built-in catalog** points here
  (`PUBLIC_BUILTIN_CATALOG_URL`, default `http://localhost:8081`) and is
  seeded as an undeletable entry in the `/catalogs` page.
- **Keycloak** at `http://localhost:8180` (admin/admin, realm `stac-higher`
  imported from `infra/keycloak/realm-stac-higher.json`). Realm import is
  skipped once the realm exists in the persisted volume — edits to the realm
  file only take effect after `docker compose down -v`.
- **MinIO** at :9000 (console :9001, minioadmin/minioadmin, bucket
  `stac-higher`).
- **pipeline service** (`services/pipeline`, Python) — queue worker +
  scheduler with `/health` on :8083. See
  `docs/decisions/0001-migration-ownership.md` for schema ownership.

Users configure additional catalogs in the `/catalogs` page (localStorage).
The same PostgreSQL instance (port 5433) backs the Astro app's extension
storage (`stac_higher.extensions`); `DATABASE_URL` overrides the default
connection string. Migrations run on the first API request via middleware.

Astro server routes:

| Route | Method(s) | Purpose |
|---|---|---|
| `/api/proxy` | ALL | CORS proxy (`X-Proxy-Target` + `X-Proxy-Endpoint` headers) |
| `/api/extensions` | GET, POST | List / create extensions |
| `/api/extensions/[id]` | GET, PUT, DELETE | Get / update / delete an extension |
| `/api/extensions/[id]/schema` | GET | Serve extension as JSON Schema |
| `/api/extensions/import` | POST | Fetch + store external JSON Schema |
| `/api/extensions/preview` | POST | Preview external schema metadata |
| `/api/extensions/resolve-schema` | POST | Fetch + cache a JSON Schema (5-min TTL) |
| `/api/auth/login` | GET | Start OIDC login (PKCE redirect to the IdP) |
| `/api/auth/callback` | GET | OIDC redirect URI — code exchange, sets the session cookie |
| `/api/auth/logout` | GET | Clear session + IdP end-session redirect |
| `/api/auth/me` | GET | Current canonical identity (`locals.auth`) |
| `/api/audit` | GET | Paginated audit log (operator: own groups; admin: all) |
| `/api/connections` | GET, POST | List (member+: own groups; admin: all) / create (operator+) connections — credentials write-only, never returned |
| `/api/connections/[id]` | GET, PUT, DELETE | Get / update / delete a connection (group-owned; PUT replaces credentials wholesale) |
| `/api/connections/[id]/test` | POST | Request a connectivity test (inserts a `connection_checks` row the pipeline drains — ADR 0004) |
| `/api/connections/[id]/checks/[checkId]` | GET | Poll a test request |
| `/api/connections/[id]/host-key/reset` | POST | Clear the TOFU host-key pin so the next test re-pins (ssh/sftp) |
| `/api/uploads` | POST | Mint presigned PUT URLs for asset uploads (operator+); returns the `/api/assets/...` hrefs to persist (ADR 0005) |
| `/api/assets/[collection]/[item]/[asset]` | GET | Authorize → 302 to a short-lived presigned URL for the canonical asset object (`{asset}` = filename) |
| `/api/collections/[id]/connections` | GET, POST | List / create ingest associations for a built-in-catalog collection (member+ scoped list; operator+ create, group-owned — Phase 4) |
| `/api/collections/[id]/connections/[assocId]` | GET, PUT, DELETE | Get / update (enabled, `config`, expectation) / delete an ingest association |

**Auth**: OIDC login with a claims-mapping layer and a dev-bypass mode
(static identity, default in dev — unit tests/e2e need no IdP). Middleware
exposes `locals.auth` to all server routes. Full env-var reference and flow
details: `docs/auth.md`.

**RBAC & audit**: the permission guard in `src/middleware.ts` requires the
`operator`/`admin` role for API mutations (extensions + connections CRUD;
reads stay open except the auth-required connections/audit surfaces) and
writes one append-only `stac_higher.audit_log` row per gated mutation
(allowed or denied) plus login/logout. The dev-bypass identity is an
operator, so existing flows keep working without login. Details:
`docs/auth.md` ("RBAC & audit").

**Connections (Phase 2)**: group-owned ingest/delivery endpoints in
`stac_higher.connections` with write-only AES-256-GCM-encrypted credentials
(`CREDENTIALS_MASTER_KEY` env — see `docs/connections.md` for the dev key
command) and TOFU host-key pinning. Test-connection bridges to the pipeline
through `stac_higher.connection_checks`
(`docs/decisions/0004-app-pipeline-bridge.md`).

**Object storage & asset service (Phase 3)**: item asset bytes live in
platform object storage (MinIO locally, `stac_higher` bucket / S3 in cloud) and
are reached only through the app. `app/src/lib/storage/` signs URLs **offline**
(the app never streams bytes): `GET /api/assets/{collection}/{item}/{filename}`
authorizes then 302s to a presigned URL (via `resolveAssetTarget`, the
`reference`-mode seam); `POST /api/uploads` (operator+) mints presigned PUTs into
canonical storage. Key layout is §5.3. App env: `S3_ENDPOINT` (must be
**browser-reachable** — presigning is offline), `S3_BUCKET`, `S3_ACCESS_KEY_ID`,
`S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_FORCE_PATH_STYLE` (MinIO defaults work
locally). The pipeline sweeps abandoned `staging/` uploads via a TTL cleanup job
(`STAGING_*` env). Details: [ADR 0005](docs/decisions/0005-asset-service.md).

**Ingest associations (Phase 4, Slice A)**: `stac_higher.collection_connections`
(migration 005) wires a connection to a built-in-catalog collection as an ingest
source with a §5.1 `config`; `stac_higher.ingest_files` is the per-file ledger
the pipeline maintains. The app owns both tables' DDL and writes associations
(`app/src/lib/associations/*`, `/api/collections/[id]/connections*`); the
pipeline (Slice B) reads associations and writes the ledger, never DDL (ADR
0001). The ingest `config` Zod schema (`associations/schemas.ts`) is the
cross-runtime contract with the pipeline. Group ownership is enforced in-route
(operator+ to mutate; `reference` storage mode is s3-only). UI: the **Data flow**
tab on built-in-catalog collection pages.

Outbound server fetches go through `safeFetch` (blocks private/loopback targets;
for dev against local pgstac set `SAFE_FETCH_ALLOW_HOSTS=localhost,127.0.0.1` in
`.env.local`; silence logs with `SAFE_FETCH_LOG=0`). `/api/proxy` rejects
`Sec-Fetch-Site: cross-site`; optional `PROXY_AUTH_TOKEN` enforces an
`X-Proxy-Auth` header.

**Extension data model**: rows in `stac_higher.extensions` — `id` (UUID), `name`,
`prefix`, `version`, `description`, `schema` (JSONB), `source` (`local`/`external`),
`source_url`. Attaching an extension adds its schema URL to `stac_extensions`;
`ExtensionFields` renders RJSF fields; on save, properties merge into
`item.properties` (items) or `collection.summaries` (collections).

## Workflow — Worktree Isolation (Mandatory)

AI work lives on `ai/main` and worktree branches off it. **Never commit directly
to `main`** — that branch is human-reviewed integration.

### Solo tasks
1. **Start**: create a worktree off `ai/main` with a descriptive branch name:
   `git worktree add .claude/worktrees/<slug> -b ai/<slug> ai/main`
2. **Work**: make changes in the worktree, commit to the worktree branch. Run
   `npm run verify` (after `npm install` in the worktree) before declaring done.
3. **Merge**: when complete and verify passes:
   `git checkout ai/main && git merge ai/<slug> --no-ff`
4. **Cleanup**: `git worktree remove .claude/worktrees/<slug>` and delete the
   branch. Push `ai/main` to origin if running unattended.

### Team tasks
Multi-agent orchestration is harness-specific — see your harness's file (for
Claude Code: `CLAUDE.md` "Team tasks" and `.claude/prompts/ai-loop.md`). The
harness-neutral invariants:
- Each teammate works in its own worktree off `ai/main`.
- Teammates run `npm run verify` **only** — never e2e, never the dev server,
  never Docker (see the contention rule below).
- The lead merges all branches into `ai/main` after teammates finish, then runs
  verify and (if UI flows changed) e2e serially on `ai/main`.

### Promoting `ai/main` → `main`
The AI does not merge into `main`. To promote, open a PR `ai/main → main` for
human review. After anything lands on `main`, sync it back with
`git checkout ai/main && git merge main --no-ff` — the only legitimate path from
`main` into `ai/main`.

### Rules
- Base branch is always `ai/main`. Create worktree branches from it.
- **Singleton resources**: the dev server (:4321), the pgstac backend (:8082),
  and the e2e suite (serial by design — shared DB, `workers: 1`) are shared. Only
  ONE process may run the dev server or e2e at a time. In team work, that is the
  lead, after merging.
- **Merge conflicts**: attempt to resolve — read both sides, understand intent,
  produce a correct merge. STOP and report only if the sides genuinely
  contradict each other.
- **`package-lock.json` conflicts**: `git checkout --theirs package-lock.json &&
  npm install && git add package-lock.json`. Regenerating from the merged
  `package.json` files is the correct fix — never hand-edit the lockfile.
- If verify fails after a merge, fix on `ai/main` and commit the fix there.

## Solo Agent Loop (TODO.md)

When iterating autonomously:
1. Pick the **first unchecked item** (`- [ ]`) in `TODO.md`. No cherry-picking.
2. Read the files the task references before changing anything. Reuse existing
   components (`StacMap`, `FootprintLayer`, `ExtentLayer`, `BboxInput`, …).
3. Implement in a worktree per the workflow above. One task per iteration; keep
   changes minimal and focused.
4. `npm run verify` must pass. Run e2e (`run-e2e` skill) if the task touched
   flows the suite covers.
5. Merge to `ai/main`, mark the task `- [x]` in `TODO.md`, and append any
   discovered follow-ups to the appropriate section.

Additional rules: no new dependencies without clear need; never edit shadcn
primitive files by hand (use `npx shadcn@latest add <component>`; shared package
if the app consumes it from `@stac-higher/shared`); don't break existing pages
when changing shared components.

## Gotchas

- Full-project `npx astro check` currently OOMs (pre-existing Vite/rolldown
  plugin type conflict between root and `app/node_modules`). Rely on
  `npm run verify` (build + vitest) instead.
- The Zod v4 → `zodResolver` type inference mismatch forces an `as any` cast on
  form resolvers — this is a known pattern, not a bug to fix.
- `extensions.spec.ts` and `proxy.spec.ts` (e2e) require the Docker backend on
  :8082. Full e2e preconditions and selector gotchas: `run-e2e` skill.
- Theme is dark by default; `Layout.astro` applies the theme class pre-hydration
  to prevent flash. Toggle via `toggleTheme()` from `@stac-higher/shared`.

## Agent Skills

Task playbooks live under `.agents/skills/`, following the
[Agent Skills](https://agentskills.io/) open standard (one folder per skill,
`SKILL.md` with `name` + `description` frontmatter). Codex/OpenCode/Cursor read
`.agents/skills/` natively; Claude Code reads it through the `.claude/skills`
symlink. Read the relevant `SKILL.md` *before* starting a matching task.

- **`project-conventions`** — read first for any non-trivial change: island pattern, three-tier state, import rules, form pattern, shadcn rules.
- **`new-component`** — adding a React component (app vs. shared placement, conventions).
- **`new-page`** — adding an Astro page + React island.
- **`new-test`** — writing unit / component / e2e tests following project patterns.
- **`add-stac-endpoint`** — adding a STAC API client function + TanStack Query hook.
- **`run-e2e`** — running the Playwright suite: preconditions, filters, and its selector/CSRF/IPv6 gotchas.

See `docs/AI-STRATEGY.md` for how this file, the skills, and per-harness shims
fit together.

## Documentation

Project docs live in `docs/` (index: `docs/README.md`), organized into three
tracks — read the relevant one before non-trivial work and update it after:

- **`docs/FEATURES.md`** — catalog of what's built, per phase, with entry points.
- **`docs/decisions/`** — ADRs (index + invariants in `docs/decisions/README.md`).
  Add the next-numbered ADR for any significant, hard-to-reverse choice.
- **`docs/ISSUES.md`** — carried-forward work, known limitations/residual risk,
  and deferrals. Log new gaps here rather than leaving them implicit.
