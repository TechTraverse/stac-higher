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

- [x] **UI-10 · Retire the global "active catalog"; pin products to the
      built-in catalog; add a read-only catalog browser.** [e2e-touch]

      **Why.** UI-3 renamed home to *Products* but left it reading
      `$activeCatalog`, so selecting an external catalog produced an incoherent
      page: the Products tile counted external collections while the
      Connections / Processes / Items-ingested tiles counted the platform;
      every product row degraded to *"unknown / Not wired"*; and **"Create
      product" wrote to the external catalog** (direct or `/api/proxy`, not the
      ADR 0008 BFF). The platform surfaces already ignored the selector — this
      slice finished the split.

      **Decided (lead, 2026-09-01):** option **(b)** — pin `/collections*` to
      the built-in catalog and give external catalogs their own **read-only**
      browser, rather than parameterizing the dual-mode page. External
      create/edit/delete is therefore GONE; it bypassed the BFF, RBAC and audit
      (logged as **I-89**, with what a real design would need).

      **Landed.**
      - Store: `$builtInCatalog` (computed) + `getCatalogById`;
        `$activeCatalogId` / `$activeCatalog` / `setActiveCatalog` removed; the
        orphaned `stac-active-catalog` key is **dropped on init** (asserted in
        `catalog-store.test.ts` against a stubbed `localStorage`).
      - `stacFetch`'s no-`endpointUrl` fallback points at the built-in catalog,
        so nothing can implicitly inherit a browse selection.
      - Pinned: `DashboardPage`, `CollectionList/Detail/Form/EditPage`,
        `ItemList/Detail/Form/EditPage`, `TopBar` search, `SidebarNav` footer.
        `catalogNoun` ternaries collapsed; Data flow / Settings lost their
        `builtIn` guards; Astro page titles now say Product(s).
      - Browser: `app/src/components/browse/*` +
        `/catalogs/[catalogId]/collections[/[collectionId][/items[/[itemId]]]]`
        (`catalogs.astro` moved to `catalogs/index.astro`, same URL). Entered
        from each card's **Browse** link and from external `/search` hits.
      - To stop the two worlds drifting, the catalog-agnostic rendering was
        EXTRACTED, not duplicated: `CollectionMetadata`, `ItemDetailView`,
        `use-token-paging`; shared `CollectionCard`/`ItemCard` take an `href`
        override.
      - `CatalogSelector` is controlled; `SearchPage` owns a local,
        non-persistent selection; `CatalogManager` dropped its redundant copy.
      - Tests: `catalog-store`, `client`, `stac-client-bff`,
        `extension-roundtrip` reworked; `catalogs.spec.ts` rewritten around
        Browse/Platform instead of Set Active/Active.

- [x] **UI-11 · Unit-test `overview.ts` (closes I-88).**
      `app/src/__tests__/overview.test.ts` — 27 cases over
      `buildConnectionChips` (direction joined from flows, health ranking,
      alert override, acknowledged→warn), `buildProductRows` (the four health
      branches, the three attribution paths, unattributed alerts, lineage
      grouping and roll-up, ingest-only item counts), `buildStats`,
      `successRate` (per-direction numerator/denominator, null when
      unmeasured) and `unanchoredAlerts` (removed by A-1, I-84). Mutation-checked:
      breaking `worse()` fails three of them.

