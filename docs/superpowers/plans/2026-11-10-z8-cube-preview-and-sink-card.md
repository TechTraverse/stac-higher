# Z-8 cube Preview + sink card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Issues:** #94 (Part A, Preview cube branch, branch `feat/z8-cube-preview`) and #168 (Part B, sink card, branch `feat/z8b-sink-card`). Queue Z, epic #83. The two parts touch different files except `app/src/lib/query/keys.ts`, where each adds its own block in a different place. They land as two PRs, in either order.

**Goal:** A cube collection previews as an animated map of its virtual Icechunk cube, with a click-for-time-series chart. Operators create and run the cube sink from the collection's Settings tab.

**Architecture:** Part A reads only the collection document. The frames come from `assets.<cube>.stac_higher:time_values`; the tiles and EDR come from `cube-server` (`PUBLIC_CUBE_SERVER_URL`). It needs no new app API. Part B is a client for the Z-8a API: `GET/PUT/PATCH /api/collections/[id]/cube-sink` and `GET /api/associations/reference-sources`. It uses `cubeSinkHealth`, the label maps in `app/src/lib/cubes/status.ts` and `cubeSinkPutSchema` from `app/src/lib/cubes/schemas.ts`.

**Tech Stack:** Astro 7 + React 19, TanStack Query, React Hook Form + Zod v4, MapLibre via `react-map-gl/maplibre`, shared components from `@stac-higher/shared`, Vitest + Testing Library, Storybook 10.

**Spec:** `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` §3.3, §8, §9. Consumer facts: `gh issue view 94` and `gh issue view 168` ("Facts…" sections). API: the module docs of `app/src/pages/api/collections/[id]/cube-sink.ts` and `app/src/pages/api/associations/reference-sources.ts`. Server: `docs/serving.md` "cube-server".

## Global Constraints

- **No new dependency** in either package. `TimeSeriesChart` is plain SVG.
- **Tile template** (row before column): `{base}/datasets/{collectionId}/tiles/WebMercatorQuad/{z}/{y}/{x}?variables={variable}&t={t}&style=raster/{colormap}&colorscalerange={min},{max}&width=256&height=256&f=png`. `{z}/{y}/{x}` stay literal. Every value is `encodeURIComponent`ed.
- **`t` is the `stac_higher:time_values` entry verbatim** (nanoseconds and trailing `Z`). Never re-format it. Parse a copy only for labels and sorting.
- **Always pass `colorscalerange`.** Default style constant: `CUBE_DEFAULT_STYLE = { colormap: "gray_r", range: [190, 310] }`. That is C13 brightness temperature in K: the spike's 190–310 K range, inverted so cold cloud tops are white, the usual IR rendering. `gray_r` is a matplotlib colormap name, which xpublish-tiles resolves through `mpl.colormaps`.
- **EDR:** `{base}/datasets/{collectionId}/edr/position?coords=POINT({lon} {lat})&parameter-name={variable}&f=csv`, GET only. The CSV header is `t,<variable>,longitude,latitude`. `t` is naive UTC `YYYY-MM-DD HH:MM:SS.nnnnnnnnn`. An empty value is null. The `x-cube-missing-steps` response header is a **count** (CORS-exposed).
- **Tile 422** (`no-store`): the asset can be one server poll (≤ 15 s) ahead of the server. Retry the frame later. Never mark it failed.
- **The cube branch comes first**, before the serving gate, `useItems` and `useItemTileJson`. Bounds come from `collection.extent.spatial.bbox[0]`. `cube:dimensions.*.reference_system` may be absent; nothing reads it.
- **Sink card:** always send `enabled` on PUT. Lock the source and the layout fields (`append_dim`, `variables`, `loadable_variables`; `parser` is fixed) once `last_snapshot_id` is set. **Refuse every write (PUT and PATCH) while `last_snapshot_id` is null and `ledger.counts.pending > 0`.** The card is mounted only after `useCollectionSettings` succeeded, so the caller manages the collection and a GET 404 means "no sink yet".
- Query keys only through `app/src/lib/query/keys.ts`. Never inline arrays.
- Never hand-edit `components/ui/`. Use `Badge`, `Switch`, `Card*`, `Input`, `Label`, `Select*`, `Button`, `Skeleton`, `EmptyState` from `@stac-higher/shared`.
- Gates: `npm run verify` from the worktree root, plus `npm run build-storybook` in `packages/shared/` for Part A. No e2e, no dev server, no Docker.

## Decisions (lead may overturn)

