# UI-TODO — product-centric remodel queue

The UI loop works this file top-down: pick the **first unchecked item**, one
slice per iteration, worktree off `ai/main` (`git worktree add
.claude/worktrees/<slug> -b ai/<slug> ai/main`), `npm run verify` before
merge (`--no-ff`), mark `- [x]`, append discovered follow-ups at the bottom.
**Never push `ai/main`.**

Scope source: **`docs/superpowers/specs/2026-08-31-ui-remodel-design.md`**
(settled decisions — do not relitigate) and **ADR 0017**. Read both, plus the
`project-conventions` skill, before any slice.

Standing constraints (from the brief; violations are review-stoppers):
- Presentation layer ONLY: no `/api/*` changes, no migrations, no route
  renames, no functionality removed (de-emphasized ⇒ relocated).
- Settings visibility knob stays parenthesized (I-1).
- shadcn primitives via `npx shadcn@latest add` only (hand-edits are
  hook-blocked). New deps: CodeMirror 6 + `@fontsource-*` only.
- Reusable presentational components → `packages/shared`; anything
  route/auth/app-API-coupled → `app/src/components/`.
- Terminology: UI copy says product / source / destination / pipeline graph;
  code identifiers, routes, APIs keep collection / association / graph.
  External-catalog collections (catalog browser, search) keep "collection".

**Verification protocol (every slice):**
1. `npm run verify` (repo root) in the worktree.
2. Visual check in Chrome against the dev server (the UI session owns
   :4321): load every touched page in **both themes**, screenshot, compare
   against `app/NOAA-geoplatform-design.zip` screens where one exists, fix
   what looks off before merging.
3. E2E: this session does NOT run the Playwright suite unilaterally — the
   M3 session owns Docker/e2e. Slices flagged **[e2e-touch]** update
   selectors in-slice, then ask the lead to schedule a run.

Parallel-session rule: the M3 loop works `TODO.md` (pipeline + scoping
docs). Don't touch `services/pipeline/`, `TODO.md`, or the M3 scoping notes.
Overlap risk is `docs/` and shared UI files — merge often, keep slices small.

## Queue

- [x] **UI-1 · NOAA theme tokens + light default + fonts.**
      Rebuild the palette in `app/src/styles/global.css` (`@theme` block,
      Tailwind v4 CSS-first) around the brief's anchors: navy `#0D2A47`,
      federal blue `#005EA2` primary, `#F5F8FB` background / white cards /
      `#D9E2EC` borders in light; navy-tinted dark (`#0B1F35` bg, `#12263A`
      cards, brightened accents). The `--color-sidebar-*` token set already
      exists — point it at the navy ramp in BOTH themes (sidebar stays navy
      in light mode). Keep the byte-identical copy in
      `packages/shared/src/styles/global.css` in sync (Storybook uses it).
      Health colors: green `#157F3D` / amber `#C05621` / red `#B3261E`
      (+ dark-mode variants) as tokens. Add `@fontsource` Public Sans +
      IBM Plex Mono (or `@fontsource-variable` equivalents), imported in
      `Layout.astro`; set font-family tokens (mono for IDs/hrefs/crons via a
      utility class, applied in later slices). Flip the default theme:
      `$theme` persistentAtom default `"dark"` → `"light"` in
      `packages/shared/src/stores/uiStore.ts`, `Layout.astro` `<html
      class="dark">` → light default, and MOVE the pre-hydration theme
      script from body-end into `<head>` (fixes the existing flash risk).
      Fix `app/src/components/items/ItemGeometryEditor.tsx` hardcoded
      dark-matter basemap → wire to `$theme` like `StacMap`. Existing pages
      may look transitional under old layout — acceptable for this slice.
