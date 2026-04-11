# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from `app/`:

```bash
npm run dev        # Start Astro dev server (http://localhost:4321)
npm run build      # Production build to app/dist/
npm run preview    # Preview production build
npx astro check    # TypeScript type checking
```

Backend (from repo root):
```bash
docker compose up -d   # Start pgstac + stac-fastapi on port 8082
```

Testing:
```bash
npm test              # Vitest unit tests (single run)
npm run test:watch    # Vitest in watch mode
npm run test:e2e      # Playwright E2E tests (auto-starts dev server)
```

## Architecture

This is a **STAC (SpatioTemporal Asset Catalog) client UI** built with Astro 6 (SSR mode) + React 19 islands. The frontend connects to one or more STAC APIs (e.g., stac-fastapi-pgstac) for full CRUD on collections, items, and assets.

### Astro + React Island Pattern

Astro pages (`src/pages/*.astro`) are thin routing shells. Each mounts a single React island via `client:only="react"` that handles all interactivity. The only cross-island split is Header vs. page content — everything else within a page is one React tree.

```
Layout.astro (HTML shell, theme script)
  └── <PageComponent client:only="react" />
        ├── QueryProvider (TanStack Query + Toaster)
        ├── Header (nav, EndpointSelector, ThemeToggle)
        └── Page content (forms, maps, tables)
```

### Three-Tier State

1. **Nanostores** — cross-island persistent state (endpoint selection, theme). Module-level atoms shared across all React trees that import them. Persisted to localStorage via `@nanostores/persistent`.

2. **TanStack Query** — server state. Query keys include the endpoint URL (`["stac", endpointUrl, "collections", ...]`), so switching endpoints automatically invalidates all cached data.

3. **React Hook Form + Zod** — form state. Schemas in `src/lib/stac-api/schemas.ts`. Forms use `useFieldArray` for repeatable sections (providers, assets, properties). The resolver uses `as any` cast due to Zod v4 type inference issues with `zodResolver`.

### Data Flow

```
useStore($activeEndpoint)  →  endpoint URL
  → TanStack Query hook (useCollections, useItem, etc.)
    → API function (src/lib/stac-api/*.ts)
      → stacFetch() reads $activeEndpoint for base URL
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

- **Path alias**: `@/*` maps to `src/*` (configured in tsconfig.json)
- **UI components**: shadcn/ui (Radix primitives + Tailwind) in `src/components/ui/`
- **Theme**: Dark by default. `Layout.astro` has a `<script>` that applies the theme class before hydration to prevent flash. Toggle via `toggleTheme()` from `uiStore`.
- **STAC API types**: Full TypeScript interfaces in `src/lib/stac-api/types.ts`. `StacApiError` class for typed error handling.
- **Query key factory**: `src/lib/query/keys.ts` — hierarchical keys enable precise cache invalidation on mutations.

## Backend

docker-compose runs pgstac (PostgreSQL + PostGIS) and stac-fastapi-pgstac with the Transaction extension enabled (full CRUD). API at `http://localhost:8082`. Users configure endpoint URLs in the UI (persisted to localStorage).

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
- React components: `app/src/components/{collections,items,map,search,shared,layout,endpoints}/`
- API client + types: `app/src/lib/stac-api/`
- Query hooks: `app/src/lib/query/`
- Map utilities: `app/src/lib/map/`
- State stores: `app/src/stores/`
- UI primitives: `app/src/components/ui/` (shadcn — do not edit these by hand)

When creating new components, follow the existing patterns:
- Import from `@/` path alias (maps to `app/src/`)
- Use shadcn/ui primitives for all UI elements
- Use `lucide-react` for icons
- Use `useStore()` from `@nanostores/react` for global state
- Use TanStack Query hooks from `app/src/lib/query/` for server data
- Wrap page-level components with `QueryProvider` from `@/components/layout/QueryProvider`

### 4. Verify
Run these checks from the `app/` directory. **All must pass before marking a task done.**

```bash
cd /Users/caesterlein/Projects/ogc-maps/stac-higher/app

# Required: Type check — must show 0 errors
npx astro check

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
- **Do not commit to main. Dont switch branches. stay on the ralph/progress branch
- **Do not introduce new dependencies** without a clear need. The stack is already comprehensive.
- **Do not modify `app/src/components/ui/`** — these are generated by shadcn. Use `npx shadcn@latest add <component>` if you need a new primitive.
- **Do not break existing pages.** If you change a shared component, check that all pages importing it still work.
- **Keep changes minimal and focused.** A task that says "add X to Y" means add X to Y — not refactor Y while you're there.