1. **Variable to render.** The collection document has no `cube:variables` (the Z-5 writer emits only `cube:dimensions`). `cubeVariable(collection)` takes the first key of `cube:variables` whose `type` is `"data"`. If there is none, it falls back to `CUBE_DEFAULT_VARIABLE = "CMI"`. Follow-up issue: the pipeline writes `cube:variables` from `config.variables`. The Preview doesn't call the sink API, because viewers outside the group get a 404 there.
2. **The Preview tab is advertised for a cube collection regardless of `serving_enabled`.** `cube-server` serves exactly the enabled sinks, so the sink's `enabled` is the cube's serving switch. `CollectionDetail.tsx`'s `previewAvailable` gains `|| findCubeAsset(collection) !== null`. This is outside the issue's file list, but nothing else shows the tab.
3. **Preload progress** counts frames whose tiles finished loading. A frame counts once the map reports all tiles loaded while that frame is current (the existing `canAdvance` returning true, or a map `idle` event). The text reads `Loaded n of N frames` and hides at N. No extra prefetch: `RasterFrameStack`'s one-frame lookahead is deliberate (connection starvation).
4. **422 retry** works by remounting the frame, not by changing its URL. xpublish-tiles treats an unknown query parameter as a dimension selector, so a cache-buster parameter is unsafe. A tile error with `status === 422` on source `cube-frame-${n}` schedules a retry after `CUBE_TILE_RETRY_MS = 5000`. The retry bumps that frame's attempt counter, and the frame's `key` becomes `${t}#${attempt}`, which remounts the source. There are at most `CUBE_TILE_MAX_RETRIES = 3` retries. After that the frame shows whatever it got, and playback is not blocked: an errored tile counts as loaded for `areTilesLoaded`.
5. **Sink card placement:** inside `SettingsTab`, below the existing settings card, on every collection. Operator or admin: the full card, with a "Set up a virtual cube" button when there is no sink. Anyone else: the status block when a sink exists, nothing otherwise.
6. **Form defaults** come from the spec §3.1 example: `variables` `CMI, DQF`; `loadable_variables` `t, x, y, goes_imager_projection`; `append_dim` `t`; `max_steps` `288`; `max_age` `24h`. `asset_key` and `on_late` are not editable (`cube`, `skip`). There is no DELETE button (YAGNI; disabling covers it).

## Review Focus

1. **A `t` with nanoseconds.** `Date.parse` on `…36.714359936Z` is engine-dependent, so labels and sort order must come from a fractional part trimmed to ms. The tile URL must carry the untrimmed value (Task A2 tests both).
2. **A cube with zero steps,** right after creation or after a trim to empty: `time_values` is absent or `[]`. Show the empty state, not a crash or a blank map (Task A4).
3. **An EDR click during a NODD outage:** a 200 with every value empty. The chart shows "no values" and the missing-steps count, not a flat line at 0 (Tasks A2, A3).
4. **An operator saving while the first append runs.** A PUT now would make the pipeline discard and redo the batch. Save and the toggle are disabled, and the reason is shown (Task B3).
5. **A PUT after the repository exists that changes a locked field** (stale tab, or a concurrent edit). The 409 `cube_layout_locked` / `cube_sink_conflict` renders as a reload prompt, not a toast that vanishes (Task B3).

---

## Part A — #94, Preview cube branch (`feat/z8-cube-preview`)

### Task A1: Cube server URL builders + env

**Files:**
- Modify: `app/src/lib/serving/urls.ts` (new section after tipg)
- Modify: `.env.example` (next to `PUBLIC_TITILER_URL`, line ~60: `# PUBLIC_CUBE_SERVER_URL=http://localhost:8086`)
- Modify: `docs/backend.md` (env table row for `PUBLIC_CUBE_SERVER_URL`, next to `PUBLIC_TITILER_URL`)
- Test: `app/src/__tests__/serving-urls.test.ts` (extend)

**Interfaces — Produces:**
```ts
export interface CubeStyle { colormap: string; range: readonly [number, number] }
export const CUBE_DEFAULT_STYLE: CubeStyle; // { colormap: "gray_r", range: [190, 310] }
export function cubeServerBaseUrl(env?: Record<string, string | undefined>): string; // default "http://localhost:8086", trailing slashes stripped
export function cubeTileUrlTemplate(collectionId: string, variable: string, t: string, style?: CubeStyle, base?: string): string;
export function cubeEdrPositionUrl(collectionId: string, lon: number, lat: number, variable: string, base?: string): string;
```