- [x] **UI-2 · AppShell: sidebar + top bar.** [e2e-touch]
      `npx shadcn@latest add sidebar` (deps sheet/separator/tooltip already
      present; goes to `app/src/components/ui/`). Build in
      `app/src/components/layout/`: `AppShell.tsx` (QueryProvider + sidebar
      + top bar + `<main>`), `SidebarNav.tsx` (primary: Products `/`,
      Processes, Connections, Pipeline graph `/graph`, Monitoring; collapsed
      **More** group: Catalogs, Search, Extensions; brand block top —
      current app name, restyled wordmark; stack-status footer: STAC API
      reachability from the existing conformance/health query), `TopBar.tsx`
      (page-title slot, global search input filtering products/connections/
      processes client-side with link results, `AlertBell`, `ThemeToggle`,
      `UserMenu`). NO create links in the sidebar. `CatalogSelector` moves
      out of the shell — render it inside the Catalogs + Search page content
      only. Swap `Header` → `AppShell` in all 20 island components
      (grep `layout/Header`); change `Layout.astro` `#app` scaffold
      `flex-col` → works with sidebar row layout; fix `SearchPage`'s
      hardcoded `lg:h-[calc(100vh-3.5rem)]`. Collapse to icon rail /
      sheet on narrow viewports (`$sidebarOpen` in shared uiStore exists).
      Update: `app/src/__tests__/connections-page.test.tsx` +
      `monitoring-page.test.tsx` Header mocks; e2e nav selectors in
      `connections.spec.ts:14`, `processes.spec.ts:16,50`,
      `monitoring.spec.ts:15` (labels become Products/Processes/Pipeline
      graph/Monitoring; CatalogSelector-combobox disambiguation comments in
      `extension-forms.spec.ts`, `data-flow.spec.ts:114`, `assets.spec.ts:31`
      — verify those still scope correctly). Delete `Header.tsx` when
      nothing imports it. Nav copy adopts "Products".
- [x] **UI-3 · Home overview.**
      Replace `DashboardPage.tsx` content with the product-centric overview
      (keep the no-catalog empty state): four stat tiles (Products ·
      Connections · Processes · Ingest 24h — derive from `usePipelineGraph()`
      + `useFlows()` + existing collection/process queries; if a 24h ingest
      number isn't derivable from existing responses, show items-registered
      total or omit the tile — do NOT add an endpoint), connection-health
      chip strip (name + protocol + direction + status dot, linking to
      `/connections`), then the product list: name, mono id, item count,
      health dot + one-line reason (open alerts anchored via the collection's
      associations/processes from `useAlerts("open")` + flow expectation
      status from `useFlows()`), mini lineage glyph (n sources → n processes
      → n destinations counts). Extend/absorb `PlatformRollup.tsx` rather
      than duplicating its derivations. Old dashboard cards (API status,
      conformance badges) move into the stack-status footer detail or a
      compact strip — nothing deleted. Introduce the presentational
      `LineageStrip` component (packages/shared — pure props: ordered
      node groups + health, size variant mini|medium|full) with the mini
      variant; app-side adapter builds props from graph/flows data.
- [x] **UI-4 · Product Overview tab.**
      In `CollectionDetail.tsx`: add a default **Overview** tab (built-in
      catalog collections; external catalogs keep current default): title +
      health badge (30d success % from `useFlowHistory` where available,
      else open-alert status), type/visibility chips, description,
      **Lineage & distribution** panel (medium `LineageStrip`; cards link to
      the Data flow tab, process pages, `/connections`), endpoints section
      (STAC API href, serving links where `serving_enabled` advertises
      them — display only), recent items strip (existing items query,
      first page). Existing tabs unchanged behind it. Copy sweep on this
      page: product/source/destination.
- [x] **UI-5 · Processes dashboard + editor + CodeMirror.** [e2e-touch]
      `/processes` (`ProcessesPage.tsx`): mockup card layout per process —
      health dot + status line, in/out product links, trigger summary
      (mono cron), last run + duration, 30d success %, last-20-runs
      sparkline (existing runs query; no new endpoint; if per-process run
      fetch is too chatty for the list, cap to visible cards or drop the
      sparkline to the detail page — judgment call, note it).
      `/processes/[id]` (`ProcessDetailPage.tsx`, ~820 lines): reorganize
      into the editor layout (definition + sources/trigger + outputs left,
      code pane right, runs below), adopt **CodeMirror 6** for the code
      editor (python + json modes, theme-aware; pin exact versions; note
      the supply-chain review in the PR/commit per I-65). Keep EnvEditor,
      test-run, deploy, re-run flows exactly as they behave today.
      Update `processes.spec.ts` selectors if layout changes break them.
