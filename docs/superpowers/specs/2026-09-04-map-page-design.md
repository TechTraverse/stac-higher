# Map page — the catalog's products as map layers — design

**Date:** 2026-09-04
**Status:** **approved by the lead 2026-09-04** (design reviewed section by
section in chat; §2 records the answers). The V queue in `TODO.md` is
copied from §10; V-1 goes first because every other slice consumes what
it extracts.
**Scope source:** the animated collection preview (commit `935ebf7`,
2026-09-04 — `CollectionPreviewTab`, `TimeSlider`, `RasterTileLayer`,
`serving/frames.ts`), OGC serving (`docs/serving.md`, G-4/G-5, I-68/I-69),
the map components in `packages/shared/src/components/map/`, ADR 0017 (the
shell and its nav groups).

## 1. Problem

The platform can now serve its products as tiles (titiler-pgstac for
rasters, tipg for vector tables) and can animate ONE product on its own
page. There is no place to look at the catalog as a whole: several products
on one map, imagery beside footprints, stepping through time together. For
a data platform whose users are trying to see what they have, a map is the
missing first page.

Everything needed already exists in pieces. A maplibre shell, item
footprints as GeoJSON, a working raster tile layer, titiler URL builders,
collection and search hooks, a time slider with tiler-paced playback, and a
demo collection (`goes-geocolor`) publishing `visual`-role COGs every five
minutes. What is missing is the assembly, and the pieces were each written
for a single-instance page and need to coexist.

## 2. Lead answers (2026-09-04)

1. **Purpose:** visual browse of the platform's data — operators and members
   open it to see what the built-in catalog holds. Discovery and QA, not an
   analyst workbench and not a curated showcase.
2. **Layer kinds in v1:** item footprints (GeoJSON, any collection), raster
   tiles through titiler-pgstac, vector tiles through tipg. External
   catalogs' `web-map-links` / `renders` links are OUT.
3. **Time:** a time slider with playback, not "latest only" and not a plain
   picker.
4. **tipg:** a generic, small picker over whatever tipg's `/collections`
   lists, even though the standing stack has no real vector table today
   (tipg serves PostGIS tables, not STAC collections; the platform's tables
   are pgstac internals and the non-spatial `stac_higher` schema). Seeding a
   demo vector table is a follow-up, not v1.
5. **Approach:** the lead approved a per-frame search-registration design
   (Approach A), then pointed at the collection preview that landed the same
   morning. The preview's approach — pin `datetime` on the **collection
   mosaic** tile URL, no registration, no POST — is strictly better, and the
   lead's direction was to reuse it and, where the preview's machinery is
   duplicated, refactor the preview to share. This spec does that (§3).

## 3. Reuse and the extraction (V-1)

The preview tab's approach is adopted whole. Its three load-bearing rules
carry over unchanged and are restated here so nobody re-learns them:

- The `datetime` handed to the tiler is the catalog's own string,
  UNMODIFIED. The filter matches the instant exactly; normalising it empties
  the map.
- Playback waits for tiles rather than dropping frames — advance only when
  `map.areTilesLoaded()`, capped — so the first pass runs at the tiler's
  pace and replays at full rate off the browser cache. The outgoing frame
  stays painted beneath the incoming one, and exactly ONE frame is warmed
  ahead. `areTilesLoaded` is map-wide, which is exactly what a multi-layer
  page needs.
- Zoom limits and bounds come from the newest item's TileJSON, because the
  collection mosaic advertises 0–24 over the whole extent.

### 3.1 Already reusable as-is

`buildPreviewFrames` / `PreviewFrame` (`app/src/lib/serving/frames.ts`),
`previewAssetCandidates` (`serving/preview.ts`), `collectionTileUrlTemplate`
+ `collectionViewerUrl` (`serving/urls.ts`), `useItemTileJson`
(`serving/queries.ts`), `RasterTileLayer` (already id-namespaced),
`TimeSlider` and the `Slider` primitive (shared).

### 3.2 Extracted or added in the shared package

- **`RasterFrameStack`** (`packages/shared/src/components/map/`). Props:
  `id` (namespace), `frames: { key: string; tiles: string[] }[]`, `index`,
  `bounds?`, `minzoom?`, `maxzoom?`, `opacity?` (the layer's own opacity,
  applied to the visible frames), `beforeId?`. It mounts the previous
  frame, the current frame and one lookahead frame (deduped), swaps them by
  opacity with `opacityTransitionMs={0}`, and tracks "previous" internally
  exactly as the tab does today. Pure presentation: no fetching, no notion
  of time. The lookahead constant (1) lives here with its reason.