- [ ] **Step 1: Write the failing tests**
```ts
expect(cubeServerBaseUrl({})).toBe("http://localhost:8086");
expect(cubeServerBaseUrl({ PUBLIC_CUBE_SERVER_URL: "https://cube.example/" })).toBe("https://cube.example");
expect(cubeTileUrlTemplate("goes19-c13-cube", "CMI", "2026-10-03T17:02:36.714359936Z", undefined, "http://c")).toBe(
  "http://c/datasets/goes19-c13-cube/tiles/WebMercatorQuad/{z}/{y}/{x}" +
  "?variables=CMI&t=2026-10-03T17%3A02%3A36.714359936Z&style=raster%2Fgray_r" +
  "&colorscalerange=190%2C310&width=256&height=256&f=png");
expect(cubeEdrPositionUrl("goes19-c13-cube", -97.123456, 38.5, "CMI", "http://c")).toBe(
  "http://c/datasets/goes19-c13-cube/edr/position?coords=POINT(-97.1235%2038.5)&parameter-name=CMI&f=csv");
```
Also: a collection id with a space or slash is encoded in the path.
- [ ] **Step 2:** `npx vitest run src/__tests__/serving-urls.test.ts` from `app/`. Expect FAIL (not exported).
- [ ] **Step 3: Implement.** lon/lat are rounded to 4 decimals (`Number(x.toFixed(4))`, ~11 m), so a re-click on the same spot reuses the query cache. `{z}/{y}/{x}` are assembled by hand, as `collectionTileUrlTemplate` does.
- [ ] **Step 4:** re-run. Expect PASS.
- [ ] **Step 5: Commit** `#94: cube-server URL builders and PUBLIC_CUBE_SERVER_URL`.

### Task A2: Cube frames, cube asset lookup, EDR CSV

**Files:**
- Create: `app/src/lib/serving/cube-frames.ts`
- Create: `app/src/lib/serving/cube-edr.ts`
- Modify: `app/src/lib/serving/queries.ts` (add `useCubePosition`)
- Modify: `app/src/lib/query/keys.ts` (in `servingKeys`: `cubePosition: (collectionId, variable, lon, lat) => [...servingKeys.all(), "cube-position", collectionId, variable, lon, lat] as const`)
- Test: `app/src/__tests__/cube-frames.test.ts`, `app/src/__tests__/cube-edr.test.ts`

**Interfaces — Produces:**
```ts
// cube-frames.ts
export const CUBE_MEDIA_TYPE = "application/vnd.zarr+icechunk";
export const CUBE_DEFAULT_VARIABLE = "CMI";
export const CUBE_DEFAULT_FRAME_COUNT = 24;
export interface CubeAssetRef { key: string; timeValues: string[] }
export function findCubeAsset(collection: StacCollection): CubeAssetRef | null; // first asset whose type === CUBE_MEDIA_TYPE; timeValues = the string entries of stac_higher:time_values, else []
export function cubeVariable(collection: StacCollection): string;
export function cubeTimeMs(t: string): number; // fractional seconds trimmed to 3 digits before Date.parse; NaN when unparseable
export interface CubeFrame { t: string; label: string } // label = formatFrameLabel(cubeTimeMs(t))
export function buildCubeFrames(timeValues: readonly string[], last?: number): CubeFrame[]; // unparseable dropped, duplicates collapsed, oldest first, then the newest `last`
// cube-edr.ts
export interface CubeSeriesPoint { t: number; value: number | null }
export function edrTimeToIso(t: string): string | null; // "2026-10-03 17:02:36.714359936" → "2026-10-03T17:02:36.714Z"
export function parseEdrPositionCsv(text: string, variable: string): CubeSeriesPoint[];
export interface CubePosition { points: CubeSeriesPoint[]; missingSteps: number }
export function fetchCubePosition(collectionId: string, lon: number, lat: number, variable: string): Promise<CubePosition>;
// queries.ts
export function useCubePosition(collectionId: string, variable: string, point: { lon: number; lat: number } | null): UseQueryResult<CubePosition>;
```