- [x] **UI-6 · Connections restyle.** [e2e-touch]
      `ConnectionsPage.tsx` + `ConnectionForm.tsx`: direction-first framing
      (Ingest source / Distribution destination segmented control per the
      mockup's create screen; type cards for s3/sftp/ftp/https), health
      chips on the list (status dot + protocol tag + direction), copy sweep
      (source/destination). No behavior change to test/host-key/credential
      flows. Update `connections.spec.ts` selectors as needed.
- [x] **UI-7 · Pipeline graph.**
      `PipelineGraph.tsx`: columnar left→right layout (source connections →
      source products → processes → derived products → destinations) using
      the full `LineageStrip` visual language, health-colored nodes/edges,
      legend, mono ids. Same `/api/monitoring/graph` data. Page copy:
      "Pipeline graph".
- [x] **UI-8 · Monitoring + long-tail + terminology sweep.** [e2e-touch]
      `MonitoringPage.tsx` (+Alerts/Flows/Channels cards): visual alignment
      (cards, health tokens, mono accents). Catalogs / Search / Extensions
      pages + all forms: visual alignment pass only (spacing, cards,
      buttons, CatalogSelector placement from UI-2). Repo-wide UI copy
      sweep for remaining collection/association strings on platform
      surfaces (grep user-facing strings; keep "collection" in the catalog
      browser/search context). Both-theme screenshot pass over every page;
      fix stragglers. Update `monitoring.spec.ts` if labels change.
- [x] **UI-9 · Wrap-up.** Update `docs/FEATURES.md` (UI surface
      descriptions), ROADMAP §8 rows' page copy where stale, ISSUES: close
      I-65 (CodeMirror landed), log any deferred follow-ups from below.
      Ask the lead to schedule the full e2e run (M3 session owns it);
      fix fallout on `ai/main`.

### Added after the remodel (lead feedback, 2026-09-01)

- [ ] **UI-10 · Retire the global "active catalog"; pin products to the
      built-in catalog.** [e2e-touch]

      **Why.** UI-3 renamed home to *Products* but left it reading
      `$activeCatalog`, so selecting an external catalog produces an
      incoherent page: the Products tile counts external collections while
      the Connections / Processes / Items-ingested tiles count the platform;
      every product row degrades to *"unknown / Not wired"* because external
      collection ids can never match platform `collection_id`s; and **"Create
      product" writes to the external catalog** (direct or `/api/proxy`, not
      the ADR 0008 BFF). The platform surfaces (`/connections`, `/processes`,
      `/graph`, `/monitoring`, `/extensions`) already ignore the selector
      entirely — this slice finishes that split rather than inventing it.

      **Decided (lead, 2026-09-01):** product surfaces are built-in-only;
      catalog selection survives ONLY where browsing an arbitrary catalog is
      the point (`/search`, `/catalogs`); the global persistent "active" flag
      goes away.

      **⚠️ OPEN DECISION — needs the lead before implementing.** Pinning
      `/collections*` to the built-in catalog removes the only way to browse
      an external catalog's collections and items, which collides with the
      standing "no functionality removed" constraint. Pick one:
      - **(a) Drop it.** External catalogs are reachable through `/search`
        and the `/catalogs` page only. Smallest surface, real removal.
      - **(b) Relocate it.** A `/catalogs/[catalogId]/collections` browser
        with no product framing (adds routes; the constraint forbids
        *renames*, not additions).
      - **(c) Parameterize it.** `/collections?catalog=<id>` defaults to
        built-in; product framing only when the resolved catalog is built-in.
        No removal, no new route, but keeps the dual-mode component the
        incoherence came from.
      **Recommendation: (c) for this slice, (b) later if the dual mode
      grates** — it removes the incoherence without deleting a capability,
      and the `catalogNoun` branch it needs already exists.

      **Store (`app/src/stores/catalogStore.ts`).**
      - Add `$builtInCatalog` (computed over `$catalogs`, finds
        `BUILT_IN_CATALOG_ID`). This is what every product surface reads.
      - Remove `$activeCatalogId` / `$activeCatalog` / `setActiveCatalog`, or
        demote them to a **non-persistent** browse selection scoped to the
        catalog-browsing surfaces. Do NOT keep a persistent global.
      - `ensureBuiltInCatalog()` no longer needs its `$activeCatalogId` seed.
      - **Orphaned localStorage:** the `stac-active-catalog` key stays in
        users' browsers. Either drop it on read or leave it inert — decide
        and comment; do not silently half-migrate. (`stac-catalogs` stays.)

      **Client (`app/src/lib/stac-api/client.ts`).** `stacFetch` currently
      falls back to `$activeCatalog` when no `endpointUrl` is passed.
      **Verified: no production caller relies on that** — every call in
      `lib/stac-api/*` passes `endpointUrl`, and only the unit tests exercise
      the fallback. Repoint the fallback at the built-in catalog (or delete
      it and make `endpointUrl` required) so nothing can implicitly inherit a
      browse selection. `getCatalogForUrl` and the `builtIn` → BFF branch are
      unchanged; the BFF routing is what makes this correctness, not polish.

      **Surfaces to pin to `$builtInCatalog`.** `DashboardPage.tsx` (+ the
      stat tiles, which then measure one world), `CollectionList`,
      `CollectionDetail`, `CollectionForm`, `CollectionEditPage`, `ItemList`,
      `ItemDetail`, `ItemForm`, `ItemEditPage`, and `TopBar`'s global search
      (product hits must come from the built-in catalog — connections and
      processes already do). `SidebarNav`'s stack-status footer should report
      the **platform** STAC API, not whatever was last browsed.

      **Surfaces that keep a selection.** `SearchPage` (local state, default
      built-in — one island, so `useState` is enough; only reach for a
      non-persistent atom if the picker must live outside the island) and
      `CatalogManager`. `CatalogSelector` stays but binds to the local
      selection instead of the global setter; its "Set active" affordance in
      `CatalogManager` becomes "Browse" or goes away.

      **Copy.** Once products are built-in-only, the `catalogNoun` ternaries
      in `CollectionList` / `CollectionForm` and the `CollectionDetail`
      breadcrumb collapse to plain "product" — unless option (c) keeps the
      dual mode, in which case they stay and should be lifted into one shared
      helper (already flagged in the UI-8 follow-ups).

      **Tests / e2e.** `catalog-store.test.ts` asserts the active-id seeding
      and `removeCatalog` reassignment (`:50`, `:67`, `:168`, `:172`);
      `client.test.ts` mocks `$activeCatalog` for the no-`endpointUrl` path;
      `stac-client-bff.test.ts` drives routing through the
      `stac-active-catalog` key; `extension-roundtrip.test.ts` mocks the store.
      All four need reworking around the new store shape. E2E: check whether
      any spec depends on the built-in catalog being active by default (it is
      today, so most should be unaffected) — and note this lands on top of a
      suite that **still has not run since UI-2**.

      **Out of scope.** No `/api/*` change, no migration. Ingesting *from* an
      external catalog's bucket stays a Connection concern and never consults
      a catalog selector — that is the point of the split, not work here.

