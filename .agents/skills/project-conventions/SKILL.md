---
name: project-conventions
description: The non-negotiable architectural rules for the stac-higher monorepo, with the why behind each. Use this skill at the start of any non-trivial code change — before adding components, pages, hooks, stores, form fields, or anything that crosses the app/shared-package boundary. Also use it when the user asks "where should this code live", "how should I structure X", or "is this the right pattern".
---

# Project Conventions

## Astro + React Island Pattern

Astro pages (`app/src/pages/*.astro`) are thin routing shells. Each mounts a
single React island via `client:only="react"` that handles all interactivity.
The only cross-island split is Header vs. page content — everything else within
a page is one React tree.

```
Layout.astro (HTML shell, theme script)
  └── <PageComponent client:only="react" />
        ├── QueryProvider (TanStack Query + Toaster)
        ├── Header (nav, CatalogSelector, ThemeToggle)
        └── Page content (forms, maps, tables)
```

**Why**: separate islands cannot share React context. Anything that needs the
QueryClient, form context, or map context must live in the same tree. Nanostores
exist precisely to bridge the Header island and the page island.

## Three-Tier State — pick the right tier

1. **Nanostores** — cross-island persistent state only (catalog selection,
   theme). Module-level atoms shared across React trees; persisted via
   `@nanostores/persistent`. Do not put per-page state here.
2. **TanStack Query** — all server state. Query keys include the catalog URL
   (`["stac", catalogUrl, "collections", ...]`) so switching catalogs
   invalidates everything. Use the key factory (`app/src/lib/query/keys.ts`) —
   never inline key arrays; mutations invalidate by key prefix.
3. **React Hook Form + Zod** — form state. Schemas in
   `app/src/lib/stac-api/schemas.ts`. `useFieldArray` for repeatable sections
   (providers, assets, properties). The resolver uses an `as any` cast (Zod v4
   type inference vs `zodResolver`) — known pattern, don't "fix" it.

## Import Rules

- App code imports app-local modules via `@/*` and everything shared from
  `@stac-higher/shared` (the barrel at `packages/shared/src/index.ts`).
- `@shared/*` is used **inside** `packages/shared` only.
- Many `app/src/lib/` and `app/src/stores/` files are thin re-export proxies —
  when editing a proxied module, the real source is in `packages/shared/`.
- Shared components are the source of truth; if a component exists in
  `packages/shared/`, do not fork a copy into `app/`.

## UI Rules

- shadcn/ui (Radix + Tailwind) for all UI primitives. Shared primitives in
  `packages/shared/src/components/ui/`; app-only primitives (dialog,
  dropdown-menu, popover, separator, sheet, sonner, table, tabs) in
  `app/src/components/ui/`.
- **Never hand-edit either `components/ui/` directory.** Add primitives with
  `npx shadcn@latest add <component>`; place in the shared package if the app
  will consume it from `@stac-higher/shared`.
- `lucide-react` for icons. `useStore()` from `@nanostores/react` for global
  state. Wrap page-level components with `QueryProvider` from
  `@/components/layout/QueryProvider`.

## Form Pattern (all CRUD forms)

Zod schema → `useForm()` with `zodResolver` → conversion functions between form
shape and STAC JSON (`formToStacCollection` / `stacCollectionToForm`) → sticky
JSON preview sidebar via `watch()` → mutation on submit with toast feedback →
redirect via `window.location.href` on success (full page reload, not SPA
navigation — islands make client-side routing pointless here).

## Map Components

Reuse before building: `StacMap` (base wrapper, nav/scale controls),
`FootprintLayer` / `ExtentLayer` (GeoJSON source+layer), `ItemGeometryEditor`
(drawing tools integrated with RHF via `Controller`; supports map drawing and
raw GeoJSON text). Layer styles in `packages/shared/src/lib/map/styles.ts`,
bbox utilities in `packages/shared/src/lib/map/bbox.ts`. Wire basemap style to
the `$theme` store (dark-matter / positron).