- **`FootprintLayer` gains `id?: string`**, mirroring `RasterTileLayer`:
  omit for the single-instance pages, pass a per-layer value on the map
  page. The style helpers in `lib/map/styles.ts` become functions of the
  source id where they currently hard-code it. It also gains
  `opacity?: number` (applied to fill and line) and `beforeId?`.
- **`VectorTileLayer`** (new, shared). Props: `id`, `url` (a TileJSON
  document URL — maplibre fetches it and takes tiles, bounds and zoom range
  from it, which is what tipg publishes), `sourceLayer` (tipg's is `default`; verified against the running tipg in
  V-4 before it is hard-coded as the default), `opacity?`, `beforeId?`.
  Renders three maplibre layers on the one source: fill (polygons), line
  (all), circle (points), with `filter` on `$type` so each geometry type is
  drawn once. Theme-token colours from `styles.ts`.
- **`tipgBaseUrl(env)`** joins `titilerBaseUrl` in `app/src/lib/serving/
  urls.ts` (app, not shared — it reads `import.meta.env`), plus
  `tipgCollectionsUrl()` and `tipgTileJsonUrl(collectionId)` →
  `{base}/collections/{id}/tiles/WebMercatorQuad/tilejson.json`. The two
  inline reads of `PUBLIC_TIPG_URL` (`SettingsTab.tsx`,
  `ProductOverview.tsx`) switch to the builder.

### 3.3 The preview tab after the refactor

`CollectionPreviewTab` keeps its items query, frames, asset picker, frame
count select, zoom-hint query and viewer link, and replaces its mounted-
frames block with one `<RasterFrameStack id="preview" …>`. Its behaviour
and its four existing test files are unchanged; those tests staying green
is the proof the extraction preserved behaviour. No new preview features.

## 4. The page

### 4.1 Route and shell

- `app/src/pages/map.astro` — thin shell, `client:only="react"`, mounting
  `MapPage` from `app/src/components/map/MapPage.tsx` (the `new-page`
  skill's shape: `QueryProvider`, the shared `Layout`).
- Sidebar: an entry in the **operate** group of `SidebarNav.tsx`, after
  Products: `{ href: "/map", label: "Map", icon: Map }` (lucide `Map`).
- `TopBar.tsx` `ROUTE_TITLES`: `[/^\/map$/, "Map"]`.
- The page fills the shell's content height. Layout: layer panel (fixed
  width, left, scrolls independently) · map (fills the rest) · time bar
  docked to the bottom of the map, shown only while at least one
  time-aware layer exists. Basemap stays theme-driven (no picker in v1).

### 4.2 State

A `useReducer` in the island — no nanostore, no persistence in v1 (a
shareable URL is §8). Shape:

```ts
type LayerKind = "footprints" | "imagery" | "vector";
interface MapLayer {
  id: string;                 // client-generated, stable for the session
  kind: LayerKind;
  /** footprints | imagery: built-in-catalog collection id. vector: tipg collection id. */
  sourceId: string;
  title: string;
  asset?: string;             // imagery only; a previewAssetCandidates key
  visible: boolean;
  opacity: number;            // 0..1, default 1
}
interface MapState {
  layers: MapLayer[];         // draw order, bottom first
  frameSpan: 25 | 50 | 100 | 200;   // newest items per STAC layer, page-wide
  axisIndex: number | null;   // null = newest (end of axis), as the preview does
}
```

Actions: `add`, `remove`, `move` (up/down one step), `setVisible`,
`setOpacity`, `setAsset`, `setFrameSpan` (resets `axisIndex` to null),
`setAxisIndex`. `add` refuses a duplicate `(kind, sourceId)` — the reducer
is a pure function in `app/src/lib/map/state.ts` and is unit-tested.

### 4.3 Data per layer

Each `footprints` or `imagery` layer owns one items query — the existing
`useItems(endpointUrl, collectionId, { limit: frameSpan, sortby:
"-datetime" })` — and derives `buildPreviewFrames(items)` from it. Two
layers on the same collection (footprints + imagery) share the query
through TanStack's key. An `imagery` layer additionally runs the zoom-hint
`useItemTileJson` for its newest item and asset, and, like the preview,
waits for that query to settle before mounting frames.

A `vector` layer holds only its TileJSON URL; maplibre fetches it.

Serving-enabled state is read per collection with `useCollectionSettings`.