## Discovered follow-ups

The ones that outlive the remodel are now tracked in `docs/ISSUES.md`:
**I-84** (`/api/alerts` omits `process_id`/`source_id` — the reason three
surfaces carry health caveats), **I-85** (Storybook misses the shared Tailwind
scan + fonts), **I-86** (no true item count per product), **I-87**
(`flow_stats_daily` counter semantics unpinned), **I-88** (`overview.ts`
derivations untested). Everything below is the per-slice detail behind them.

**Still open, not yet an issue:** the full e2e suite has NOT run since UI-2.
Selectors were updated in-slice across `connections.spec.ts`,
`processes.spec.ts` and `extension-forms.spec.ts` by reasoning only. The lead
owns scheduling that run (the M3 session owns Docker/e2e).

**From UI-1:**
- The `tech` utility (`@utility tech` in both `global.css` copies) is defined
  but applied nowhere yet — UI-3…UI-8 apply it to IDs, hrefs, crons and
  counts as those pages are reworked.
- `--color-navy`, `--color-sidebar-*` and the `warning` / `danger` /
  `*-subtle` / `*-border` health tokens are defined but unconsumed until the
  shell (UI-2) and the health chips (UI-3, UI-6) land. Don't "clean them up".
- The old `Header` reads flat under the new palette (uniform navy nav, badge
  variants used as health signals — e.g. the blue `OK` chip on
  `/connections`). Superseded by UI-2's `AppShell` and UI-6's health chips;
  deliberately not patched in UI-1.