- [ ] **Step 1: Write the failing tests** (`cube-frames.test.ts`)
  - `buildCubeFrames(["2026-10-03T17:07:36.714359936Z", "2026-10-03T17:02:36.714359936Z", "bogus"])` → two frames, oldest first, `t` unchanged (nanoseconds intact), labels `2026-10-03 17:02 UTC` / `2026-10-03 17:07 UTC`.
  - 30 values with `last = 24` → 24 frames, the newest 24. `last` undefined → 30.
  - Duplicate `t` → one frame.
  - `findCubeAsset`: a collection whose asset `cube` has the media type and `stac_higher:time_values: ["a", 1, "b"]` → `{ key: "cube", timeValues: ["a", "b"] }`. A COG-only collection → `null`. A cube asset without `time_values` → `timeValues: []`.
  - `cubeVariable`: `{"cube:variables": {DQF: {type: "auxiliary"}, CMI: {type: "data"}}}` → `"CMI"`. Absent → `"CMI"`. `{"cube:variables": {Rad: {type: "data"}}}` → `"Rad"`.
- [ ] **Step 2: Write the failing tests** (`cube-edr.test.ts`)
  - `edrTimeToIso("2026-10-03 17:02:36.714359936")` → `"2026-10-03T17:02:36.714Z"`. `"2026-10-03 17:02:36"` → `"2026-10-03T17:02:36.000Z"`. `"junk"` → `null`.
  - `parseEdrPositionCsv("t,CMI,longitude,latitude\n2026-10-03 17:02:36.714359936,251.5,-97,38.5\n2026-10-03 17:07:36.714359936,,-97,38.5\n", "CMI")` → `[{t: Date.parse("2026-10-03T17:02:36.714Z"), value: 251.5}, {t: …17:07…, value: null}]`.
  - The variable column is found by header name: the same rows with the columns reordered parse identically. A header without the variable → `[]`. A blank trailing line is ignored. Rows with an unparseable `t` are dropped.
  - `fetchCubePosition` with a mocked `fetch` returning 200, the CSV and header `x-cube-missing-steps: 1` → `{ points, missingSteps: 1 }`. Header absent → `0`. A 404 throws an `Error` whose message carries the status.
- [ ] **Step 3:** run both files. Expect FAIL.
- [ ] **Step 4: Implement.** `useCubePosition`: `enabled: point !== null`, `staleTime: 60_000`, no retry on 4xx.
- [ ] **Step 5:** run both files. Expect PASS.
- [ ] **Step 6: Commit** `#94: cube frames from stac_higher:time_values and the EDR position CSV reader`.

### Task A3: `TimeSeriesChart` in packages/shared

**Files:**
- Create: `packages/shared/src/components/shared/TimeSeriesChart.tsx`
- Create: `packages/shared/src/components/shared/TimeSeriesChart.stories.tsx` (stories: `Continuous`, `WithGaps`, `IsolatedPoint`, `AllMissing`, `Flat`; follow `EmptyState.stories.tsx` for the meta shape)
- Modify: `packages/shared/src/index.ts` (export the component and its prop/point types next to `EmptyState`)
- Test: `app/src/__tests__/time-series-chart.test.tsx` (shared components are tested from the app, importing `@stac-higher/shared`)

**Interfaces — Produces:**
```ts
export interface TimeSeriesPoint { t: number; value: number | null } // t = epoch ms
export interface TimeSeriesChartProps {
  points: TimeSeriesPoint[];          // any order; sorted by t inside
  ariaLabel: string;
  unit?: string;                      // shown on the y labels, e.g. "K"
  width?: number;                     // viewBox width, default 480 (the svg is width 100%)
  height?: number;                    // default 160
  formatTime?: (ms: number) => string; // default "HH:MM UTC"
  emptyMessage?: string;              // default "No values at this point"
}
export function TimeSeriesChart(props: TimeSeriesChartProps): JSX.Element;
```

- [ ] **Step 1: Write the failing tests**
  - Values `[1, 2, null, 4, 5]` → two `[data-testid="series-segment"]` paths and no `[data-testid="series-point"]`.
  - `[1, null, 3, 4]` → one segment and one point (an isolated value is a circle, never dropped).
  - All null → no `svg` paths and the text `No values at this point`.
  - All values equal (`[250, 250, 250]`) → one segment whose `d` attribute contains no `NaN` (no zero-height divide).
  - `role="img"` with the given `aria-label`. The y labels show min and max with the unit (`250.0 K`). The x labels show the first and last time.
- [ ] **Step 2:** run. Expect FAIL.
- [ ] **Step 3: Implement.** Segments are runs of consecutive non-null points after sorting. Colours use `currentColor` / Tailwind `text-primary` and `text-muted-foreground`, so both themes work. Values are formatted with 1 decimal.
- [ ] **Step 4:** run. Expect PASS. Then `npm run build-storybook` in `packages/shared/` succeeds.
- [ ] **Step 5: Commit** `#94: TimeSeriesChart (plain SVG, nulls as gaps) with stories`.

