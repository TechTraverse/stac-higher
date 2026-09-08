# AGENTS.md

Canonical instructions for all AI coding agents in this repo, per the
[AGENTS.md](https://agents.md/) standard. Harness-specific additions live in
that harness's own file (`CLAUDE.md`, `opencode.json`). This file is loaded
into every session: facts and rules only. Reference material lives in `docs/`
and the skills — read it when the task touches that area, not up front.

## Layout

npm-workspaces monorepo:

- `app/` — the Astro 7 (SSR) + React 19 STAC client
- `packages/shared/` — `@stac-higher/shared`: shared components, hooks, types, stores, RJSF theme, Storybook
- `services/pipeline/` — the Python queue worker + scheduler; `services/process-runtime/` — the process-run image
- `infra/` + `docker-compose.yml` — the local stack; `tests/contract-fixtures/` — cross-runtime golden fixtures

Facts:
- `npm install` at the **repo root** only — single lockfile, workspace symlinks.
- **Shared components are the source of truth.** UI primitives, `shared/*` utilities, cards, all `map/` components and the RJSF theme live in `packages/shared/` and are imported from `@stac-higher/shared`; most `app/src/lib/` and `app/src/stores/` files are thin re-export proxies. App-only shadcn primitives (dialog, dropdown-menu, popover, separator, sheet, sonner, table, tabs) stay in `app/src/components/ui/`.
- Path aliases: `@/*` → `app/src/*`; `@shared/*` → `packages/shared/src/*` (inside the shared package only).

## Commands

- **Install**: `npm install` (repo root)
- **Verify**: `npm run verify` (repo root — app-scoped typecheck + build + unit tests, the CI gates; **must pass before declaring any task done**)
- **Dev**: `npm run dev` (from `app/`, http://localhost:4321)
- **Unit tests**: `npm test` / `npm run test:watch` (from `app/`)
- **Pipeline tests**: `uv run pytest` and `uv run ruff check .` (from `services/pipeline/` — both, whenever the pipeline is touched; `DATABASE_URL=…` enables the DB-gated tests. Reference: `services/pipeline/README.md`)
- **E2E**: `npm run test:e2e:ci` (from `app/`). Read the `run-e2e` skill first.
- **Proxy integration tests**: `npm run test:integration` (repo root — needs the auth-enforced Docker stack; lead/human only)
- **Demo pipeline**: `uv run python -m pipeline.demo seed | status | teardown` (from `services/pipeline`, stack up — rebuilds the scene → process → thumbnail loop after a `down -v`)
- **Storybook**: `npm run storybook` (from `packages/shared/`)
- **Backend**: `docker compose up -d` (repo root). Services, ports, credentials and the auth-enforced overlay: `docs/backend.md`.

## Architecture

**Astro + React islands**: Astro pages (`app/src/pages/*.astro`) are thin
routing shells; each mounts a single React island via `client:only="react"`.
The only cross-island split is Header vs. page content.

**Three-tier state**:
1. **Nanostores** — cross-island persistent state (catalog list, theme) in localStorage. `app/src/stores/catalogStore.ts` (`$catalogs`, `$builtInCatalog`). There is **no global "active catalog"** (UI-10): product surfaces read `$builtInCatalog`; the catalog browser takes its catalog from the route (`/catalogs/[catalogId]/collections*`, `?src=` URL when present); `/search` keeps a local selection.
2. **TanStack Query** — server state; query keys include the catalog URL. Key factory: `app/src/lib/query/keys.ts`.
3. **React Hook Form + Zod** — form state. Schemas: `app/src/lib/stac-api/schemas.ts`.

**Data flow**: `useStore($builtInCatalog)` → TanStack Query hook → API function
(`app/src/lib/stac-api/*.ts`) → `stacFetch()` → STAC API. Mutations invalidate
query keys; forms redirect via `window.location.href` on success.

**Map**: MapLibre GL JS via `react-map-gl/maplibre`. Components `StacMap`,
`FootprintLayer`, `ExtentLayer`, `ItemGeometryEditor`; utilities in
`packages/shared/src/lib/map/`.

Full conventions (island rationale, form pattern, import rules): the
`project-conventions` skill — read it before any non-trivial change.

## Backend invariants

Cross-cutting rules that hold on every task. The per-area design lives in
`docs/` (index: `docs/README.md`; route table + env: `docs/backend.md`).

- **Schema ownership**: the app owns every `stac_higher.*` DDL (migrations run on the first API request); the pipeline reads and writes rows, never DDL (ADR 0001).
- **Cross-runtime contracts**: any shape shared by app and pipeline (association `config`, channel config, manifests, status strings, alert kinds) has a golden fixture in `tests/contract-fixtures/` consumed by **both** vitest and pytest. A new or changed shape ⇒ a new/updated fixture (that directory's README has the format).
- **App → pipeline** requests are rows the pipeline drains (`connection_checks`, `process_checks`, `delivery_backfills`, …), never a direct call (ADR 0004).
- **Catalog writes** go only to the built-in catalog, through the BFF `/api/catalog/*` (ADR 0008); `stacFetch` refuses writes to any other catalog — external catalogs are read-only (I-89). Direct-to-proxy writes are gated by the ADR 0015 policy.
- **RBAC & audit**: API mutations need `operator`/`admin` (guard in `src/middleware.ts`) and write one append-only `audit_log` row each; the dev-bypass identity is an operator. Credentials and secrets are write-only in every API.
- **Egress**: server fetches go through `safeFetch` (blocks private/loopback; dev allow-list in `docs/backend.md`). Webhook dispatch is pipeline-side behind the connections egress policy — never widen `safeFetch` for it.
- **Process runs** see only their revision's env, run-scoped STS credentials and their code — never the DB URL, master key or platform keys (ADR 0013). Author contract: `docs/processes.md`.
- **Byte deletion** happens only through the `asset_gc` mark-then-collect queue (ADR 0011); nothing is deleted on an unconfigured platform.
- **Pipeline logging**: data goes in `extra={...}` structured fields, never interpolated into the message.

## Workflow — Worktree Isolation (Mandatory)

AI work lives on `ai/main` and worktree branches off it. **Never commit directly
to `main`** — that branch is human-reviewed integration.

### Solo tasks
1. **Start**: `git worktree add .claude/worktrees/<slug> -b ai/<slug> ai/main`
2. **Work**: commit to the worktree branch. Run `npm run verify` (after
   `npm install` in the worktree) before declaring done.
3. **Merge**: `git checkout ai/main && git merge ai/<slug> --no-ff`
4. **Cleanup**: `git worktree remove .claude/worktrees/<slug>` and delete the
   branch. **Never push `ai/main`** — it stays local; the human promotes it.

### Team tasks
Orchestration is harness-specific (Claude Code: `CLAUDE.md` "Team tasks" and
`.claude/prompts/ai-loop.md`; opencode: `/team-task`). Invariants:
- Each teammate works in its own worktree off `ai/main`.
- Teammates run `npm run verify` **only** — never e2e, the dev server, or Docker.
- The lead merges all branches into `ai/main` after teammates finish, then runs
  verify and (if UI flows changed) e2e serially on `ai/main`.

### Promoting `ai/main` → `main`
The AI never merges into `main`. Humans promote via PR `ai/main → main`. After
anything lands on `main`, sync back with
`git checkout ai/main && git merge main --no-ff` — the only path from `main`
into `ai/main`.

### Rules
- Base branch is always `ai/main`.
- **Singleton resources**: the dev server (:4321), the pgstac backend (:8082)
  and the e2e suite (serial, shared DB) are shared. Only ONE process may run
  them at a time — in team work, the lead, after merging.
- **Merge conflicts**: read both sides, understand intent, produce a correct
  merge. STOP and report only if the sides genuinely contradict.
- **`package-lock.json` conflicts**: `git checkout --theirs package-lock.json &&
  npm install && git add package-lock.json` — never hand-edit the lockfile.
- If verify fails after a merge, fix on `ai/main` and commit the fix there.

## Solo Agent Loop (TODO.md)

When iterating autonomously:
1. Pick the **first unchecked item** (`- [ ]`) **in the queue you were asked
   to work** — `TODO.md` holds several independent queues and its header routes
   between them. No cherry-picking within a queue, no wandering across queues.
   If nobody named a queue, ask rather than guess.
2. Read the files the task references before changing anything. Reuse existing
   components (`StacMap`, `FootprintLayer`, `ExtentLayer`, `BboxInput`, …).
3. Implement in a worktree per the workflow above. One task per iteration;
   minimal, focused changes.
4. `npm run verify` must pass. Run e2e (`run-e2e` skill) if the task touched
   flows the suite covers.
5. Merge to `ai/main`, mark the task `- [x]` in `TODO.md`, and append
   discovered follow-ups to the appropriate section.

Additional rules: no new dependencies without clear need; never edit shadcn
primitive files by hand (`npx shadcn@latest add <component>` — in the shared
package if the app consumes it from `@stac-higher/shared`); don't break
existing pages when changing shared components.

## Gotchas

- Full-project `npx astro check` from the repo root is meaningless (no
  `src/pages` there — I-8). The app-scoped check (`npm run check` from `app/`)
  is what `npm run verify` runs. Never run the check from the repo root.
- Astro 7 auto-daemonizes `astro dev` in AI-agent environments (manage with
  `astro dev stop`/`status`/`logs`). Playwright needs a foreground server —
  `playwright.config.ts` sets `ASTRO_DEV_BACKGROUND` in the webServer env;
  keep it, and set it yourself for a foreground dev server.
- The Zod v4 → `zodResolver` type mismatch forces an `as any` cast on form
  resolvers — a known pattern, not a bug to fix.
- `extensions.spec.ts` and `proxy.spec.ts` (e2e) need the Docker backend on
  :8082. `map.spec.ts` also needs the backend's built-in catalog to have
  products, and is the first spec to mount a real MapLibre canvas, so the
  suite now fetches its basemap style from `basemaps.cartocdn.com` over the
  network. Full e2e preconditions and selector gotchas: `run-e2e` skill.
- The repo-root `.dockerignore` excludes `infra/`, `services/`, `docs/` and
  `tests/`. A new repo-root-context derived image (pattern: `infra/proxy-policy`,
  `infra/titiler`) must re-include exactly the path it `COPY`s or its build
  fails with `"/<path>": not found`.
- Theme is **light** by default (ADR 0017). The inline script in `Layout.astro`
  and the `$theme` persistentAtom default in
  `packages/shared/src/stores/uiStore.ts` read the same `stac-theme` key and
  must stay in lockstep. Toggle via `toggleTheme()` from `@stac-higher/shared`.
- Keycloak realm import is skipped once the realm exists in the persisted
  volume — realm-file edits need `docker compose down -v`.

## Agent Skills

Task playbooks live in `.agents/skills/` ([Agent Skills](https://agentskills.io/)
standard; Claude Code reads them through the `.claude/skills` symlink). Read the
matching `SKILL.md` *before* starting: `project-conventions` (any non-trivial
change), `new-component`, `new-page`, `new-test`, `add-stac-endpoint`,
`run-e2e`. How the pieces fit: `docs/AI-STRATEGY.md`.

## Documentation

`docs/` (index: `docs/README.md`) has three tracks — read the relevant one
before non-trivial work and update it after:

- **`docs/FEATURES.md`** — what's built, per phase, with entry points.
- **`docs/decisions/`** — ADRs (index + invariants in `docs/decisions/README.md`).
  Add the next-numbered ADR for any significant, hard-to-reverse choice.
- **`docs/ISSUES.md`** — carried-forward work, known limitations, deferrals.
  Log new gaps here rather than leaving them implicit.