- [x] **UI-12 · Storybook CSS + font entry (closes I-85).**
      `packages/shared/.storybook/preview.css` (imports the shared
      `global.css`, states `@source "../src"`) + the four `@fontsource`
      imports in `preview.tsx`, mirroring `Layout.astro`; the two packages are
      now declared in `packages/shared` devDependencies too. Verified in a
      built Storybook: both faces load in the story iframe
      (`document.fonts.check`) and 33 font files ship.
      **I-85 was half wrong and the fix says so:** the missing FONTS were real,
      but shared-only classes were never missing — verified by building with
      and without the `@source` (a marker class in `LineageStrip` is generated
      either way, because Storybook's Vite root IS `packages/shared`). The
      `@source` stays as belt-and-braces, documented as such.
      Storybook's default theme also flipped dark → light, matching ADR 0017.

- [x] **UI-13 · The e2e run.** Ran against `ai/main` on 2026-09-01 with the
      full Docker stack (`E2E_PORT=4399`, worktree, `CREDENTIALS_MASTER_KEY`
      sourced by absolute path). First run: **36 passed, 2 failed, 1 not run**
      — so eight slices of reasoned-only selector edits cost exactly two
      breakages. Both fixed; the suite is **39/39 green**.
      1. `extensions.spec.ts` "edit extension": Astro's dev toolbar is a fixed
         bottom-centre overlay and swallowed the click on "Update Extension"
         (57 retries against `<astro-dev-toolbar> intercepts pointer events`).
         Astro exposes no env switch, so `playwright.config.ts` sets `E2E=1`
         on its webServer and `astro.config.mjs` disables the toolbar on that
         signal. Ordinary `npm run dev` keeps it. NOTE: a REUSED dev server
         (`reuseExistingServer`) still has its toolbar — if this recurs, that
         is why.
      2. `processes.spec.ts`: UI-5 dropped the colon from "Ceiling: N
         runs/hour"; the regex still had it.
      `processes.spec.ts`'s Pipeline heading was already exact — the UI-7
      follow-up had been handled in-slice.

### Added after UI-13 (lead review, 2026-09-01)

- [x] **UI-14 · Enforce the built-in-only write rule in the client.**
      Lead confirmed the external-CRUD removal and asked what remained.
      Nothing in the UI could reach it — all five write-bearing components read
      `$builtInCatalog` — but `stacFetch` still had the branch, so the rule
      lived in convention. It now refuses: a write whose resolved catalog is
      not `builtIn` throws instead of leaving the browser.
      **`POST /search` is exempt** — STAC's item search is a read that speaks
      POST. That carve-out is the same reason `/api/proxy` cannot simply be
      narrowed to GET/HEAD, which is why the proxy is documented under I-89
      rather than changed (it is an `/api/*` surface, out of remodel scope).
      README's unscoped "Full CRUD" claims are now scoped to the platform
      catalog. `stac-client-bff.test.ts` gained four cases: refusal, refusal
      per verb, and `/search` still reaching both an external catalog and the
      built-in one directly.

- [x] **UI-15 · Make a catalog-browse link shareable.** [e2e-touch]
      A browse URL's catalog id is `crypto.randomUUID()` from the browser that
      added the catalog, so a link like
      `/catalogs/92f1f883-…/collections/ak_bare_earth` resolved for NOBODY
      else — not another user, not the same user in another browser. Browse
      links now carry `?src=<catalog url>`, which is the identity everyone
      agrees on:
      - `resolveBrowseCatalog` matches by URL when `src` is present and
        **ignores the path id** — a local catalog that happens to share the
        sender's id is a different catalog, not this one. Without `src` it
        falls back to the id (a link the user made in this browser).
      - A recipient who has the catalog browses it under their OWN id; one who
        does not gets "This link points at a catalog you haven't added" with
        the host, the URL, and an **Add this catalog** button that opens the
        normal `CatalogForm` prefilled (new `defaults` prop — the dialog stays
        an "Add", not an "Edit").
      - **Nothing is fetched before consent.** `src` comes from a link someone
        else wrote, so `parseSrc` discards anything that is not http(s), the
        query hooks stay disabled while the catalog is unresolved, and the
        browser verified zero requests to the target host until Add was
        clicked.
      **A crash the unit tests could not have caught:** a page's breadcrumb and
      card hrefs are JSX PROPS, evaluated before `BrowseFrame` can early-return,
      so `catalog!` threw `Cannot read properties of null (reading 'id')` on
      exactly the case this feature exists for. `browseTarget()` now supplies
      the route's own id + src while unresolved; regression-tested.


## Discovered follow-ups

The ones that outlive the remodel are now tracked in `docs/ISSUES.md`:
**I-84** (`/api/alerts` omits `process_id`/`source_id` — the reason three
surfaces carry health caveats), **I-85** (Storybook misses the shared Tailwind
scan + fonts), **I-86** (no true item count per product), **I-87**
(`flow_stats_daily` counter semantics unpinned), **I-88** (`overview.ts`
derivations untested). Everything below is the per-slice detail behind them.

**The e2e suite is green again** (39/39, 2026-09-01) — see UI-13 for what the
eight slices of reasoned-only selector edits actually broke.

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
- `successRate` lives in `app/src/components/layout/overview.ts` alongside the
  home derivations (`unanchoredAlerts` was removed by A-1, I-84). If
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

**From UI-15:**
- **The address bar keeps the SENDER's catalog id** after a recipient adds the
  catalog; resolution is URL-first so everything works, and every link the page
  renders uses the local id. Rewriting the id segment with `history.replaceState`
  would be tidier but is pure cosmetics — deliberately not done.
- `?src=` is now part of the browse URL contract. Any new browse route, or any
  new link into one, must go through `lib/browse/paths.ts` or it will silently
  produce a link that only works in the author's browser again.
- A shared link still needs the recipient to know whether to trust the host —
  we show it, but the app cannot vouch for it. That is the whole reason the add
  step is a click and not automatic.
- The `proxy` toggle defaults OFF in the add dialog, so a shared link to a
  catalog without CORS headers will add fine and then fail to load. The dialog
  shows the toggle, but nothing hints that it is the fix. Worth a follow-up if
  it bites.

**From UI-13:**
- **Astro's dev toolbar eats Playwright clicks.** It is a fixed bottom-centre
  overlay, so any control that lands under it is unclickable — which looks
  exactly like a broken selector. Now disabled via `E2E=1`, but ONLY for a
  server Playwright starts. A reused dev server keeps the toolbar, so an agent
  running e2e against its own already-running server can still hit this.
- Running e2e from a worktree needs `CREDENTIALS_MASTER_KEY` sourced from the
  main checkout by ABSOLUTE path (`.env` is gitignored, so the worktree has
  none) — as the `run-e2e` skill warns. `E2E_PORT` keeps it off :4321 when
  another dev server owns that port.

**From UI-12:**
- **`LineageStrip` has no story** — the one component UI-3 introduced
  specifically as "pure props, presentational" is the one not in Storybook.
  Its three size variants are exactly what a story is for. Same for
  `ProductOverview`-adjacent shared pieces if any land later.
- Storybook and the app now import the same `@fontsource` packages from two
  different `package.json` files at the same pinned version. If one is bumped
  the other must be too, or Storybook renders a different typeface than the
  product — the failure this slice just fixed, in reverse.
- Tailwind v4's auto source detection follows the **Vite root**, not the CSS
  file's directory. That is why the app needs an explicit `@source` for the
  shared package and Storybook does not. Worth remembering before filing the
  next "class not generated" issue.

**From UI-11:**
- **`buildProductRows`' `lateFlow` branch is unreachable.** `isLate` matches an
  association-anchored alert that is not resolved — exactly the set the `own`
  filter has already claimed as firing or acknowledged, so one of those two
  branches always hits first. The verdict is `warn` either way, so nothing is
  wrong today; deleting the branch (or making it the source of a better reason
  string than "(acknowledged)") is a behavior decision, not a test-slice one.
- The tests fix `alertKindLabel`'s exact strings (e.g. `"ingest inactivity"`,
  lowercase). Renaming a label in `components/monitoring/shared.ts` will fail
  them — which is the point: three surfaces render those strings.

