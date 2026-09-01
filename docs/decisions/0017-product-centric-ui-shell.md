# ADR 0017 — Product-centric UI shell, NOAA theme, and terminology adoption

- **Status:** accepted (2026-08-31 — approved by the lead after the UI-remodel
  brainstorm interview; design brief:
  `docs/superpowers/specs/2026-08-31-ui-remodel-design.md`)
- **Owners:** the UI-remodel track (`app/UI-TODO.md`, slices UI-1…UI-8).
- **Related:** ROADMAP §8 (UI surface) & §9 Phase 9 terminology note;
  ISSUES I-65 (inline editor dependency); ADR 0016 (unaffected — this ADR is
  presentation-layer only).

## Context

The app grew as a generic STAC client (catalogs, search, extension builder)
and accreted platform surfaces (connections, data flow, monitoring, processes)
around a top-header nav that treats all nine pages as peers. The platform's
actual users are **data managers** whose center of gravity is the product
(built-in-catalog collection): its description, items/assets, and how it
connects to sources, processes, other products, and destinations. A NOAA
design exploration (`app/NOAA-geoplatform-design.zip`) established a visual
direction: navy sidebar, white content, health-first overview pages,
NOAA blue/white palette.

## Decision

1. **Shell**: replace the top-header nav with a persistent dark-navy sidebar
   (primary nav: Products, Processes, Connections, Pipeline graph,
   Monitoring; a collapsed "More" group holds Catalogs, Search, Extensions;
   stack-status footer) plus a slim top bar (page context, global client-side
   search, AlertBell, ThemeToggle, UserMenu). The CatalogSelector renders
   only on catalog-context pages, not in the shell.
2. **Home is the product list**: `/` becomes a product-centric overview
   (stat tiles, connection-health strip, per-product health rollup) built on
   existing read endpoints only.
3. **Terminology — full adoption in copy, nowhere else**: UI copy says
   "product" (built-in-catalog collection), "source" (ingest association),
   "destination" (deliver association), "pipeline graph" (flow topology).
   Routes, `/api/*` contracts, schema, and code vocabulary keep the
   canonical repo terms (ROADMAP §9 note already reserved this as a
   copy-only decision).
4. **Theme**: NOAA light becomes the default (navy `#0D2A47`, federal blue
   `#005EA2`, white/`#F5F8FB` content); dark mode is retained as a
   navy-tinted variant. Public Sans (UI) + a mono face (technical values),
   self-hosted via `@fontsource`.
5. **Lineage strip** as the shared visual language for flow topology
   (mini/medium/full on home / product Overview / graph): one presentational
   component, not three widgets.
6. **CodeMirror 6** is adopted for the process code editor only (settles the
   I-65 dependency choice; Monaco rejected — heavier, worse fit for an
   islands app).

## Invariants

- Presentation-layer only: no `/api/*` contract changes, no migrations, no
  route renames. De-emphasized surfaces are relocated, never removed.
- The Settings visibility knob stays parenthesized (I-1).
- New dependencies are limited to CodeMirror 6 packages and `@fontsource`
  font packages; shadcn primitives only via `npx shadcn@latest add`.

## Alternatives considered

- **Keep top header, restyle** — cheaper, but keeps nine-peer navigation
  that buries the product-centric workflow; rejected.
- **Light-only theme** — most mockup-faithful, but drops a working feature
  (dark mode + basemap wiring) without need; rejected.
- **Route renames to `/products/*`** — nicer URLs, but churns bookmarks,
  e2e, and docs for zero functional gain now; deferred, not rejected.

## Consequences

- All 20 page-island components swap `Header` for the new `AppShell`
  (Header is rendered inside each island, not as its own island); 4 e2e
  specs' nav selectors and 2 unit-test Header mocks update with it.
- Default-theme flip touches the `$theme` store default and moves the
  pre-hydration theme script into `<head>` (fixing an existing
  flash-of-wrong-theme risk).
- Copy adopts "product" while code says "collection" — a deliberate,
  documented seam; contributors should read this ADR before "fixing" it.