### Task A4: The cube branch in the Preview tab

**Files:**
- Create: `app/src/components/collections/CubePreview.tsx`
- Modify: `app/src/components/collections/CollectionPreviewTab.tsx`: the exported component becomes a switch. `findCubeAsset(collection)` non-null → `<CubePreview collection collectionId cube={ref} />`. Otherwise the current body, moved unchanged into a file-local `ItemPreview`, so its hooks never run for a cube.
- Modify: `app/src/components/collections/CollectionDetail.tsx` (`previewAvailable`, Decision 2)
- Create: `app/src/lib/serving/cube-retry.ts`
- Test: `app/src/__tests__/collection-preview-cube.test.tsx`, `app/src/__tests__/cube-retry.test.ts`. The existing `collection-preview-tab.test.tsx` must pass unmodified.

**Interfaces:**
- Consumes: A1 `cubeTileUrlTemplate`, `CUBE_DEFAULT_STYLE`. A2 `findCubeAsset`, `cubeVariable`, `buildCubeFrames`, `CUBE_DEFAULT_FRAME_COUNT`, `useCubePosition`. A3 `TimeSeriesChart`. Existing `RasterFrameStack`, `TimeSlider`, `StacMap` (`onClick`, `onMapRef`), `FRAME_MAX_WAIT_TICKS`, `bboxToLngLatBounds`.
- Produces (`cube-retry.ts`):
```ts
export const CUBE_TILE_RETRY_MS = 5000;
export const CUBE_TILE_MAX_RETRIES = 3;
/** null when `t` has used its retries. */
export function nextRetry(attempts: Readonly<Record<string, number>>, t: string): Record<string, number> | null;
/** The frame index of a RasterFrameStack source id `${stackId}-frame-${n}`, else null. */
export function frameIndexFromSourceId(stackId: string, sourceId: string | undefined): number | null;
```

`CubePreview` behaviour:
- `RasterFrameStack id="cube"`. The frames are `buildCubeFrames(cube.timeValues, showAll ? undefined : CUBE_DEFAULT_FRAME_COUNT)` mapped to `{ key: \`${f.t}#${attempts[f.t] ?? 0}\`, tiles: [cubeTileUrlTemplate(collectionId, variable, f.t)] }`. `bounds` is the collection bbox, `minzoom={0}`, `maxzoom={CUBE_PREVIEW_MAXZOOM}` (= 7, a file constant: ABI C13 is 2 km, and maplibre over-zooms past it).
- The index works as in `ItemPreview` (null = newest), reset when `showAll` changes. `TimeSlider` takes the frame labels, `canAdvance` and `FRAME_MAX_WAIT_TICKS`. Its children: a `Switch` labelled `Show full window ({timeValues.length} steps)`, shown only when there are more than 24 steps.
- Progress (Decision 3): `Loaded {n} of {N} frames` in an `aria-live="polite"` line, hidden once n === N.
- Retry (Decision 4): on the map's `error` event, `frameIndexFromSourceId("cube", e.sourceId)`, then `e.error?.status === 422` → `setTimeout(CUBE_TILE_RETRY_MS)` → `nextRetry`. Clear the timers on unmount.
- Click: `StacMap onClick` → `setPoint({ lon, lat })` → `useCubePosition`. Below the map: a skeleton while loading; `TimeSeriesChart` with `unit="K"` when `variable === "CMI"` (else no unit) and an aria label like `CMI at 38.5000, -97.1235`. When `missingSteps > 0`: `{missingSteps} of {points.length} steps unavailable: the source file is missing or unreachable.` On a fetch error: `Time series unavailable: {message}`. Before any click: `Click the map for a time series at that point.`
- Empty: `timeValues` is empty → `EmptyState` titled `The cube has no steps yet`, description `The first append publishes the time axis; check the Settings tab's Virtual cube card.`
- Footer: `Frames rendered by the cube server from the virtual <code>{cube.key}</code> asset; no source bytes are copied.`