**From UI-10:**
- **The catalog browser is read-only by construction, not by a flag.** There is
  no `readOnly` prop to forget: the browse components simply never render a
  create/edit/delete affordance, and they cannot reach the product routes
  because every link goes through `lib/browse/paths.ts`. Keep it that way —
  reintroducing a mode prop is how the dual-mode incoherence came back.
- `CollectionMetadata` and `ItemDetailView` live in `app/src/components/`, not
  `packages/shared`, because they are built on the APP-only shadcn primitives
  (`tabs`, `table`). Moving them shared means adding those primitives to the
  shared package — two copies of `table` — which is worse than the current
  split. Revisit only if the shared package grows tabs/table for other reasons.
- `BrowseFrame` treats an empty `$catalogs` as "still hydrating" and an unknown
  id as "not found". That is right for a persistent store that seeds the
  built-in entry on init, but it means a genuinely empty catalog list would
  spin. It cannot happen today (`ensureBuiltInCatalog` always seeds one).
- The browse routes read `$catalogs` from localStorage, so an external browse
  URL is NOT shareable between browsers — the recipient has no such catalog id
  and gets "Catalog not found". Making these links portable would mean putting
  the catalog URL in the route, which reintroduces an open-redirect-shaped
  surface. Deliberately not done.
- `/search` results now link into the browse routes for external catalogs via
  `itemHref`. Anything else that renders a card for a catalog it did not
  hard-code must use `collectionHref`/`itemHref` too, or it will silently point
  at the product page for a collection that does not exist there.
- Astro page titles were fixed to product copy in this slice (`Products`,
  `Product: {id}`, `Create product`). `ROUTE_TITLES` in `TopBar.tsx` and the
  `<Layout title>` are two separate title systems — a new route needs both.