- IBM Plex Mono has no `@fontsource-variable` package — the static
  `@fontsource/ibm-plex-mono` 400/500/600 faces are imported instead. If a
  later slice needs another weight, add its `NNN.css` import to
  `Layout.astro`.
- Storybook loads `packages/shared/src/styles/global.css` but nothing imports
  the `@fontsource` packages on that side, so stories render with the system
  fallback stack. Worth a `.storybook/preview` font import in UI-8's polish
  pass (fonts are an `app` dependency today).
- The dev-server `optimizeDeps` fix carried in from the working tree
  (c99346b) lists client island deps explicitly; **CodeMirror 6 must be added
  to that list in UI-5**, or the editor island will 504 on first load.

**From UI-2:**
- **E2E has not run since the shell landed.** `connections.spec.ts`,
  `processes.spec.ts` and `monitoring.spec.ts` navigate by sidebar link and
  `extension-forms.spec.ts` scopes comboboxes by placeholder; selectors were
  updated in-slice but only reasoned about, never executed. Ask the lead to
  schedule a run (M3 session owns Docker/e2e) — ideally before UI-5/UI-6 pile
  more selector churn on top.
- `AppShell` does NOT use shadcn's `SidebarInset` (which renders a `<main>`)
  because each island already owns its `<main>`. If a later slice
  restructures islands to drop theirs, switch to `SidebarInset` then.
- The top bar's title is route-derived (`ROUTE_TITLES` in `TopBar.tsx`); the
  `AppShell title` prop overrides it but nothing passes one yet. Home shows
  "Products" in the bar while `DashboardPage` still renders its own
  `<h1>Dashboard</h1>` — UI-3 should drop that duplicate heading (or pass an
  explicit title). Add a `ROUTE_TITLES` row whenever a route is added.
- Global search matches products/connections/processes by name/id only; a
  connection has no `direction` on `ApiConnection` (direction lives on the
  association), so the hit detail shows the protocol. Relevant to UI-3's
  connection-health strip and UI-6's direction-first framing: direction must
  come from `/api/monitoring/flows` or the associations, not the connection.
- `app/src/hooks/use-mobile.ts` arrived with the shadcn sidebar — first file
  in `app/src/hooks/`. Non-primitive, safe to edit.

**From UI-3:**
- **`/api/alerts` should return `process_id` and `source_id`.** Migration 024
  stores both, but the API shape stops at connection / association / channel /
  collection, so the home overview cannot attribute a `process_stalled`,
  `process_failed` or `process_rate_limited` alert to a product. Home now
  counts what it could not attribute and links to /monitoring — honest, but a
  banner where a red product row belongs. Adding the two fields is an
  `/api/*` change, so it is OUT of the UI remodel's scope: hand it to the
  platform track.
- **No true item count per product.** The home list shows ingest-derived
  `flow_stats.items` (labelled "ingested") because a real count needs
  `numberMatched` per collection — one request each — or a new endpoint.
  UI-4's Overview tab has the same constraint for a single product, where one
  extra request IS affordable.