### 4.4 The shared time axis

`app/src/lib/map/axis.ts`, pure and unit-tested:

- `buildAxis(layerFrames: Map<layerId, PreviewFrame[]>): AxisTick[]` — the
  sorted union of every time-aware layer's frame **instants** (the parsed
  start; the label is formatted the same way `frames.ts` does). Two layers
  whose items share an instant produce one tick.
- `resolveLayerFrame(tickInstant, frames): number | null` — the index of
  the layer's newest frame at or before the tick ("hold last"), or `null`
  when the layer has nothing that early (the layer draws nothing for that
  tick). Binary search; frames are already sorted.

The page renders `TimeSlider` over the axis labels with `index =
axisIndex ?? last`, and for each time-aware layer passes the resolved
frame index into its `RasterFrameStack` (imagery) or the resolved frame's
items into its `FootprintLayer` (footprints show the items of their
current frame, not the whole span). `canAdvance` is the preview's
`areTilesLoaded` rule with the same 10 s cap. Vector layers ignore the
axis.

When no time-aware layer is present the axis is empty and the time bar is
hidden; when the span changes every layer's query refetches and the axis
returns to the newest tick.

### 4.5 The layer panel

- **Layer list**, topmost layer first. Row: kind icon (`Layers` for
  footprints, `Image` for imagery, `Hexagon` for vector), title, visibility
  toggle (`Eye`/`EyeOff`), an opacity `Slider`, move up / move down
  buttons (no drag-and-drop: it would need a dependency), remove. An
  imagery row shows the asset `Select` when its collection publishes more
  than one tileable key (`previewAssetCandidates`, same control as the
  preview tab). A STAC layer whose items query yields no usable frame shows
  a muted "no items with a timestamp" line under its title; a layer never
  errors. Draw order on the map follows list order, with `beforeId`
  chaining so imagery never covers footprints of a layer above it.
- **Add layer** popover (`app/src/components/ui/popover`), two sections:
  - **Products** — `useCollections($builtInCatalog)`. Each collection
    offers **Footprints** (always) and **Imagery** (only when
    `servingEnabled` is true AND a probe of its newest items —
    `useItems(…, { limit: 5, sortby: "-datetime" })` — yields a
    `previewAssetCandidates` key; the probe and the settings query run per
    listed collection when the popover opens, and both are small). A
    combination already on the map is shown as "added" and disabled.
  - **Vector tiles** — `GET {tipg}/collections` via a `useTipgCollections`
    query (`credentials: "omit"`, `retry: false`, silent on failure, in
    `serving/queries.ts` next to `useItemTileJson`). Each collection adds a
    `vector` layer with its `tipgTileJsonUrl`. Unreachable tipg or an empty
    list renders the section as a one-line "no vector tiles published"
    note, never an error.
- **Empty state** when there are no layers: the panel shows an
  `EmptyState` inviting the first add; the map shows the basemap at the
  default world view.

### 4.6 Map interaction

- Hovering a footprint applies the existing `hover` feature-state style
  and shows a small tooltip (item id, datetime). Clicking opens
  `/collections/{collectionId}/items/{itemId}` in a new tab. Interactive
  layer ids are collected from the footprint layers so `interactiveLayerIds`
  stays correct as layers come and go.
- Imagery and vector layers have no hover or click behaviour in v1.
- Camera: on the **first** layer added the map fits that layer's extent
  (imagery: the zoom-hint bounds if present, else the collection's spatial
  extent; footprints: the collection extent; vector: none — tipg TileJSON
  bounds are used once loaded if available). After that the camera is
  left alone.

### 4.7 Degradation

The preview's rule everywhere: no serving, no tileable asset, an
unreachable tiler or tipg, an item without a timestamp — all mean "that
option is absent or that layer draws nothing", never an error state on the
page. The tilers are called straight from the browser with credentials
omitted, as G-5 does; I-1/I-69 still apply and are not changed here.

## 5. Costs accepted

- Several imagery layers each mount up to three raster sources, so tiles
  contend for the tiler's few connections harder than the single-product
  preview. Lookahead stays at one; if a demo needs a smooth first pass the
  fix is server-side warming, as the preview follow-up already notes.
- The per-collection probes in the picker are N small requests per open.
  The built-in catalog has a handful of collections; if that grows, cache
  the probe in the collections list view later.
- Footprints per frame, not per span: a frame's items are what the raster
  shows, so the two kinds line up. Whole-span footprints are a follow-up
  toggle if wanted.

## 6. Testing

