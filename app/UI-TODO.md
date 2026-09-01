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
- [ ] **UI-2 · AppShell: sidebar + top bar.** [e2e-touch]
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
- [ ] **UI-3 · Home overview.**
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
- [ ] **UI-4 · Product Overview tab.**
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
- [ ] **UI-5 · Processes dashboard + editor + CodeMirror.** [e2e-touch]
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
- [ ] **UI-6 · Connections restyle.** [e2e-touch]
      `ConnectionsPage.tsx` + `ConnectionForm.tsx`: direction-first framing
      (Ingest source / Distribution destination segmented control per the
      mockup's create screen; type cards for s3/sftp/ftp/https), health
      chips on the list (status dot + protocol tag + direction), copy sweep
      (source/destination). No behavior change to test/host-key/credential
      flows. Update `connections.spec.ts` selectors as needed.
- [ ] **UI-7 · Pipeline graph.**
      `PipelineGraph.tsx`: columnar left→right layout (source connections →
      source products → processes → derived products → destinations) using
      the full `LineageStrip` visual language, health-colored nodes/edges,
      legend, mono ids. Same `/api/monitoring/graph` data. Page copy:
      "Pipeline graph".
- [ ] **UI-8 · Monitoring + long-tail + terminology sweep.** [e2e-touch]
      `MonitoringPage.tsx` (+Alerts/Flows/Channels cards): visual alignment
      (cards, health tokens, mono accents). Catalogs / Search / Extensions
      pages + all forms: visual alignment pass only (spacing, cards,
      buttons, CatalogSelector placement from UI-2). Repo-wide UI copy
      sweep for remaining collection/association strings on platform
      surfaces (grep user-facing strings; keep "collection" in the catalog
      browser/search context). Both-theme screenshot pass over every page;
      fix stragglers. Update `monitoring.spec.ts` if labels change.
- [ ] **UI-9 · Wrap-up.** Update `docs/FEATURES.md` (UI surface
      descriptions), ROADMAP §8 rows' page copy where stale, ISSUES: close
      I-65 (CodeMirror landed), log any deferred follow-ups from below.
      Ask the lead to schedule the full e2e run (M3 session owns it);
      fix fallout on `ai/main`.

## Discovered follow-ups

(append here during iterations)

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