- The "Ingest (24h)" tile is an all-time total; `flow_stats` is cumulative and
  nothing exposes a 24h rollup. `/api/monitoring/history` is per-subject, so a
  real 24h figure means N requests or a widened endpoint.
- `LineageStrip`'s `full` size currently renders the `medium` layout larger —
  **UI-7 must replace that renderer**, not just call it.
- `LineageStrip`'s `mini` variant ignores `group.href` on purpose (it sits
  inside link rows). If a future caller renders mini outside a link and wants
  navigation, add an explicit `interactive` prop rather than restoring the
  anchors.
- No unit tests were added for `overview.ts`'s derivations (health ranking,
  alert attribution, lineage grouping). It is the most logic-heavy UI module
  in the remodel and is pure functions over plain data — cheap to cover, and
  worth a `new-test` pass in UI-8 or UI-9.

**From UI-4:**
- The Overview tab already existed and was rebuilt, not added: the product
  panel is prepended for built-in catalogs and the original STAC metadata
  (description / extents / license / providers) still renders below it. If a
  later slice wants those restructured, that is a separate decision.
- `CollectionDetail`'s `Tabs` is now CONTROLLED (`tab` / `setTab`) so the
  Overview panel can hand off to Data flow. Anything adding a tab must add it
  to that state's vocabulary, and a deep link to a tab still isn't supported
  (no hash/query sync) — worth doing if the e2e suite or docs ever want to
  link straight to Settings or Data flow.
- The 30d success % covers only the PRIMARY association (first ingest, else
  first flow). A product with several flows shows one flow's rate. A truthful
  product-wide number needs either N history requests or a rollup the API
  doesn't expose; revisit only if operators ask.
- `PUBLIC_TITILER_URL` / `PUBLIC_TIPG_URL` are read with compose defaults
  baked in. They are not declared in an `env.d.ts` — if one is added later,
  declare both there.
- `successRate` and `unanchoredAlerts` live in
  `app/src/components/layout/overview.ts` alongside the home derivations. If
  that file keeps growing, `app/src/lib/overview/` is the better home — but
  keep ONE module: the whole point is that home and the product page cannot
  disagree.

**From UI-5:**
- **I-65 is closed** — CodeMirror 6 landed. ISSUES needs the update in UI-9.
  Seven new runtime deps (`@codemirror/{state,view,commands,language,
  lang-python,lang-json}` + `@lezer/highlight`), all pinned exactly, all from
  the codemirror/lezer orgs, no postinstall scripts. The editor's theme is
  hand-written against the app's CSS variables, so no theme package.
- **The daily-rollup counters are not UI-trustworthy.** `flow_stats_daily`'s
  `runs`/`failed`/`dead` are deltas of a live jsonb, and nothing pins whether
  `runs` includes failures. UI-5 therefore reports success over the RUN LEDGER
  and labels it "last N runs". If someone wants a real 30-day process success
  %, the semantics need pinning first (a contract fixture would be the right
  place) — do NOT quietly relabel the current number as "30d".
- `useRuns(id, { poll })` — the dashboard passes `poll: false`. Anything new
  rendering many `useRuns` at once should do the same.
- The dashboard is one `useRuns` + `useSources` + `useOutputs` per card (3N
  requests, no polling). Fine at operator scale; if a deployment ever has
  dozens of processes, the fix is a list endpoint that embeds the rollup, not
  more client fan-out.
- `processVerdict` deliberately ignores process-anchored ALERTS — same
  `/api/alerts` gap as UI-3/UI-4. Once `process_id` is returned, the verdict
  should fold it in, and the product-page caveat can go.
- `CodeEditor` rebuilds the view when theme / language / editability change
  (compartments would be more machinery than the rebuild costs). If a caller
  ever flips those mid-typing, revisit with `Compartment`.

**From UI-6:**
- **The mockup's create-screen direction toggle was NOT adopted.** A connection
  has no direction column — direction is on `collection_connections` — and the
  same endpoint is routinely both. Adding the toggle needs an `/api/*` +
  schema change (out of scope) or it silently discards the choice. Direction
  instead appears where it is real: the list filter and per-card badges,
  derived from `useFlows()`. If the lead WANTS direction on the connection
  itself, that is a platform-track decision (a `default_direction` hint
  column), not a UI slice.
