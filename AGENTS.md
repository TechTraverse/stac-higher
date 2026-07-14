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
  pass-through mode in Phase 0 (`DEFAULT_PUBLIC=true`); Phase 1 tightens it.
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
