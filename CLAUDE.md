# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Monorepo Layout

This is an npm-workspaces monorepo:

- `app/` — the Astro + React STAC client
- `packages/shared/` — `@stac-higher/shared` package: reusable components, hooks, types, stores, RJSF theme, and Storybook

The app imports from `@stac-higher/shared` (the package's public barrel at `packages/shared/src/index.ts`). Most `app/src/lib/` and `app/src/stores/` files are thin re-export proxies that re-export from `@stac-higher/shared`, so existing app imports like `@/lib/utils` and `@/stores/mapStore` still work unchanged. **Shared components are the source of truth** — UI primitives (Button, Card, Badge, Input, Label, Select, Skeleton, Switch, Textarea, Tooltip), the `shared/*` utilities (BboxInput, EmptyState, ErrorBoundary, ErrorState, JsonViewer, LoadingState), layout (ThemeToggle), collection/item cards, all `map/` components (StacMap, DrawingToolbar, FootprintLayer, ExtentLayer), and the RJSF theme (`shadcnTheme` and its widgets/templates) all live in `packages/shared/` and are imported from `@stac-higher/shared`. App-only shadcn primitives (dialog, dropdown-menu, popover, separator, sheet, sonner, table, tabs) remain in `app/src/components/ui/`.

Run `npm install` at the **repo root** — not `app/` — to wire workspace symlinks. There is a single lockfile at the root.

### Path aliases
- `@/*` → `app/src/*` (app-local, set in `app/tsconfig.json` and `app/astro.config.mjs`)
- `@shared/*` → `packages/shared/src/*` (used *inside* `packages/shared` only; app code should import from `@stac-higher/shared` instead)

## Commands

App commands run from `app/`:

```bash
npm run dev        # Start Astro dev server (http://localhost:4321)
npm run build      # Production build to app/dist/
npx astro preview  # Preview production build
npx astro check    # TypeScript type checking
```

Backend (from repo root):
```bash
docker compose up -d   # Start pgstac + stac-fastapi on port 8082
```

Testing (from `app/`):
```bash
npm test              # Vitest unit tests (single run)
npm run test:watch    # Vitest in watch mode
npm run test:e2e      # Playwright E2E tests (auto-starts dev server)
```

Storybook (from `packages/shared/`):
```bash
npm run storybook        # Component library dev server (http://localhost:6006)
npm run build-storybook  # Build static Storybook to packages/shared/storybook-static/
```

Story files live in `packages/shared/src/**/*.stories.tsx`, co-located next to the components. Config is in `packages/shared/.storybook/`. Shared STAC fixtures are in `packages/shared/src/components/__fixtures__/stac.ts`. Map components (`StacMap`, `FootprintLayer`, `ExtentLayer`) are not yet storified — they require WebGL/MapLibre context.

## Architecture

This is a **STAC (SpatioTemporal Asset Catalog) client UI** built with Astro 6 (SSR mode) + React 19 islands. The frontend connects to one or more STAC APIs (e.g., stac-fastapi-pgstac) for full CRUD on collections, items, and assets.

### Astro + React Island Pattern

Astro pages (`src/pages/*.astro`) are thin routing shells. Each mounts a single React island via `client:only="react"` that handles all interactivity. The only cross-island split is Header vs. page content — everything else within a page is one React tree.

```
Layout.astro (HTML shell, theme script)
  └── <PageComponent client:only="react" />
        ├── QueryProvider (TanStack Query + Toaster)
        ├── Header (nav, CatalogSelector, ThemeToggle)
        └── Page content (forms, maps, tables)
```

### Three-Tier State

1. **Nanostores** — cross-island persistent state (catalog selection, theme). Module-level atoms shared across all React trees that import them. Persisted to localStorage via `@nanostores/persistent`. Catalog state lives in `app/src/stores/catalogStore.ts` (`$catalogs`, `$activeCatalogId`, `$activeCatalog` computed).

2. **TanStack Query** — server state. Query keys include the catalog URL (`["stac", catalogUrl, "collections", ...]`), so switching catalogs automatically invalidates all cached data.

3. **React Hook Form + Zod** — form state. Schemas in `src/lib/stac-api/schemas.ts`. Forms use `useFieldArray` for repeatable sections (providers, assets, properties). The resolver uses `as any` cast due to Zod v4 type inference issues with `zodResolver`.

### Data Flow

```
useStore($activeCatalog)  →  catalog URL
  → TanStack Query hook (useCollections, useItem, etc.)
    → API function (src/lib/stac-api/*.ts)
      → stacFetch() reads $activeCatalog for base URL
        → fetch() to STAC API
```

Mutations invalidate relevant query keys. Forms redirect via `window.location.href` on success (full page reload, not SPA navigation).

### Map Integration

MapLibre GL JS via `react-map-gl/maplibre`. Basemaps from CartoDB (dark-matter / positron). Key components:

- `StacMap` — base map wrapper with nav/scale controls
- `FootprintLayer` / `ExtentLayer` — GeoJSON source+layer rendering
- `ItemGeometryEditor` — drawing tools (polygon/bbox/point) integrated with React Hook Form via `Controller`. Supports both map drawing and raw GeoJSON text input.

Layer styles defined in `src/lib/map/styles.ts`. Bbox utilities in `src/lib/map/bbox.ts`.

### Form Pattern

All CRUD forms follow: Zod schema → `useForm()` with `zodResolver` → conversion functions between form shape and STAC JSON (`formToStacCollection`/`stacCollectionToForm`) → sticky JSON preview sidebar via `watch()` → mutation on submit with toast feedback.

## Key Conventions

- **Path alias**: `@/*` maps to `app/src/*` (configured in `app/tsconfig.json`). Import shared code from `@stac-higher/shared`.
- **UI components**: shadcn/ui (Radix primitives + Tailwind). Shared primitives live in `packages/shared/src/components/ui/` and are imported from `@stac-higher/shared`. App-only primitives (dialog, dropdown-menu, popover, separator, sheet, sonner, table, tabs) live in `app/src/components/ui/`.
- **Theme**: Dark by default. `Layout.astro` has a `<script>` that applies the theme class before hydration to prevent flash. Toggle via `toggleTheme()` from `@stac-higher/shared`.
- **STAC API types**: Full TypeScript interfaces in `packages/shared/src/lib/stac-api/types.ts` (app proxy at `app/src/lib/stac-api/types.ts` re-exports from `@stac-higher/shared`). `StacApiError` class for typed error handling.
- **Query key factory**: `src/lib/query/keys.ts` — hierarchical keys enable precise cache invalidation on mutations.

## Backend

docker-compose runs pgstac (PostgreSQL + PostGIS) and stac-fastapi-pgstac with the Transaction extension enabled (full CRUD). API at `http://localhost:8082`. Users configure catalogs (STAC API URLs) in the `/catalogs` page (persisted to localStorage).

The same PostgreSQL instance (port 5433) is also used by the Astro app itself for extension storage. Set `DATABASE_URL` env var to override the default connection string (`postgresql://username:password@localhost:5433/postgis`). Migrations run automatically on the first API request via Astro middleware.

Server-side outbound fetches (`/api/proxy`, extension schema fetches) block private/loopback targets by default via the `safeFetch` helper. For dev against the local pgstac API, set `SAFE_FETCH_ALLOW_HOSTS=localhost,127.0.0.1` in `.env.local`.

## API Routes (Astro server-side)

| Route | Method(s) | Purpose |
|---|---|---|
| `/api/proxy` | ALL | CORS proxy — forwards requests using `X-Proxy-Target` + `X-Proxy-Endpoint` headers |
| `/api/extensions` | GET, POST | List / create extensions |
| `/api/extensions/[id]` | GET, PUT, DELETE | Get / update / delete an extension |
| `/api/extensions/[id]/schema` | GET | Serve extension as JSON Schema (`application/schema+json`) |
| `/api/extensions/import` | POST `{ url }` | Fetch + store an external JSON Schema as an extension |
| `/api/extensions/preview` | POST `{ url }` | Preview external schema metadata without saving |
| `/api/extensions/resolve-schema` | POST `{ url }` | Fetch + cache a JSON Schema (5-min TTL in-memory cache) |

## Extension Data Model

Extensions are stored in `stac_higher.extensions` (PostgreSQL). Key fields: `id` (UUID), `name`, `prefix`, `version`, `description`, `schema` (JSONB), `source` (`local` or `external`), `source_url`.

When a user attaches an extension to a collection or item form:
- Its schema URL is added to `stac_extensions`
- `ExtensionFields` fetches the schema and renders RJSF fields
- On save: extension properties merge into `item.properties` (items) or `collection.summaries` (collections)

## Automated Guardrails

Hooks are configured in `.claude/settings.json` (committed, shared):

- **PostToolUse (Edit|Write)**: After any `.ts`, `.tsx`, or `.astro` file is edited, `npx astro check` runs automatically. If it reports errors, fix them before continuing.
- **PreToolUse (Edit|Write)**: Edits to `components/ui/` are blocked. Use `npx shadcn@latest add <component>` instead.

These hooks run without prompting. If a hook blocks your action, read the error message — it explains what to do instead.

## Project Commands

Slash commands are defined in `.claude/commands/` and can be invoked during development:

| Command | Purpose |
|---|---|
| `/verify` | Run full verification suite (type check + build + unit tests) |
| `/new-component <Name>` | Scaffold a new React component following project conventions |
| `/new-page <route>` | Create a new Astro page + React island following the existing pattern |
| `/new-test <target>` | Write tests for a file or feature following project test patterns |
| `/add-stac-endpoint <desc>` | Add a new STAC API function + query hook following the client layer pattern |

## Agent Loop Protocol

When operating in an autonomous loop, follow this protocol for each iteration:

### 1. Pick a task
Read `TODO.md` at the repo root. Select the **first unchecked item** (`- [ ]`). Do not skip ahead or cherry-pick — tasks are ordered by priority and dependency.

### 2. Understand before changing
Before writing code, read the files the task references. Understand the existing patterns in nearby components. If the task says to modify `CollectionDetail.tsx`, read that file and its imports first. Reuse existing components (`StacMap`, `FootprintLayer`, `ExtentLayer`, `BboxInput`, etc.) rather than building new ones.

### 3. Implement
All source code lives under `app/src/`. Key locations:
- Pages (Astro routing shells): `app/src/pages/`
- React components: `app/src/components/{collections,items,catalogs,extensions,search,layout}/` (app-specific). Shared components live in `packages/shared/src/components/{shared,layout,map,collections,items}/` and are imported from `@stac-higher/shared`.
- Extension UI: `app/src/components/extensions/` — list, detail, form, picker, dynamic fields (`ExtensionFields`), import dialog. RJSF theme lives in `packages/shared/src/components/extensions/rjsf-theme/` and is imported from `@stac-higher/shared`.
- Extension pages: `app/src/pages/extensions/` — index, new, `[id]/index`, `[id]/edit`
- Catalog pages: `app/src/pages/catalogs.astro`
- API client + types: `app/src/lib/stac-api/` (types are proxies to `@stac-higher/shared`)
- Query hooks: `app/src/lib/query/`
- Map utilities: `packages/shared/src/lib/map/` (proxies at `app/src/lib/map/`)
- State stores: `app/src/stores/catalogStore.ts` (app-only). Shared stores (uiStore, mapStore) live in `packages/shared/src/stores/` with app proxies.
- UI primitives: shared ones in `packages/shared/src/components/ui/`; app-only ones in `app/src/components/ui/` (do not edit either by hand)
- Database layer: `app/src/lib/db/` — `connection.ts` (singleton Pool from `DATABASE_URL`), `migrate.ts` (creates `stac_higher.extensions` table)
- Extension logic: `app/src/lib/extensions/` — `types.ts`, `schemas.ts`, `storage.ts` (CRUD via `pg`), `api.ts`, `queries.ts` (TanStack), `schema-cache.ts` (in-memory TTL cache)

When creating new components, follow the existing patterns:
- Import app-local modules via `@/*`; import shared utilities, components, stores, and UI primitives from `@stac-higher/shared`
- Use shadcn/ui primitives for all UI elements
- Use `lucide-react` for icons
- Use `useStore()` from `@nanostores/react` for global state
- Use TanStack Query hooks from `app/src/lib/query/` for server data
- Wrap page-level components with `QueryProvider` from `@/components/layout/QueryProvider`

### 4. Verify
Run these checks from the `app/` directory. **All must pass before marking a task done.**

```bash
cd /Users/caesterlein/Projects/ogc-maps/stac-higher/app

# Type check — currently OOMs (pre-existing Vite/rolldown plugin type conflict
# between root and app/node_modules). Skip until fixed. Rely on build + vitest.
# npx astro check

# Required: Build — must complete without errors
npx astro build

# If tests exist: Run them
npm test 2>/dev/null || true
```

If `astro check` or `astro build` reports errors, fix them and re-run. Do not mark the task complete with failing checks. Warnings and hints are acceptable; errors are not.

### 5. Mark done
Edit `TODO.md`: change `- [ ]` to `- [x]` for the completed task. If your work revealed new issues or follow-up needs, append them to the appropriate priority section in `TODO.md` with a clear description and file references.

### Rules
- **One task per iteration.** Do not combine unrelated tasks.
- **Do not commit to main. Do not switch branches. Stay on the `ralph/progress` branch.**
- **Do not introduce new dependencies** without a clear need. The stack is already comprehensive.
- **Do not modify shadcn primitive files by hand** — neither `packages/shared/src/components/ui/*` nor `app/src/components/ui/*`. Use `npx shadcn@latest add <component>` if you need a new primitive; place it in the shared package if the app will consume it from `@stac-higher/shared`.
- **Do not break existing pages.** If you change a shared component, check that all pages importing it still work.
- **Keep changes minimal and focused.** A task that says "add X to Y" means add X to Y — not refactor Y while you're there.