- The list now calls `useFlows()` for direction. That is the whole
  cross-collection association list on a page that only needs
  connection→direction; harmless today, but a `?connection_id=` filter would
  be the right fix if the association count ever grows.
- Protocol card ORDER is a display constant (`PROTOCOL_ORDER`) separate from
  `WRITABLE_PROTOCOLS`. Adding a protocol to the schema without adding it to
  `PROTOCOL_ORDER` / `PROTOCOL_LABEL` / `PROTOCOL_HINT` will silently hide it
  from the form — the maps are typed `Record<WritableProtocol, …>` so the
  labels fail the build, but the ORDER array does not.
- The mockup's fourth type card, "OGC API hosting", is deliberately absent —
  ROADMAP §8 settled that as the per-collection serving toggle, not a
  connection type (the spec lists it under Out of scope).

**From UI-7:**
- **`packages/shared` is outside the app's Tailwind source scan.** A utility
  class used ONLY in a shared component is never generated; today's shared
  components survive because their classes also appear in app code. UI-7 works
  around it by referencing CSS variables inline for the graph's type colours.
  The proper fix is an `@source "../../../packages/shared/src"` in the app's
  `global.css` — but the two copies are byte-identical by contract and the
  relative path differs (app is one level under the root, packages/shared is
  two), so it needs either a deliberate divergence with a comment on both
  sides, or a shared Tailwind config. **Worth doing in UI-8** before more
  shared components are written against classes that silently do not exist.
- `@theme` → `@theme static` in both global.css copies. Plain `@theme`
  tree-shakes variables no generated utility references, which had deleted the
  whole `--color-chart-*` ramp at runtime. Do not "optimize" it back.
- `LineageStrip`'s `full` renderer shows COLUMN adjacency, not per-node edges.
  Drawing real edges needs measured node positions (refs + ResizeObserver +
  an SVG overlay) — a genuinely bigger component. The Flows list is the edge
  truth in the meantime, and the file says so.
- A connection wired both ways appears in both the Source-connections and
  Destinations columns by design. If that ever reads as duplication, the fix
  is a marker on the node, not a forced single home.
- `/graph`'s h1 is now "Pipeline graph" (was "Pipeline"). `processes.spec.ts`
  asserts `heading { name: "Pipeline", level: 1 }` — still passes, since
  Playwright's name matching is substring-based, but it is now an inexact
  match worth tightening during the e2e run.

**From UI-8:**
- The Tailwind scan gap is FIXED via `app/src/styles/app.css` (imports the
  shared `global.css`, adds `@source`). `Layout.astro` imports `app.css` now.
  **Anything app-only in CSS belongs in `app.css`**, never in `global.css` —
  that pair stays byte-identical for Storybook.
- Storybook still loads `packages/shared/src/styles/global.css` directly and
  therefore has NO `@source` for its own components. Shared-only classes may
  be missing in Storybook even though they now work in the app. Worth a
  `.storybook/preview` CSS entry mirroring `app.css` (also the natural place
  for the `@fontsource` imports Storybook lacks — see the UI-1 follow-up).
- `font-mono` (42 call sites) and the `tech` utility coexist. Both resolve to
  IBM Plex Mono; `tech` adds ligature/tracking tuning. Deliberately NOT swept
  — 42 mechanical edits for a subtle typographic delta, with test-selector
  risk. If a future slice touches those files anyway, prefer `tech`.
- Copy nouns now branch on `catalog.builtIn` in `CollectionList` and
  `CollectionForm` (`catalogNoun`) and in `CollectionDetail`'s breadcrumb from
  UI-4. If a third place needs it, lift `catalogNoun` into a shared module
  rather than copying the ternary again.
- The long-tail pages keep their `max-w-*` containers while home / processes /
  graph / monitoring are full-width. That is deliberate (reading width for
  lists and forms, full width for telemetry), but it is a judgment call a
  designer may want to revisit.
