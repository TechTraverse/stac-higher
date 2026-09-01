# UI Remodel — product-centric shell, NOAA theme (design brief)

**Date:** 2026-08-31 · **Status:** approved (brainstorm interview 2026-08-31)
**Decision record:** ADR 0017 · **Execution plan:** `app/UI-TODO.md`
**Reference mockups:** `app/NOAA-geoplatform-design.zip` (7 screens; guidance,
not gospel — the "CREATE" side-menu links and the gov banner are explicitly
NOT adopted)

## Purpose

Reorient the UI around what data managers do: manage **products** (built-in
catalog collections) and how each connects to sources, processes, other
products, and destinations. Emphasize collections/processes/connections/
pipeline; de-emphasize (never delete) the early STAC-client surfaces
(catalog aggregation, search, extension building). This is
**presentation-layer work only** — no `/api/*` contract changes, no schema
changes, no route renames.

## Settled decisions (do not relitigate)

1. **Shell**: persistent dark-navy left sidebar + slim white top bar
   (hybrid). Sidebar: brand block, primary nav (Products, Processes,
   Connections, Pipeline graph, Monitoring), collapsed **More** group
   (Catalogs, Search, Extensions), stack-status footer. No create links in
   the sidebar. Top bar: page context, global search (client-side filter),
   AlertBell, ThemeToggle, UserMenu. **CatalogSelector leaves the shell** —
   it renders only on catalog-context pages (Catalogs, Search).
2. **Terminology — full adoption, copy only** (ROADMAP §9 permits):
   "Product" = built-in-catalog collection; "Source" = ingest association;
   "Destination" = deliver association; "Pipeline graph" = /graph.
   External-catalog collections keep "collection". Routes, APIs, and schema
   vocabulary unchanged.
3. **Theme**: NOAA light is the new default (white content `#F5F8FB`-ground,
   navy sidebar); dark mode is retained as a navy-tinted variant. Toggle
   stays. Palette anchors: navy `#0D2A47`, federal blue `#005EA2` (primary/
   links/active), hairline borders `#D9E2EC`, health green `#157F3D` /
   amber `#C05621` / red `#B3261E` (status only, never decoration).
4. **Type**: Public Sans for UI/display; mono (IBM Plex Mono or system mono)
   for IDs, hrefs, crons, technical values. Self-hosted via `@fontsource`
   (build-time only; approved small dependency — fall back to system stack
   if it grows legs).
5. **Signature element**: the **lineage strip** — sources → processes →
   product → destinations as connected health-dotted cards, at three zoom
   levels: mini (home rows), medium (product Overview tab), full (pipeline
   graph). One visual language, one presentational component.
6. **Home** (`/`): mockup-style overview — four stat tiles (Products ·
   Connections · Processes · Ingest 24h), connection-health chip strip,
   product list with per-product health dot + reason. Health derives from
   existing endpoints only (`/api/monitoring/flows`, `/api/monitoring/graph`,
   `/api/alerts`); extend `PlatformRollup` prior art.
7. **Product detail**: new default **Overview** tab (health badge,
   description, lineage & distribution panel, endpoints, recent items).
   Existing tabs (Items, Assets, Data flow, Settings, Raw JSON) survive
   unchanged behind it.
8. **CodeMirror 6** (I-65): process code editor only. Other textareas stay.
9. **Sequencing**: shell-first slices (theme+shell land first across all
   pages, then per-page slices), each a worktree off `ai/main` → `npm run
   verify` → merge. Small merges keep conflict risk low against the
   parallel M3 session.
10. **Branding**: keep the current app name, restyled wordmark ("Meridian"
    was mockup-only). Cheap to change later; flagged for lead veto.

## In scope

All pages. Deep restructure: `/` (home), collection detail (Overview tab),
`/processes` + `/processes/[id]` (M3-W-2 merged — unblocked), `/connections`,
`/graph`, `/monitoring`. Visual-alignment only: `/catalogs`, `/search`,
`/extensions/*`, forms. Plus: default-theme flip, pre-hydration theme script
moved to `<head>` (fixes existing flash), `SearchPage` hardcoded header
height, `ItemGeometryEditor` hardcoded dark basemap, terminology copy sweep,
nav-selector updates in the 4 e2e specs + 2 unit-test Header mocks.

## Out of scope

- Any `/api/*` contract change, new endpoint, or migration.
- The Settings visibility knob (parenthesized pending I-1).
- Route renames (`/products/*` aliases) — future decision.
- CodeMirror beyond the process editor; Monaco.
- Gov banner strip; mockup "CREATE" sidebar links; "OGC API hosting"
  connection type (already settled as a serving toggle, ROADMAP §8).
- Dropping any current functionality — de-emphasized surfaces move under
  "More", nothing is removed.

## Slice list (details in `app/UI-TODO.md`)

- **UI-1** NOAA theme tokens + light default + fonts
- **UI-2** AppShell (sidebar + top bar) swapped across all 20 page islands
- **UI-3** Home overview (stat tiles, connection strip, product list w/ health)
- **UI-4** Product Overview tab + lineage strip (medium)
- **UI-5** Processes dashboard cards + editor polish + CodeMirror 6
- **UI-6** Connections restyle (direction-first, health chips)
- **UI-7** Pipeline graph columnar restyle (full lineage language)
- **UI-8** Monitoring + long-tail alignment + terminology sweep + polish pass
- **UI-9** Wrap-up: FEATURES/ROADMAP/ISSUES doc updates (close I-65), lead-scheduled e2e run

## Verification

Every slice: `npm run verify` before merge, plus a **Chrome visual check**
against the dev server (:4321, owned by the UI session) — load the touched
pages in both themes, screenshot, self-critique against the mockups. The M3
session owns Docker + e2e; slices touching e2e-covered flows (UI-2 nav,
UI-5 processes, UI-6 connections, UI-8 monitoring) update selectors in-slice
but **coordinate with the lead before running the suite**.