- [ ] **Step 1: Write the failing tests** (`cube-retry.test.ts`): `nextRetry({}, "a")` → `{a: 1}`; `nextRetry({a: 3}, "a")` → `null`; `frameIndexFromSourceId("cube", "cube-frame-12")` → `12`; `"other-frame-1"` / `undefined` → `null`.
- [ ] **Step 2: Write the failing component tests** (`collection-preview-cube.test.tsx`; copy the `react-map-gl/maplibre` mock from `collection-preview-tab.test.tsx`, and extend the default `Map` mock to forward `onClick` to a test button):
  - A cube collection with 30 time values and `servingEnabled: false` renders the map. `useItems` and `useItemTileJson` are never called. The slider shows 24 labels, ending at the newest.
  - The mounted source's `tiles[0]` contains `t=` + the newest value's exact `encodeURIComponent` and `/{z}/{y}/{x}?`.
  - Toggling `Show full window (30 steps)` → 30 labels.
  - `time_values: []` → `The cube has no steps yet`.
  - A click with a mocked `fetch` (CSV with one empty value, header `x-cube-missing-steps: 1`) → the chart's `role="img"` and `1 of 2 steps unavailable`.
  - A collection without a cube asset still takes the item path (`useItems` called).
- [ ] **Step 3:** run. Expect FAIL.
- [ ] **Step 4: Implement** `cube-retry.ts`, `CubePreview.tsx`, the switch and `previewAvailable`.
- [ ] **Step 5:** run the new tests plus `collection-preview-tab.test.tsx` and `collection-preview-frames.test.ts`. Expect PASS.
- [ ] **Step 6:** `npm run verify` from the worktree root, and `npm run build-storybook` in `packages/shared/`. Both pass.
- [ ] **Step 7: Docs.** `docs/FEATURES.md`: a Z-8 row (cube Preview + EDR chart). `docs/serving.md` cube-server section: one paragraph on the Preview's style constant, the 422 retry and the variable fallback. Then the follow-up issue for Decision 1 (`gh issue create`, label `queue: virtual cube (Z)`; title `Cube collection: write cube:variables so the Preview needn't guess the variable (Z-5 follow-up)`).
- [ ] **Step 8: Commit** `#94: cube branch in the Preview tab: frames, preload progress, 422 retry, EDR chart`.

---

## Part B — #168, Virtual cube sink card (`feat/z8b-sink-card`)

### Task B1: Client + hooks

**Files:**
- Create: `app/src/lib/cubes/queries.ts`
- Modify: `app/src/lib/query/keys.ts` (append a `cubeSinkKeys` block at the end of the file)
- Test: `app/src/__tests__/cube-sink-queries.test.tsx`

**Interfaces — Produces:**
```ts
// keys.ts
export const cubeSinkKeys = {
  all: () => ["cube-sink"] as const,
  detail: (collectionId: string) => [...cubeSinkKeys.all(), collectionId] as const,
  referenceSources: () => [...cubeSinkKeys.all(), "reference-sources"] as const,
};
// queries.ts (server types imported type-only from ./storage and ./schemas)
export interface CubeSinkView { sink: ApiCubeSink; ledger: CubeLedgerSummary }
export interface ReferenceSourceOption extends ReferenceSourceCollection { refusal: CubeSourceRefusalCode | null }
export type CubeSinkPutBody = z.input<typeof cubeSinkPutSchema>;
export class CubeSinkApiError extends Error { status: number; code?: string; details?: { path: (string | number)[]; message: string }[] }
export function useCubeSink(collectionId: string): UseQueryResult<CubeSinkView | null>;   // 404 → null; refetchInterval 15_000 while !last_snapshot_id, else 60_000
export function useReferenceSources(enabled: boolean): UseQueryResult<ReferenceSourceOption[]>;
export function usePutCubeSink(collectionId: string): UseMutationResult<{ sink: ApiCubeSink }, CubeSinkApiError, CubeSinkPutBody>;
export function usePatchCubeSink(collectionId: string): UseMutationResult<{ sink: ApiCubeSink }, CubeSinkApiError, { enabled: boolean }>;
```
Both mutations invalidate `cubeSinkKeys.detail(collectionId)` when they settle, error or success, so a 409 reloads.

- [ ] **Step 1: Write the failing tests** (mocked `fetch`, wrapped in a `QueryClientProvider`): GET 404 → `data === null`; GET 200 → the view; PUT 422 `{error, code: "unsupported_source_protocol"}` → `CubeSinkApiError` with `status 422` and that `code`; PUT 400 `{error, details: [{path: ["config","window","max_steps"], message}]}` → `details` kept; PATCH sends `{"enabled":false}` with method `PATCH`.
- [ ] **Step 2:** run. Expect FAIL. **Step 3:** implement (the `settings-client.ts` fetch pattern). **Step 4:** run. Expect PASS.
- [ ] **Step 5: Commit** `#168: cube sink client, query keys and hooks`.

### Task B2: Form model

