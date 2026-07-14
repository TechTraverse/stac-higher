---
name: new-component
description: Create a new React component for the STAC Higher UI. Use whenever the user asks to create, scaffold, or add a component, panel, card, dialog, toolbar, or widget — even if they don't say "component". Covers the app-vs-shared placement decision, project conventions (shadcn primitives, lucide icons, nanostores, TanStack Query), and verification.
---

# New Component

## 1. Decide where it lives

**Shared package** (`packages/shared/src/components/`) if it is reusable across
pages or could be consumed by another app / Storybook: `shared/` (generic
utilities), `layout/`, `map/`, `collections/`, `items/`. Export it from the
barrel (`packages/shared/src/index.ts`) and consider a co-located
`*.stories.tsx`.

**App** (`app/src/components/`) if it is page-specific:
- `collections/` — collection-related UI
- `items/` — item-related UI
- `catalogs/` — catalog management
- `extensions/` — extension list/detail/form/picker/dynamic fields
- `search/` — search panel and results
- `layout/` — header, providers, page wrappers

## 2. Match conventions

Read 2–3 existing components in the target directory first. Then:
- App code: import app-local modules via `@/*`, shared code from `@stac-higher/shared`. Inside the shared package: `@shared/*`.
- shadcn/ui primitives for all UI elements — never hand-edit `components/ui/`
- `lucide-react` for icons
- `useStore()` from `@nanostores/react` for global state
- TanStack Query hooks from `app/src/lib/query/` for server data
- Reuse existing map components (`StacMap`, `FootprintLayer`, `ExtentLayer`, `BboxInput`) rather than building new ones

## 3. Create and verify

Create the component file. Run `npm run verify` from the repo root (the Claude
Code post-edit hook also runs a scoped `astro check`). Fix all errors before
declaring done.