- **Unit (vitest):** `axis.ts` (union, dedupe, hold-last, a layer with no
  early frames → null, vector layers excluded, empty input); `state.ts`
  reducer (add / duplicate refused / remove / move at the ends / visible /
  opacity / asset / span resets axis); tipg URL builders (trailing-slash
  strip, encoding).
- **Component:** `RasterFrameStack` mounts exactly previous + current +
  one lookahead with the right opacities and no transition (moved from the
  preview-tab test where that assertion lives today); `VectorTileLayer`
  renders the three sub-layers on one source; `FootprintLayer` with two
  ids on one map does not collide; `MapPage` (hooks mocked, the
  `collection-preview-tab.test.tsx` pattern): empty state, add footprints
  from the picker, add imagery only when serving + candidates allow, time
  bar appears only with a time-aware layer and hides when the last one is
  removed, remove/reorder update the list.
- **Refactor proof:** the four preview test files pass unchanged.
- **Storybook:** `VectorTileLayer.stories.tsx`, `RasterFrameStack.stories.tsx`.
- **E2E:** `app/e2e/map.spec.ts` — page loads, sidebar entry active, picker
  lists the built-in catalog's collections, adding a footprints layer shows
  a row in the layer list and a footprint source under that layer's
  namespaced id on the map. Needs only pgstac (:8082), which
  the suite already assumes for some specs. Imagery and tipg paths are
  live-only (an env gate like `goes-loop.spec.ts`), since CI has no tiler.
- **Live check before the V-3 and V-4 merges:** the standing GOES demo —
  `goes-geocolor` imagery + `goes-abi-mcmipc` footprints on one map,
  playback across 50 frames, terminator moving, no console errors.

## 7. Docs

`docs/FEATURES.md` (a Map page entry under serving), `docs/serving.md`
(the page as the third consumer of the tilers; the tipg TileJSON path), the
`project-conventions` skill only if a new pattern emerges (none expected).
No ADR: no hard-to-reverse choice is made.

## 8. Deferred (log in `docs/ISSUES.md` / follow-ups when V-4 closes)

- Shareable URL state (layers, span, tick, camera) — the obvious next
  step for "look at this".
- The STAC `renders` extension (band / rescale / colormap) instead of the
  tiler's defaults; a legend.
- A basemap picker.
- Whole-span footprints toggle; click-through on imagery to the item at
  that tick.
- A seeded demo vector table (e.g. GOES hotspot points) so the tipg
  section has something to show.
- A pre-warmed mosaic cache for smooth first-pass playback (shared with
  the preview follow-up).

## 9. Decisions taken by the agent

- **Adopt the preview's mosaic-with-datetime approach over Approach A.**
  No POST, no CORS pre-flight, composites tiled timesteps for free.
- **Frames per layer, axis as a union, hold-last resolution.** The
  alternative — one axis from the "primary" layer — breaks the moment a
  second product with a different cadence is added.
- **Footprints show the current frame's items.** Keeps the two STAC kinds
  in step; whole-span is a toggle later.
- **Reducer in the island, no nanostore.** Nothing else on the site reads
  map state; a store would be persistence by accident.
- **No drag-and-drop.** Up/down buttons cost nothing; DnD costs a
  dependency.

## 10. Queue (copied to `TODO.md` as the V queue)

- **V-1 · Shared pieces + preview refactor.** §3: `RasterFrameStack`,
  `FootprintLayer` `id`/`opacity`/`beforeId`, `VectorTileLayer`, styles as
  functions of the source id, `tipgBaseUrl` + tipg URL builders replacing
  the inline env reads; `CollectionPreviewTab` consumes `RasterFrameStack`;
  stories; preview tests unchanged and green. Goes first.
- **V-2 · Page shell, state, footprints.** §4.1, §4.2, §4.3 (footprints
  half), §4.5 (list + Products section, Footprints only), §4.6, §4.7.
  Reducer + tests, `MapPage` component tests, the e2e smoke spec.
  Depends on V-1.
- **V-3 · Imagery layers + the shared time axis.** §4.3 (imagery), §4.4,
  the Imagery option in the picker with its serving + probe gate, the
  asset select per row, the time bar. `axis.ts` + tests. Live check on
  the GOES demo. Depends on V-2.
- **V-4 · tipg vector layers + docs.** §4.5 Vector tiles section,
  `useTipgCollections`, `VectorTileLayer` on the page, source-layer name
  verified live, §7 docs, §8 deferrals logged. Depends on V-2 (not V-3).