**Files:**
- Create: `app/src/lib/cubes/form.ts`
- Test: `app/src/__tests__/cube-sink-form.test.ts`

**Interfaces — Produces:**
```ts
export const CUBE_FORM_DEFAULTS: CubeSinkFormValues; // Decision 6
export interface CubeSinkFormValues {
  source_collection_id: string; append_dim: string;
  variables: string; loadable_variables: string; // comma-separated
  max_steps: string; max_age: string;             // "" = unset
}
export const cubeSinkFormSchema: z.ZodType<CubeSinkFormValues>; // field-level messages; validates by building the PUT body and running cubeSinkPutSchema, mapping its issue paths back to form fields
export function sinkToForm(sink: ApiCubeSink): CubeSinkFormValues;
export function formToPutBody(values: CubeSinkFormValues, enabled: boolean, existing?: CubeSinkConfig): CubeSinkPutBody; // asset_key/on_late/parser preserved from `existing` when given
export function apiIssuesToFormErrors(details: CubeSinkApiError["details"]): Partial<Record<keyof CubeSinkFormValues | "root", string>>;
```
Path map: `["source_collection_id"]` → `source_collection_id`; `["config","append_dim"]` → `append_dim`; `["config","variables",…]` → `variables`; `["config","loadable_variables",…]` → `loadable_variables`; `["config","window","max_steps"]` → `max_steps`; `["config","window","max_age"]` → `max_age`; `["config","window"]` (the "needs one" refine) → `max_steps`; anything else → `root`.

- [ ] **Step 1: Write the failing tests:**
  - `formToPutBody(CUBE_FORM_DEFAULTS with source "src", true)` deep-equals `{ source_collection_id: "src", enabled: true, config: { parser: "hdf5", append_dim: "t", variables: ["CMI","DQF"], loadable_variables: ["t","x","y","goes_imager_projection"], asset_key: "cube", window: { max_steps: 288, max_age: "24h" }, on_late: "skip" } }`.
  - Both window fields `""` → no `window` key (no trim, no GC).
  - `max_steps` `"0"` → a schema error on `max_steps`. `max_age` `"31d"` → an error on `max_age`. `loadable_variables` without the append dim → an error on `loadable_variables`. ` CMI , ,DQF ` → `["CMI","DQF"]`.
  - `sinkToForm` round-trips a stored config. `apiIssuesToFormErrors` maps every path above.
- [ ] **Step 2:** run. Expect FAIL. **Step 3:** implement. **Step 4:** run. Expect PASS.
- [ ] **Step 5: Commit** `#168: cube sink form model mapped onto the PUT schema`.

### Task B3: `CubeSinkCard` + Settings mount

**Files:**
- Create: `app/src/components/collections/CubeSinkCard.tsx` (form + toggle)
- Create: `app/src/components/collections/CubeSinkStatus.tsx` (read-only status block)
- Modify: `app/src/components/collections/SettingsTab.tsx` (render `<CubeSinkCard collectionId canAct={canAct} />` after the settings card; wrap both in a fragment or `space-y-6`)
- Test: `app/src/__tests__/cube-sink-card.test.tsx`

**Interfaces — Consumes:** B1 hooks, B2 form model, `cubeSinkHealth`, `CUBE_SINK_HEALTH_LABEL`, `CUBE_MAINTENANCE_ATTENTION_LABEL`, `CUBE_MAINTENANCE_STATUS_LABEL`, `CUBE_PUBLISH_OUTCOME_LABEL`, `CUBE_APPEND_STATUS_LABEL`, `cubeLedgerReasonLabel` (`@/lib/cubes/status`), `formatBytes` and `timeAgo` (`@/components/monitoring/shared`).

Behaviour:
- **Card states.** `useCubeSink` loading → skeleton. No sink with `canAct` → a short explainer ("Keep a rolling window of the source's files as virtual references in an Icechunk cube, served as map tiles and point time series. No source bytes are copied.") and a `Set up a virtual cube` button that opens the form. No sink without `canAct` → render nothing. A sink exists → `CubeSinkStatus`, then the form (if `canAct`).
- **Source picker** (`Select`): `useReferenceSources(canAct)`, the current collection excluded. A refused option is disabled, and its label carries the reason: `no_reference_ingest` "no reference-mode ingest", `unsupported_source_protocol` "not an S3 source", `signed_source_unsupported` "signed (private) source". No eligible option → the hint `No eligible source: add a reference-mode ingest association on an anonymous S3 connection first.`
- **Locked** (`sink.last_snapshot_id`): source, `append_dim`, `variables` and `loadable_variables` disabled, with the note `Locked: the cube repository exists. Only the window can change.`
- **First append pending** (`!last_snapshot_id && ledger.counts.pending > 0`): Save and the `Switch` disabled, with the note `The first append is running. Changes wait until it commits: an edit now would make the pipeline discard and redo it.`
- **Save** → `formToPutBody(values, sink?.enabled ?? true, sink?.config)` → PUT. 400 → `apiIssuesToFormErrors` → `setError` per field. 422 with a source `code` → the message under the picker. Other 422 (`cube_collection_not_found`, …) and 409 `reserved_item_id` → a form-level alert with the server's `error`. 409 `cube_layout_locked` / `cube_sink_conflict` → a form-level alert `The sink changed since this page loaded. It has been reloaded; review and save again.` (the mutation invalidates, so the view refetches), and the form re-seeds from the refetched sink. Success → toast `Virtual cube saved`.
- **Enable switch** (only when a sink exists) → PATCH `{enabled}`. A 422 (re-enable with a now-ineligible source) or 409 → an inline alert with the server's `error`.
- **`CubeSinkStatus`:**
  - Badge `CUBE_SINK_HEALTH_LABEL[health.state]` with `data-state={health.state}`.
  - The health reasons as a list, labelled: the maintenance attention labels, plus a local map for `last_error` "Last append failed", `maintenance_failed` "Last maintenance failed", `failed_rows` "Recent ledger rows failed" and `no_append_within_window` "Nothing appended within the window's max age". Unknown reasons are shown verbatim.
  - The source collection (link `/collections/{id}`) and the ledger counts for all four statuses.
  - `last_error` in a `<pre>`.
  - The last snapshot: the first 12 characters of the id, plus `timeAgo(last_appended_at)`.
  - The last maintenance: the status label, `timeAgo(finished_at ?? started_at)`, the publish outcome label, `gc.bytes_deleted` via `formatBytes`, and `total_bytes` of `warn_bytes` (or `Size readout pending` when `total_bytes` is absent).
  - Up to 10 `recent` rows: item id, status label, `cubeLedgerReasonLabel`, `timeAgo(updated_at)`.

- [ ] **Step 1: Write the failing tests** (mock `@/lib/cubes/queries`' hooks, the way `settings-tab.test.tsx` mocks its hooks):
  1. No sink, operator → `Set up a virtual cube` → choose a source, Save → `put.mutate` called with the B2 default body plus that source and `enabled: true`.
  2. A put error 422 `unsupported_source_protocol` → its message under the source picker.
  3. A put error 400 with details `["config","window","max_steps"]` → the message next to the max-steps input.
  4. Locked sink (`last_snapshot_id: "ABC…"`, `enabled: false`) → the source/variables/append-dim inputs are disabled, max steps is editable, Save sends `enabled: false`.
  5. First append pending (`last_snapshot_id: null`, `counts.pending: 3`) → Save and the switch are disabled and the note is shown. With `pending: 0` and no snapshot → enabled.
  6. One case per `CUBE_SINK_HEALTH_STATES` value (build sinks that make `cubeSinkHealth` return it) → the badge text equals the label, and `data-state` matches.
  7. A viewer without operator: no sink → nothing rendered; a sink → status shown, no Save button, no switch.
  8. Switch → `patch.mutate({enabled: false})`. A patch error 409 → the alert text.
  9. Client validation: max steps `0` → an error shown and `put.mutate` not called.
  10. 409 `cube_layout_locked` → the reload alert.
- [ ] **Step 2:** run. Expect FAIL. **Step 3:** implement both components and the mount. **Step 4:** run the new tests plus `settings-tab.test.tsx`. Expect PASS (mock the new hooks there if the mount breaks it).
- [ ] **Step 5:** `npm run verify` from the worktree root. Expect PASS.
- [ ] **Step 6: Docs.** `docs/FEATURES.md`: a Z-8b row (the sink card). `docs/backend.md`: nothing (the routes exist).
- [ ] **Step 7: Commit** `#168: Virtual cube sink card on the Settings tab`.

## PRs

- `#94` PR title `Z-8: cube Preview: animated virtual-cube frames and the EDR time-series chart`. `#168` PR title `Z-8b: Virtual cube sink card on the Settings tab`. Each body starts `Closes #<n>` and lists the gates and their counts. Lead-only steps: a browser check against the stack (`docker compose --profile cube up -d`, a sink with ≥ 1 commit) and e2e if a spec is added. Deviations: Decisions 1–6.
