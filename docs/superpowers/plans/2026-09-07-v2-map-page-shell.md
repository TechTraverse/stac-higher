# V-2 · Map Page Shell, State and Footprint Layers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/map` exists as a real page: the built-in catalog's products can be added as footprint layers, reordered, dimmed, hidden and removed, with hover tooltips and click-through to the item page — and the shared layer components carry the two amendment props (`visible`, the stack anchor) the later slices need.

**Architecture:** A `useReducer` in the island (`app/src/lib/map/state.ts`, the repo's first) owns an ordered list of layers; the panel renders it topmost-first and the map renders it topmost-first too, because react-map-gl creates layers *during render* in tree order and a `beforeId` naming a layer that does not exist yet is a silently dropped `addLayer`. Chaining therefore only ever targets ids that are stable for the life of a layer: a footprints layer's fill id, and — for the imagery layers V-3 adds — a new always-mounted, zero-opacity `background` layer per raster stack. Each footprints layer owns its own `useItems` query in a small child component, so hooks stay unconditional while the list is dynamic.

**Tech Stack:** React 19 (`useReducer`, no nanostore — spec §4.2), Astro 7 page shell, `react-map-gl/maplibre` 8.1.1, MapLibre GL 5, TanStack Query, radix Popover/Slider via the app's and the shared package's shadcn primitives, lucide-react icons, vitest + Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-04-map-page-design.md` — §4.1, §4.2, §4.3 (footprints half), §4.5 (layer list + Products/Footprints only), §4.6, §4.7, §6 (the V-2 rows), §9, §11 (§11.1 and §11.2 are built first here; §11.3 belongs to V-3).

## Global Constraints

- Worktree off `ai/main`: `git worktree add .claude/worktrees/v2-map-page -b ai/v2-map-page ai/main`, then `npm install` at the **worktree root** (single lockfile, workspace symlinks).
- Gate: `npm run verify` from the worktree root. One test file: `cd app && npx vitest run src/__tests__/<file>`.
- **Teammates never run e2e, the dev server or Docker.** The lead runs `npm run test:e2e:ci -- map` once on `ai/main` after merging (needs Docker: pgstac-backed STAC API on :8082 and the built-in catalog on :8081).
- Shared components live in `packages/shared/src/components/map/` and are exported from `packages/shared/src/index.ts`; app code imports them from `@stac-higher/shared`, **never** by relative path. Inside the shared package use `@shared/*`.
- **Never edit `packages/shared/src/components/ui/*` or `app/src/components/ui/*`** — a PreToolUse hook blocks it. Use `npx shadcn@latest add <component>` if a primitive is genuinely missing (none is: `Popover*` is already in `app/src/components/ui/popover.tsx`, `Slider`/`Button`/`Badge`/`EmptyState`/`Tooltip*`/`Select*`/`Switch` are exported from `@stac-higher/shared`).
- The `astro check --minimumSeverity error` PostToolUse hook runs after every `.ts`/`.tsx`/`.astro` edit. Fix what it reports before moving on.
- **No new dependencies.** No drag-and-drop library (spec §9: up/down buttons). lucide-react icons only.
- Existing default ids stay exactly as they are: `stac-footprints`, `stac-footprint-fill`, `stac-footprint-line`, `stac-raster-preview`, `stac-raster-preview-layer`, and the preview's `preview-frame-{n}` source ids.
- **The four preview test files must stay green**: `app/src/__tests__/collection-preview-tab.test.tsx`, `collection-preview-frames.test.ts`, `raster-tile-layer.test.tsx`, `time-slider.test.tsx`. They are not edited by this plan. `raster-frame-stack.test.tsx` **may** change, but only where the new anchor layer makes an assertion see one more layer — with that reason written into the commit message.
- Opacity is clamped **once**, by the layer row, before the value reaches `footprintLayers` / `vectorTileLayers` / `RasterFrameStack`. The layer components stay non-clamping — that is settled (V-1 final review); do not add clamps inside them.
- `app/src/lib/map/styles.ts` and `app/src/lib/map/bbox.ts` are thin re-export proxies of the shared package. The **new** `app/src/lib/map/state.ts` is a real module, app-only (nothing in the shared package needs it).
- Every commit message ends with the executing session's attribution trailer — fill in the session URL of the session that actually runs this plan:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <this session's claude.ai/code URL>
  ```

---

### Task 1: `clamp01` in the shared map styles

The single opacity clamp the V-1 review asked for. maplibre rejects an opacity above 1 as a style error, and every layer component takes the caller's number unchanged, so exactly one function has to own the clamp — here, next to the specs that consume opacity.

**Files:**
- Modify: `packages/shared/src/lib/map/styles.ts` (append after `footprintLayers`, i.e. after line 68)
- Modify: `packages/shared/src/index.ts:41-55` (the styles export block)
- Test: `app/src/__tests__/map-styles.test.ts` (append a describe block; the file ends at line 51)

**Interfaces:**
- Produces:
  ```ts
  export function clamp01(value: number): number;
  ```
  Clamps into `[0, 1]`; a non-finite input (`NaN`, `Infinity`) returns `0`.

- [ ] **Step 1: Write the failing test**

Append to `app/src/__tests__/map-styles.test.ts`:

```ts
describe("clamp01", () => {
  it("passes values already inside the range through untouched", () => {
    expect(clamp01(0)).toBe(0);
    expect(clamp01(0.42)).toBe(0.42);
    expect(clamp01(1)).toBe(1);
  });

  it("clamps out-of-range values to the ends", () => {
    // maplibre treats an opacity above 1 as a style ERROR, not a saturation —
    // one bad slider value would take the whole layer off the map.
    expect(clamp01(1.5)).toBe(1);
    expect(clamp01(-0.2)).toBe(0);
    expect(clamp01(Number.POSITIVE_INFINITY)).toBe(0);
  });

  it("treats a non-numeric value as fully transparent", () => {
    // NaN reaches a paint property as "invalid"; 0 is the safe reading.
    expect(clamp01(Number.NaN)).toBe(0);
  });
});
```

Extend that file's import to include the new symbol:

```ts
import {
  FOOTPRINT_SOURCE,
  clamp01,
  footprintFillLayer,
  footprintLineLayer,
  footprintLayerIds,
  footprintLayers,
} from "@stac-higher/shared";
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-styles.test.ts`
Expected: FAIL — `clamp01` is not exported from `@stac-higher/shared`.

- [ ] **Step 3: Implement `clamp01`**

Append to `packages/shared/src/lib/map/styles.ts` (after `footprintLayers`, before `footprintFillLayer`):

```ts
/**
 * The ONE opacity clamp. Every layer spec here and every map layer component
 * takes the caller's opacity unchanged — deliberately, so a caller can see
 * exactly what it asked for — and maplibre rejects a value outside 0..1 as a
 * style error rather than saturating it. The /map page's opacity control
 * clamps here before the value reaches any layer.
 *
 * A non-finite value (a slider that produced NaN) reads as fully transparent:
 * "invisible" is recoverable, "invalid paint property" is not.
 */
export function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}
```

Add it to the styles export block in `packages/shared/src/index.ts` (line 41-55), after `EXTENT_SOURCE`:

```ts
export {
  FOOTPRINT_SOURCE,
  EXTENT_SOURCE,
  clamp01,
  footprintFillLayer,
  footprintLineLayer,
  footprintLayerIds,
  footprintLayers,
  extentFillLayer,
  extentLineLayer,
  selectedFillLayer,
  selectedLineLayer,
  RASTER_PREVIEW_SOURCE,
  RASTER_PREVIEW_LAYER,
  vectorTileLayers,
} from "@shared/lib/map/styles";
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/map-styles.test.ts`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/shared/src/lib/map/styles.ts packages/shared/src/index.ts app/src/__tests__/map-styles.test.ts
git commit -m "feat(map): single clamp01 opacity clamp in the shared map styles (V-2, spec §11)"
```

---

### Task 2: `RasterFrameStack` anchor layer (spec §11.2)

A raster stack's frame layer ids (`${id}-frame-${n}-layer`) come and go on every step, so nothing may chain a `beforeId` to them: react-map-gl calls `map.addLayer(options, beforeId)` during render, and maplibre **fires an error event and no-ops** when `beforeId` names a layer that is not in the style. The stack therefore mounts one layer that never unmounts and never changes id.

The spec says "empty-tile layer"; this implements it as a maplibre **`background` layer with `background-opacity: 0`** — a background layer needs no source and fetches nothing, so it costs one draw of nothing instead of a tile source that would sit in the style asking for empty tiles. Imagery layers themselves are V-3; the anchor is built now because `MapPage`'s chaining code is written now and the amendment is breaking once written against.

**Files:**
- Modify: `packages/shared/src/components/map/RasterFrameStack.tsx:1-2` (imports) and `:69-112` (body/return)
- Modify: `packages/shared/src/index.ts:96` (export the id helper)
- Test: `app/src/__tests__/raster-frame-stack.test.tsx:15` (import), `:28-38` (the `layers()` helper), `:102-105` (the empty-series test)

**Interfaces:**
- Produces:
  ```ts
  export function rasterFrameStackAnchorId(id: string): string; // `${id}-anchor`
  ```
  `<RasterFrameStack id="s" …>` always renders a `background` layer with `id="s-anchor"`, `paint: { "background-opacity": 0 }` and the stack's own `beforeId`, as the FIRST child — including when `frames` is empty. Frame layers keep the caller's `beforeId` unchanged, and because the anchor is added first they end up above it.

- [ ] **Step 1: Write the failing tests**

In `app/src/__tests__/raster-frame-stack.test.tsx`, extend the import on line 15:

```ts
import {
  RasterFrameStack,
  RASTER_FRAME_LOOKAHEAD,
  rasterFrameStackAnchorId,
} from "@stac-higher/shared";
```

Replace the `layers()` helper (lines 28-38) with these two:

```ts
function allLayers(): Record<string, unknown>[] {
  return screen.queryAllByTestId("layer").map((el) => JSON.parse(el.dataset.props as string));
}

/**
 * The FRAME layers only. The stack also mounts a zero-opacity background
 * layer as the stable `beforeId` anchor (spec §11.2) — not a frame, no
 * source, no raster paint.
 */
function layers(): Rendered[] {
  return allLayers()
    .filter((props) => props.type === "raster")
    .map((props) => {
      const paint = props.paint as Record<string, unknown>;
      return {
        source: props.source as string,
        opacity: paint["raster-opacity"] as number,
        transition: paint["raster-opacity-transition"],
      };
    });
}
```

Replace the "renders nothing for an empty series" test (lines 102-105) with:

```ts
  it("keeps the anchor mounted for an empty series, but no sources", () => {
    // The anchor is what a layer ABOVE this one chains its beforeId to. If it
    // came and went with the data, that chaining would silently break every
    // time a product had no frames yet.
    render(<RasterFrameStack id="s" frames={[]} index={0} />);

    expect(allLayers().map((l) => l.id)).toEqual(["s-anchor"]);
    expect(screen.queryAllByTestId("source")).toHaveLength(0);
  });

  it("mounts the anchor first, beneath the frames, at zero opacity", () => {
    render(<RasterFrameStack id="s" frames={FRAMES} index={0} beforeId="top" />);

    expect(rasterFrameStackAnchorId("s")).toBe("s-anchor");
    const [anchor, ...rest] = allLayers();
    expect(anchor).toMatchObject({
      id: "s-anchor",
      type: "background",
      beforeId: "top",
      paint: { "background-opacity": 0 },
    });
    // A background layer takes no source — that is why it can never 404 or
    // sit in the style waiting for empty tiles.
    expect(anchor).not.toHaveProperty("source");
    // Frames keep the CALLER's beforeId: both they and the anchor insert
    // before "top", and the anchor got there first, so it stays lowest.
    expect(rest.every((l) => l.beforeId === "top")).toBe(true);
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/raster-frame-stack.test.tsx`
Expected: FAIL — `rasterFrameStackAnchorId` is not exported, and no background layer is rendered.

- [ ] **Step 3: Implement the anchor**

`packages/shared/src/components/map/RasterFrameStack.tsx` — change the imports (lines 1-2) to:

```tsx
import { useRef } from "react";
import { Layer } from "react-map-gl/maplibre";
import { RasterTileLayer } from "./RasterTileLayer";
```

Add the id helper directly above the `RASTER_FRAME_LOOKAHEAD` constant:

```tsx
/**
 * The stack's stable `beforeId` target (spec §11.2). Frame layer ids
 * (`${id}-frame-${n}-layer`) are mounted and unmounted on every step, and
 * maplibre no-ops an `addLayer` whose `beforeId` names a layer that is not
 * in the style — so a layer drawn above a stack chains to THIS id, never to
 * a frame.
 */
export function rasterFrameStackAnchorId(id: string): string {
  return `${id}-anchor`;
}
```

Replace the body from `const count = frames.length;` (line 69) to the end of the component with:

```tsx
  // Always mounted, even for an empty series: a chaining target that appears
  // only once data arrives is not a chaining target. A `background` layer
  // needs no source and fetches nothing — at zero opacity it paints nothing
  // and costs one no-op draw, where an "empty tile" raster source would sit
  // in the style making requests.
  const anchor = (
    <Layer
      id={rasterFrameStackAnchorId(id)}
      type="background"
      beforeId={beforeId}
      paint={{ "background-opacity": 0 }}
    />
  );

  const count = frames.length;
  if (count === 0) return anchor;

  // A non-finite index shows the first frame rather than nothing.
  const safeIndex = Number.isFinite(index) ? index : 0;

  // Normalised into the series so an index from a caller's own arithmetic
  // (a shared time axis, a series that shrank under a stale ref) can never
  // reach past the array: previous and current are both taken modulo the
  // series, which also keeps the previous frame painted when the series
  // shrinks below the index it remembers.
  const current = ((safeIndex % count) + count) % count;
  const previous = ((previousIndex.current % count) + count) % count;

  // Draw order, bottom to top: previous, current, then the lookahead frames
  // loading invisibly. Deduped — a series shorter than the window would
  // otherwise repeat itself.
  const mounted = [
    ...new Set([
      previous,
      ...Array.from(
        { length: RASTER_FRAME_LOOKAHEAD + 1 },
        (_, offset) => (current + offset) % count,
      ),
    ]),
  ];

  return (
    <>
      {anchor}
      {mounted.map((frameIndex) => (
        <RasterTileLayer
          key={frames[frameIndex].key}
          id={`${id}-frame-${frameIndex}`}
          tiles={frames[frameIndex].tiles}
          bounds={bounds}
          minzoom={minzoom}
          maxzoom={maxzoom}
          opacity={frameIndex === current || frameIndex === previous ? opacity : 0}
          opacityTransitionMs={0}
          beforeId={beforeId}
        />
      ))}
    </>
  );
}
```

Export the helper from `packages/shared/src/index.ts` (line 96):

```ts
export {
  RasterFrameStack,
  RASTER_FRAME_LOOKAHEAD,
  rasterFrameStackAnchorId,
} from "@shared/components/map/RasterFrameStack";
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/raster-frame-stack.test.tsx`
Expected: PASS (10 tests).

- [ ] **Step 5: Prove the preview tests are untouched by the extra layer**

Run: `cd app && npx vitest run src/__tests__/collection-preview-tab.test.tsx src/__tests__/collection-preview-frames.test.ts src/__tests__/raster-tile-layer.test.tsx src/__tests__/time-slider.test.tsx`
Expected: PASS, with no edits to those files.

Why they survive: `collection-preview-tab.test.tsx`'s `layers()` helper reads `paint["raster-opacity"]` off every `data-testid="layer"` node, so the anchor arrives as `{ source: undefined, opacity: undefined }`. Every assertion there filters on `opacity === 1` or `opacity === 0`, or uses `visibleTiles()`'s `findLast((l) => l.opacity === 1)` — an `undefined` entry matches none of them. Its source assertions read `queryAllByTestId("source")`, and the anchor has no source. The two "no layers at all" assertions (`hintSettled === false`, and the not-previewable empty state) hold because the tab does not mount the stack in either case.

- [ ] **Step 6: Commit**

```bash
git add packages/shared/src/components/map/RasterFrameStack.tsx packages/shared/src/index.ts app/src/__tests__/raster-frame-stack.test.tsx
git commit -m "feat(map): stable ${id}-anchor layer on RasterFrameStack (V-2, spec §11.2)

The stack's frame layer ids come and go on every step and maplibre no-ops an
addLayer whose beforeId is missing, so a layer drawn above a stack needs a
target that never unmounts. Implemented as a zero-opacity background layer:
no source, no tile requests.

raster-frame-stack.test.tsx changes only because its layers() helper saw
EVERY rendered layer; it now filters the frame (raster) layers and asserts
the anchor explicitly. The four preview test files are unchanged and green."
```

---

### Task 3: `visible` on the three layer components (spec §11.1)

The panel's visibility toggle must not unmount a layer: unmounting drops the maplibre source, and for the imagery layers V-3 adds that means re-fetching up to three raster frames from the tiler on every toggle (spec §5's contention note). `layout: { visibility: "none" }` keeps the source mounted and warm.

The layout object is always passed explicitly (`"visible"` or `"none"`) rather than omitted when visible: react-map-gl diffs `layout` by reference and then per key, and always sending the key avoids relying on its "key removed → reset to default" path.

The anchor from Task 2 deliberately takes no `visible`: it is invisible in both states and exists only as a chaining target.

**Files:**
- Modify: `packages/shared/src/components/map/FootprintLayer.tsx:6-62`
- Modify: `packages/shared/src/components/map/RasterTileLayer.tsx:7-77`
- Modify: `packages/shared/src/components/map/RasterFrameStack.tsx` (props + threading)
- Modify: `packages/shared/src/components/map/VectorTileLayer.tsx:4-41`
- Test: `app/src/__tests__/footprint-layer.test.tsx` (append), `app/src/__tests__/raster-frame-stack.test.tsx` (append), `app/src/__tests__/vector-tile-layer.test.tsx` (append, file ends at line 65)

**Interfaces:**
- Consumes: `rasterFrameStackAnchorId` (Task 2) — only to know the anchor is exempt.
- Produces: `visible?: boolean` (default `true`) on `FootprintLayerProps`, `RasterTileLayerProps`, `RasterFrameStackProps` and `VectorTileLayerProps`. When `false`, **every layer the component owns** gets `layout: { visibility: "none" }`; when `true`, `layout: { visibility: "visible" }`. The component never unmounts on a toggle.

- [ ] **Step 1: Write the failing tests**

Append to `app/src/__tests__/footprint-layer.test.tsx` (inside the existing `describe`):

```tsx
  it("hides both layers without unmounting the source", () => {
    // Unmounting would drop the GeoJSON source and re-diff the whole feature
    // collection on every toggle; visibility is a layout property.
    const { rerender } = render(<FootprintLayer id="layer-a" items={[item("a")]} />);
    expect(layers().map((l) => l.layout)).toEqual([
      { visibility: "visible" },
      { visibility: "visible" },
    ]);

    rerender(<FootprintLayer id="layer-a" items={[item("a")]} visible={false} />);

    expect(sources().map((s) => s.id)).toEqual(["layer-a"]);
    expect(layers().map((l) => l.layout)).toEqual([
      { visibility: "none" },
      { visibility: "none" },
    ]);
  });
```

Append to `app/src/__tests__/raster-frame-stack.test.tsx` (inside the existing `describe`):

```tsx
  it("hides every mounted frame when not visible, and leaves the anchor alone", () => {
    render(<RasterFrameStack id="s" frames={FRAMES} index={1} visible={false} />);

    const [anchor, ...frames] = allLayers();
    expect(frames.map((l) => l.layout)).toEqual([
      { visibility: "none" },
      { visibility: "none" },
    ]);
    // The anchor paints nothing in either state; hiding it would only risk
    // maplibre dropping it as a chaining target.
    expect(anchor).not.toHaveProperty("layout");
    expect(screen.queryAllByTestId("source")).toHaveLength(2);
  });
```

Append to `app/src/__tests__/vector-tile-layer.test.tsx` (inside the existing `describe`):

```tsx
  it("hides all three layers without dropping the vector source", () => {
    render(<VectorTileLayer id="v" url="http://t/tilejson.json" visible={false} />);

    expect(source()).toMatchObject({ id: "v", type: "vector" });
    expect(layers().map((l) => l.layout)).toEqual([
      { visibility: "none" },
      { visibility: "none" },
      { visibility: "none" },
    ]);
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd app && npx vitest run src/__tests__/footprint-layer.test.tsx src/__tests__/raster-frame-stack.test.tsx src/__tests__/vector-tile-layer.test.tsx`
Expected: FAIL — `visible` is not a prop; `layout` is `undefined` on every layer.

- [ ] **Step 3: Add the prop to all four components**

`packages/shared/src/components/map/FootprintLayer.tsx` — add to `FootprintLayerProps` after `opacity`:

```ts
  /**
   * Hidden layers stay MOUNTED (`visibility: "none"`): unmounting would drop
   * the source and re-diff the whole feature collection on every toggle.
   */
  visible?: boolean;
```

and change the component body:

```tsx
export function FootprintLayer({
  items,
  selectedId,
  id,
  opacity = 1,
  visible = true,
  beforeId,
}: FootprintLayerProps) {
  const sourceId = id ?? FOOTPRINT_SOURCE;
  const { fill, line } = footprintLayers(sourceId, opacity);
  const layout: { visibility: "visible" | "none" } = {
    visibility: visible ? "visible" : "none",
  };
```

and the return's two `<Layer>` elements:

```tsx
      <Layer {...fill} layout={layout} beforeId={beforeId} />
      <Layer {...line} layout={layout} beforeId={beforeId} />
```

`packages/shared/src/components/map/RasterTileLayer.tsx` — add to `RasterTileLayerProps` after `opacityTransitionMs`:

```ts
  /** Hidden frames stay mounted so their tiles are not re-fetched on a toggle. */
  visible?: boolean;
```

destructure `visible = true` and render:

```tsx
      <Layer
        id={layerId}
        type="raster"
        source={sourceId}
        beforeId={beforeId}
        layout={{ visibility: visible ? "visible" : "none" }}
        paint={{
          "raster-opacity": opacity,
          ...(opacityTransitionMs !== undefined && {
            "raster-opacity-transition": { duration: opacityTransitionMs, delay: 0 },
          }),
        }}
      />
```

`packages/shared/src/components/map/RasterFrameStack.tsx` — add to `RasterFrameStackProps` after `opacity`:

```ts
  /** Hides every mounted frame (the anchor is unaffected — it paints nothing). */
  visible?: boolean;
```

destructure `visible = true` and pass it to each frame:

```tsx
        <RasterTileLayer
          key={frames[frameIndex].key}
          id={`${id}-frame-${frameIndex}`}
          tiles={frames[frameIndex].tiles}
          bounds={bounds}
          minzoom={minzoom}
          maxzoom={maxzoom}
          opacity={frameIndex === current || frameIndex === previous ? opacity : 0}
          opacityTransitionMs={0}
          visible={visible}
          beforeId={beforeId}
        />
```

`packages/shared/src/components/map/VectorTileLayer.tsx` — add to `VectorTileLayerProps` after `opacity`:

```ts
  /** Hidden layers stay mounted; the MVT source keeps its tiles. */
  visible?: boolean;
```

and:

```tsx
export function VectorTileLayer({
  id,
  url,
  sourceLayer = "default",
  opacity = 1,
  visible = true,
  beforeId,
}: VectorTileLayerProps) {
  const { fill, line, circle } = vectorTileLayers(id, sourceLayer, opacity);
  const layout: { visibility: "visible" | "none" } = {
    visibility: visible ? "visible" : "none",
  };
  return (
    <Source id={id} type="vector" url={url}>
      <Layer {...fill} layout={layout} beforeId={beforeId} />
      <Layer {...line} layout={layout} beforeId={beforeId} />
      <Layer {...circle} layout={layout} beforeId={beforeId} />
    </Source>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/footprint-layer.test.tsx src/__tests__/raster-frame-stack.test.tsx src/__tests__/vector-tile-layer.test.tsx src/__tests__/collection-preview-tab.test.tsx src/__tests__/raster-tile-layer.test.tsx`
Expected: PASS. The preview and raster-tile assertions all use `toMatchObject`, so an extra `layout` prop does not break them.

- [ ] **Step 5: Commit**

```bash
git add packages/shared/src/components/map/ app/src/__tests__/footprint-layer.test.tsx app/src/__tests__/raster-frame-stack.test.tsx app/src/__tests__/vector-tile-layer.test.tsx
git commit -m "feat(map): visible prop on the three layer components (V-2, spec §11.1)"
```

---

### Task 4: The map page reducer (`app/src/lib/map/state.ts`)

Spec §4.2, verbatim shapes. Pure and unit-tested, and complete — `setAsset`, `setFrameSpan` and `setAxisIndex` are implemented here even though their UI lands in V-3, because the reducer is where their semantics live and a half-written reducer is a merge conflict waiting for V-3.

This is the repo's first `useReducer`, and the first **real** module under `app/src/lib/map/` (`styles.ts` and `bbox.ts` there are re-export proxies of the shared package).

**Files:**
- Create: `app/src/lib/map/state.ts`
- Test: `app/src/__tests__/map-state.test.ts` (new)

**Interfaces:**
- Consumes: `clamp01` (Task 1), `rasterFrameStackAnchorId` (Task 2), `footprintLayerIds` (V-1) — all from `@stac-higher/shared`.
- Produces:
  ```ts
  export type LayerKind = "footprints" | "imagery" | "vector";
  export interface MapLayer {
    id: string; kind: LayerKind; sourceId: string; title: string;
    asset?: string; visible: boolean; opacity: number;
  }
  export type FrameSpan = 25 | 50 | 100 | 200;
  export const FRAME_SPANS: readonly FrameSpan[];
  export const DEFAULT_FRAME_SPAN: FrameSpan;          // 50
  export interface MapState { layers: MapLayer[]; frameSpan: FrameSpan; axisIndex: number | null }
  export const INITIAL_MAP_STATE: MapState;
  export type MapAction =
    | { type: "add"; layer: MapLayer }
    | { type: "remove"; id: string }
    | { type: "move"; id: string; direction: "up" | "down" }
    | { type: "setVisible"; id: string; visible: boolean }
    | { type: "setOpacity"; id: string; opacity: number }
    | { type: "setAsset"; id: string; asset: string }
    | { type: "setFrameSpan"; frameSpan: FrameSpan }
    | { type: "setAxisIndex"; axisIndex: number | null };
  export function mapReducer(state: MapState, action: MapAction): MapState;
  export function layerAnchorId(layer: MapLayer): string;
  export function beforeIdFor(layers: MapLayer[], index: number): string | undefined;
  export function opacityFromSlider(values: number[]): number;
  ```
  `layers` is in DRAW order, bottom first. `add` refuses a duplicate `(kind, sourceId)` by returning the state unchanged. `move` with `"up"` swaps toward the END of the array (higher in the stack); at either end it is a no-op. Layer `id`s are supplied by the caller so the reducer stays pure.

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/map-state.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  DEFAULT_FRAME_SPAN,
  INITIAL_MAP_STATE,
  beforeIdFor,
  layerAnchorId,
  mapReducer,
  opacityFromSlider,
  type MapLayer,
  type MapState,
} from "@/lib/map/state";

function layer(id: string, over: Partial<MapLayer> = {}): MapLayer {
  return {
    id,
    kind: "footprints",
    sourceId: `collection-${id}`,
    title: `Collection ${id}`,
    visible: true,
    opacity: 1,
    ...over,
  };
}

function stateWith(...layers: MapLayer[]): MapState {
  return { ...INITIAL_MAP_STATE, layers };
}

describe("mapReducer", () => {
  it("starts empty, at the default span, on the newest tick", () => {
    expect(INITIAL_MAP_STATE).toEqual({
      layers: [],
      frameSpan: DEFAULT_FRAME_SPAN,
      axisIndex: null,
    });
    expect(DEFAULT_FRAME_SPAN).toBe(50);
  });

  it("adds a layer on top of the draw order", () => {
    const one = mapReducer(INITIAL_MAP_STATE, { type: "add", layer: layer("a") });
    const two = mapReducer(one, { type: "add", layer: layer("b") });

    // Bottom first: the newest layer draws above the ones already there.
    expect(two.layers.map((l) => l.id)).toEqual(["a", "b"]);
  });

  it("refuses a duplicate kind + source, and allows the same source in another kind", () => {
    const one = mapReducer(INITIAL_MAP_STATE, { type: "add", layer: layer("a") });
    const dup = mapReducer(one, {
      type: "add",
      layer: layer("b", { sourceId: "collection-a" }),
    });
    expect(dup).toBe(one);

    const imagery = mapReducer(one, {
      type: "add",
      layer: layer("c", { kind: "imagery", sourceId: "collection-a" }),
    });
    expect(imagery.layers.map((l) => l.id)).toEqual(["a", "c"]);
  });

  it("removes by id and ignores an unknown id", () => {
    const state = stateWith(layer("a"), layer("b"));

    expect(mapReducer(state, { type: "remove", id: "a" }).layers.map((l) => l.id)).toEqual(["b"]);
    expect(mapReducer(state, { type: "remove", id: "zz" })).toBe(state);
  });

  it("moves a layer one step and stops at the ends", () => {
    const state = stateWith(layer("a"), layer("b"), layer("c"));

    // "up" in the panel = higher in the stack = later in the draw array.
    expect(
      mapReducer(state, { type: "move", id: "a", direction: "up" }).layers.map((l) => l.id),
    ).toEqual(["b", "a", "c"]);
    expect(
      mapReducer(state, { type: "move", id: "c", direction: "down" }).layers.map((l) => l.id),
    ).toEqual(["a", "c", "b"]);

    expect(mapReducer(state, { type: "move", id: "c", direction: "up" })).toBe(state);
    expect(mapReducer(state, { type: "move", id: "a", direction: "down" })).toBe(state);
    expect(mapReducer(state, { type: "move", id: "zz", direction: "up" })).toBe(state);
  });

  it("sets visibility, opacity and the imagery asset on one layer only", () => {
    const state = stateWith(layer("a"), layer("b", { kind: "imagery" }));

    const hidden = mapReducer(state, { type: "setVisible", id: "a", visible: false });
    expect(hidden.layers.map((l) => l.visible)).toEqual([false, true]);

    const dimmed = mapReducer(state, { type: "setOpacity", id: "b", opacity: 0.25 });
    expect(dimmed.layers.map((l) => l.opacity)).toEqual([1, 0.25]);

    const assigned = mapReducer(state, { type: "setAsset", id: "b", asset: "visual" });
    expect(assigned.layers.map((l) => l.asset)).toEqual([undefined, "visual"]);
  });

  it("returns to the newest tick when the span changes", () => {
    // Every layer's items query refetches on a span change, so an index into
    // the old axis means nothing (spec §4.4).
    const state = { ...stateWith(layer("a")), axisIndex: 7 };

    const next = mapReducer(state, { type: "setFrameSpan", frameSpan: 200 });
    expect(next.frameSpan).toBe(200);
    expect(next.axisIndex).toBeNull();
  });

  it("sets the axis index, including back to newest", () => {
    const state = stateWith(layer("a"));

    expect(mapReducer(state, { type: "setAxisIndex", axisIndex: 3 }).axisIndex).toBe(3);
    expect(mapReducer(state, { type: "setAxisIndex", axisIndex: null }).axisIndex).toBeNull();
  });
});

describe("layerAnchorId / beforeIdFor", () => {
  it("names the STABLE bottom layer of each kind", () => {
    // Never a raster frame id: those are mounted and unmounted every step and
    // maplibre no-ops an addLayer whose beforeId is missing.
    expect(layerAnchorId(layer("a"))).toBe("a-fill");
    expect(layerAnchorId(layer("a", { kind: "imagery" }))).toBe("a-anchor");
    expect(layerAnchorId(layer("a", { kind: "vector" }))).toBe("a-fill");
  });

  it("chains each layer beneath the anchor of the layer above it", () => {
    const layers = [layer("a"), layer("b", { kind: "imagery" }), layer("c")];

    expect(beforeIdFor(layers, 0)).toBe("b-anchor");
    expect(beforeIdFor(layers, 1)).toBe("c-fill");
    // The topmost layer chains to nothing and is simply appended.
    expect(beforeIdFor(layers, 2)).toBeUndefined();
  });
});

describe("opacityFromSlider", () => {
  it("converts percent to a clamped 0..1 opacity", () => {
    expect(opacityFromSlider([50])).toBe(0.5);
    expect(opacityFromSlider([0])).toBe(0);
    expect(opacityFromSlider([100])).toBe(1);
  });

  it("never lets an out-of-range value reach a layer", () => {
    expect(opacityFromSlider([150])).toBe(1);
    expect(opacityFromSlider([-20])).toBe(0);
    expect(opacityFromSlider([Number.NaN])).toBe(0);
    expect(opacityFromSlider([])).toBe(1);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-state.test.ts`
Expected: FAIL — cannot resolve `@/lib/map/state`.

- [ ] **Step 3: Write the reducer**

`app/src/lib/map/state.ts`:

```ts
/**
 * The /map page's layer state (spec §4.2).
 *
 * A reducer in the island: nothing outside the page reads this, and a
 * nanostore would make it persistence by accident (spec §9). Pure and
 * exhaustively tested — the interesting behaviour is at the edges (a
 * duplicate add, a move at either end), and none of it is worth discovering
 * against a live map.
 *
 * Layer ids come from the CALLER so this module stays pure; the page hands
 * in a per-session counter.
 */
import {
  clamp01,
  footprintLayerIds,
  rasterFrameStackAnchorId,
} from "@stac-higher/shared";

export type LayerKind = "footprints" | "imagery" | "vector";

export interface MapLayer {
  /** Client-generated, stable for the session; also the maplibre source id. */
  id: string;
  kind: LayerKind;
  /** footprints | imagery: a built-in-catalog collection id. vector: a tipg collection id. */
  sourceId: string;
  title: string;
  /** imagery only; a `previewAssetCandidates` key (V-3). */
  asset?: string;
  visible: boolean;
  /** 0..1, default 1. Clamped by the control that sets it, not here. */
  opacity: number;
}

/** Newest items per STAC layer, page-wide (spec §4.2). */
export type FrameSpan = 25 | 50 | 100 | 200;
export const FRAME_SPANS: readonly FrameSpan[] = [25, 50, 100, 200];
export const DEFAULT_FRAME_SPAN: FrameSpan = 50;

export interface MapState {
  /** Draw order, BOTTOM first — the panel renders this reversed. */
  layers: MapLayer[];
  frameSpan: FrameSpan;
  /** null = the newest tick (the end of the axis), as the preview does. */
  axisIndex: number | null;
}

export const INITIAL_MAP_STATE: MapState = {
  layers: [],
  frameSpan: DEFAULT_FRAME_SPAN,
  axisIndex: null,
};

export type MapAction =
  | { type: "add"; layer: MapLayer }
  | { type: "remove"; id: string }
  | { type: "move"; id: string; direction: "up" | "down" }
  | { type: "setVisible"; id: string; visible: boolean }
  | { type: "setOpacity"; id: string; opacity: number }
  | { type: "setAsset"; id: string; asset: string }
  | { type: "setFrameSpan"; frameSpan: FrameSpan }
  | { type: "setAxisIndex"; axisIndex: number | null };

function patch(
  state: MapState,
  id: string,
  change: (layer: MapLayer) => MapLayer,
): MapState {
  if (!state.layers.some((l) => l.id === id)) return state;
  return {
    ...state,
    layers: state.layers.map((l) => (l.id === id ? change(l) : l)),
  };
}

export function mapReducer(state: MapState, action: MapAction): MapState {
  switch (action.type) {
    case "add": {
      // One layer per (kind, source): a second copy would collide on nothing
      // technically, but it is always a mis-click and it doubles the queries.
      const exists = state.layers.some(
        (l) => l.kind === action.layer.kind && l.sourceId === action.layer.sourceId,
      );
      if (exists) return state;
      return { ...state, layers: [...state.layers, action.layer] };
    }

    case "remove": {
      if (!state.layers.some((l) => l.id === action.id)) return state;
      return { ...state, layers: state.layers.filter((l) => l.id !== action.id) };
    }

    case "move": {
      const from = state.layers.findIndex((l) => l.id === action.id);
      if (from === -1) return state;
      // "up" in the panel means higher in the stack, which is LATER in the
      // draw array (bottom first).
      const to = action.direction === "up" ? from + 1 : from - 1;
      if (to < 0 || to >= state.layers.length) return state;
      const layers = [...state.layers];
      [layers[from], layers[to]] = [layers[to], layers[from]];
      return { ...state, layers };
    }

    case "setVisible":
      return patch(state, action.id, (l) => ({ ...l, visible: action.visible }));

    case "setOpacity":
      return patch(state, action.id, (l) => ({ ...l, opacity: action.opacity }));

    case "setAsset":
      return patch(state, action.id, (l) => ({ ...l, asset: action.asset }));

    case "setFrameSpan":
      // Every STAC layer's items query refetches, so the axis is rebuilt and
      // an index into the old one means nothing: back to the newest tick.
      return { ...state, frameSpan: action.frameSpan, axisIndex: null };

    case "setAxisIndex":
      return { ...state, axisIndex: action.axisIndex };
  }
}

/**
 * The id of the layer's own BOTTOM-most maplibre layer — the only id that is
 * stable for the life of the layer, and therefore the only legal `beforeId`
 * target. A raster stack's frame layers come and go on every step, which is
 * why imagery resolves to the stack's anchor (spec §11.2).
 */
export function layerAnchorId(layer: MapLayer): string {
  switch (layer.kind) {
    case "imagery":
      return rasterFrameStackAnchorId(layer.id);
    case "footprints":
      return footprintLayerIds(layer.id).fill;
    case "vector":
      // V-4 replaces this with a `vectorTileLayerIds` helper beside
      // `vectorTileLayers`; the shape (`${id}-fill`) is already settled.
      return `${layer.id}-fill`;
  }
}

/**
 * What the layer at `index` draws beneath: the anchor of the layer above it,
 * or nothing when it is the topmost layer.
 */
export function beforeIdFor(layers: MapLayer[], index: number): string | undefined {
  const above = layers[index + 1];
  return above ? layerAnchorId(above) : undefined;
}

/**
 * The single place a user-supplied opacity is clamped (V-1 final review):
 * the layer components take the caller's number unchanged and maplibre
 * rejects anything outside 0..1 as a style error.
 */
export function opacityFromSlider(values: number[]): number {
  const percent = values[0];
  if (percent === undefined) return 1;
  return clamp01(percent / 100);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/map-state.test.ts`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/map/state.ts app/src/__tests__/map-state.test.ts
git commit -m "feat(map): /map layer-state reducer with anchor chaining helpers (V-2, spec §4.2)"
```

---

### Task 5: `StacMap` interaction props

Spec §4.6 needs hover and click over the footprint fills. `StacMap` today forwards only `onClick`; the four props added here go straight through to react-map-gl's `<Map>` and are all optional, so every existing caller is untouched.

`interactiveLayerIds` is what makes `e.features` populated at all; without it a mousemove carries no features and no hover is possible.

**Files:**
- Modify: `packages/shared/src/components/map/StacMap.tsx:14-58`
- Test: `app/src/__tests__/stac-map.test.tsx` (new)

**Interfaces:**
- Produces: `StacMap` additionally accepts
  ```ts
  interactiveLayerIds?: string[];
  onMouseMove?: (e: MapMouseEvent) => void;
  onMouseLeave?: (e: MapMouseEvent) => void;
  cursor?: string;
  ```
  each forwarded verbatim to `<Map>`. `MapMouseEvent` is react-map-gl's (`react-map-gl/maplibre`), the type `onClick` already uses.

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/stac-map.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// The real Map needs WebGL. This stand-in records the props it was handed so
// the test can assert what StacMap forwards.
const { mapProps } = vi.hoisted(() => ({
  mapProps: { current: null as Record<string, unknown> | null },
}));

vi.mock("react-map-gl/maplibre", () => ({
  default: (props: Record<string, unknown>) => {
    mapProps.current = props;
    return <div data-testid="map">{props.children as React.ReactNode}</div>;
  },
  NavigationControl: () => <div data-testid="nav" />,
  ScaleControl: () => <div data-testid="scale" />,
}));

import { StacMap } from "@stac-higher/shared";

beforeEach(() => {
  mapProps.current = null;
});

describe("StacMap interaction props", () => {
  it("forwards the interactive layer ids and the cursor", () => {
    render(<StacMap interactiveLayerIds={["a-fill", "b-fill"]} cursor="pointer" />);

    expect(mapProps.current?.interactiveLayerIds).toEqual(["a-fill", "b-fill"]);
    expect(mapProps.current?.cursor).toBe("pointer");
  });

  it("forwards the mouse handlers untouched", () => {
    const onMouseMove = vi.fn();
    const onMouseLeave = vi.fn();
    render(<StacMap onMouseMove={onMouseMove} onMouseLeave={onMouseLeave} />);

    (mapProps.current?.onMouseMove as (e: unknown) => void)({ point: { x: 1, y: 2 } });
    (mapProps.current?.onMouseLeave as (e: unknown) => void)({});

    expect(onMouseMove).toHaveBeenCalledWith({ point: { x: 1, y: 2 } });
    expect(onMouseLeave).toHaveBeenCalledTimes(1);
  });

  it("leaves every new prop undefined when the caller omits it", () => {
    // The item, search, browse and preview pages pass none of these.
    render(<StacMap />);

    expect(mapProps.current?.interactiveLayerIds).toBeUndefined();
    expect(mapProps.current?.cursor).toBeUndefined();
    expect(mapProps.current?.onMouseMove).toBeUndefined();
    expect(screen.getByTestId("map")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/stac-map.test.tsx`
Expected: FAIL — `interactiveLayerIds` is not a `StacMap` prop (TS error at build; at runtime the assertions on `interactiveLayerIds` / `cursor` are `undefined`).

- [ ] **Step 3: Add the props**

`packages/shared/src/components/map/StacMap.tsx` — replace the props interface and the component signature/return (lines 14-58):

```tsx
interface StacMapProps {
  children?: React.ReactNode;
  className?: string;
  initialBounds?: LngLatBoundsLike;
  onMapRef?: (ref: MapRef) => void;
  onClick?: (e: MapMouseEvent) => void;
  /**
   * Layer ids that answer mouse queries. Without this, `e.features` is empty
   * on every event — it is what turns a plain map into one you can hover.
   */
  interactiveLayerIds?: string[];
  onMouseMove?: (e: MapMouseEvent) => void;
  onMouseLeave?: (e: MapMouseEvent) => void;
  /** CSS cursor over the canvas, e.g. "pointer" while a feature is hovered. */
  cursor?: string;
}

export function StacMap({
  children,
  className = "h-[400px] w-full rounded-lg overflow-hidden",
  initialBounds,
  onMapRef,
  onClick,
  interactiveLayerIds,
  onMouseMove,
  onMouseLeave,
  cursor,
}: StacMapProps) {
  const theme = useStore($theme);
  const mapRef = useRef<MapRef>(null);

  const onLoad = useCallback(() => {
    if (mapRef.current && initialBounds) {
      mapRef.current.fitBounds(initialBounds, { padding: 50, duration: 0 });
    }
    if (mapRef.current && onMapRef) {
      onMapRef(mapRef.current);
    }
  }, [initialBounds, onMapRef]);

  return (
    <Map
      ref={mapRef}
      initialViewState={{
        longitude: 0,
        latitude: 20,
        zoom: 1.5,
      }}
      style={{ width: "100%", height: "100%" }}
      mapStyle={theme === "dark" ? BASEMAP_DARK : BASEMAP_LIGHT}
      onLoad={onLoad}
      onClick={onClick}
      interactiveLayerIds={interactiveLayerIds}
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
      cursor={cursor}
      attributionControl={false}
    >
      <NavigationControl position="top-right" />
      <ScaleControl position="bottom-left" />
      {children}
    </Map>
  );
}
```

The `className` prop stays unused by `<Map>` exactly as before (the size comes from `style`); do not "fix" that here.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/stac-map.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/shared/src/components/map/StacMap.tsx app/src/__tests__/stac-map.test.tsx
git commit -m "feat(map): StacMap forwards interactiveLayerIds, hover handlers and cursor (V-2, spec §4.6)"
```

---

### Task 6: The `/map` route, shell and empty state

Spec §4.1 and the empty half of §4.5. The page is an Astro thin shell mounting one React island, exactly as `/monitoring` does: `AppShell` supplies `QueryProvider` + sidebar + top bar, and the island renders its own `<main>`.

The layout is fixed here and not revisited: panel left at a fixed width scrolling on its own, map filling the rest, and — reserved, empty in V-2 — the bottom of the map column where V-3 docks the time bar.

**Files:**
- Create: `app/src/pages/map.astro`
- Create: `app/src/components/map/MapPage.tsx`
- Create: `app/src/components/map/LayerPanel.tsx`
- Modify: `app/src/components/layout/SidebarNav.tsx:17-27` (imports) and `:53-59` (`OPERATE`)
- Modify: `app/src/components/layout/TopBar.tsx:28-53` (`ROUTE_TITLES`)
- Test: `app/src/__tests__/map-page.test.tsx` (new)

**Interfaces:**
- Consumes: `INITIAL_MAP_STATE`, `mapReducer`, `MapLayer` (Task 4); `StacMap` (Task 5).
- Produces:
  ```ts
  export function MapPage(): JSX.Element;            // app/src/components/map/MapPage.tsx
  export function LayerPanel(props: LayerPanelProps): JSX.Element;
  export interface LayerPanelProps {
    layers: MapLayer[];        // draw order, bottom first; the panel reverses it
    collections: StacCollection[];
    catalogUrl: string;
    frameSpan: FrameSpan;
  }
  ```
  Task 7 adds `onAdd` and the rows to `LayerPanelProps`, Task 8 adds `onVisibleChange` / `onOpacityChange` / `onMove` / `onRemove`. This task delivers the route, the island and the empty state only.

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/map-page.test.tsx`:

```tsx
/**
 * /map island (V-2, spec §4). Query hooks and the map are mocked; what is
 * under test is the page's own logic — layer state, the layer panel, the
 * beforeId chain, and the hover/click plumbing.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { StacCollection } from "@/lib/stac-api/types";

const { useCollectionsMock, useItemsMock, mapProps } = vi.hoisted(() => ({
  useCollectionsMock: vi.fn(),
  useItemsMock: vi.fn(),
  mapProps: { current: null as Record<string, unknown> | null },
}));

// The shell is replaced by the bare QueryProvider it wraps: this test
// exercises page content, not the sidebar/top-bar chrome.
vi.mock("@/components/layout/AppShell", async () => {
  const { QueryProvider } = await import("@/components/layout/QueryProvider");
  return {
    AppShell: ({ children }: { children: React.ReactNode }) => (
      <QueryProvider>{children}</QueryProvider>
    ),
  };
});
vi.mock("@/stores/catalogStore", async () => {
  const { atom } = await import("nanostores");
  return {
    $builtInCatalog: atom({
      id: "built-in",
      name: "Built-in Catalog",
      url: "http://localhost:8081",
      isDefault: true,
      builtIn: true,
    }),
  };
});
vi.mock("@/lib/query/collections", () => ({
  useCollections: (...a: unknown[]) => useCollectionsMock(...a),
}));
vi.mock("@/lib/query/items", () => ({
  useItems: (...a: unknown[]) => useItemsMock(...a),
}));
// maplibre needs WebGL; inert stand-ins keep the tree — and the source/layer
// specs — intact. The DEFAULT export is the Map that StacMap renders, and it
// records its props so the test can drive the hover/click handlers.
vi.mock("react-map-gl/maplibre", () => ({
  default: (props: Record<string, unknown>) => {
    mapProps.current = props;
    return <div data-testid="map">{props.children as React.ReactNode}</div>;
  },
  Source: ({ children, ...props }: Record<string, unknown> & { children?: React.ReactNode }) => (
    <div data-testid="source" data-props={JSON.stringify(props)}>
      {children}
    </div>
  ),
  Layer: (props: Record<string, unknown>) => (
    <div data-testid="layer" data-props={JSON.stringify(props)} />
  ),
  NavigationControl: () => <div />,
  ScaleControl: () => <div />,
}));

import { MapPage } from "@/components/map/MapPage";

function collection(id: string, bbox?: number[]): StacCollection {
  return {
    type: "Collection",
    stac_version: "1.0.0",
    id,
    title: `Product ${id}`,
    description: "",
    license: "proprietary",
    extent: {
      spatial: { bbox: bbox ? [bbox] : [[-180, -90, 180, 90]] },
      temporal: { interval: [[null, null]] },
    },
    links: [],
  } as unknown as StacCollection;
}

beforeAll(() => {
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia);
});

beforeEach(() => {
  mapProps.current = null;
  useCollectionsMock.mockReset();
  useItemsMock.mockReset();
  useCollectionsMock.mockReturnValue({
    data: { collections: [collection("alpha"), collection("beta")] },
    isLoading: false,
  });
  useItemsMock.mockReturnValue({ data: { features: [] }, isLoading: false });
});

describe("MapPage", () => {
  it("opens on an empty map with an invitation to add the first layer", () => {
    render(<MapPage />);

    expect(screen.getByTestId("map")).toBeTruthy();
    expect(screen.getByText(/No layers yet/i)).toBeTruthy();
    expect(screen.queryAllByTestId("map-layer-row")).toHaveLength(0);
    // Nothing is drawn on the map, and nothing errored (spec §4.7).
    expect(screen.queryAllByTestId("source")).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-page.test.tsx`
Expected: FAIL — cannot resolve `@/components/map/MapPage`.

- [ ] **Step 3: Create the page, the island and the panel shell**

`app/src/pages/map.astro`:

```astro
---
import Layout from "../layouts/Layout.astro";
import { MapPage } from "../components/map/MapPage";
---

<Layout title="Map">
  <MapPage client:only="react" />
</Layout>
```

`app/src/components/map/MapPage.tsx`:

```tsx
/**
 * /map island (V-2, spec §4): the built-in catalog's products as map layers.
 *
 * Layer state is the reducer in `@/lib/map/state` — the page owns it, nothing
 * else reads it, and none of it is persisted in v1 (spec §4.2, §9).
 *
 * Nothing on this page renders an error state (spec §4.7): a product with no
 * timestamped items, an unreachable catalog or an empty collection list all
 * mean "that layer draws nothing" or "that option is absent".
 */
import { useReducer } from "react";
import { useStore } from "@nanostores/react";
import { StacMap } from "@stac-higher/shared";
import { AppShell } from "@/components/layout/AppShell";
import { LayerPanel } from "@/components/map/LayerPanel";
import { useCollections } from "@/lib/query/collections";
import { INITIAL_MAP_STATE, mapReducer } from "@/lib/map/state";
import { $builtInCatalog } from "@/stores/catalogStore";

function MapPageInner() {
  const catalog = useStore($builtInCatalog);
  const catalogUrl = catalog?.url ?? "";
  const [state] = useReducer(mapReducer, INITIAL_MAP_STATE);

  const { data: collectionList } = useCollections(catalogUrl);
  const collections = collectionList?.collections ?? [];

  return (
    <main className="flex min-h-0 flex-1 overflow-hidden">
      <LayerPanel
        layers={state.layers}
        collections={collections}
        catalogUrl={catalogUrl}
        frameSpan={state.frameSpan}
      />
      <div className="relative flex min-w-0 flex-1 flex-col">
        <div className="relative min-h-0 flex-1">
          <StacMap className="h-full w-full" />
        </div>
        {/* V-3 docks the shared time bar here, below the map and above the
            page edge. Nothing is rendered in V-2: an empty bar would take
            height from the map for no reason. */}
      </div>
    </main>
  );
}

export function MapPage() {
  return (
    <AppShell>
      <MapPageInner />
    </AppShell>
  );
}
```

`app/src/components/map/LayerPanel.tsx`:

```tsx
/**
 * The /map layer list (spec §4.5), TOPMOST layer first — the order a reader
 * sees on the map, which is the reverse of the draw order the state keeps.
 */
import { Layers } from "lucide-react";
import { EmptyState } from "@stac-higher/shared";
import type { StacCollection } from "@/lib/stac-api/types";
import type { FrameSpan, MapLayer } from "@/lib/map/state";

export interface LayerPanelProps {
  /** Draw order, bottom first. */
  layers: MapLayer[];
  collections: StacCollection[];
  catalogUrl: string;
  frameSpan: FrameSpan;
}

export function LayerPanel({ layers }: LayerPanelProps) {
  const topFirst = [...layers].reverse();

  return (
    <aside
      data-testid="map-layer-panel"
      className="flex w-80 shrink-0 flex-col overflow-y-auto border-r border-border bg-card"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h1 className="text-sm font-bold tracking-tight">Layers</h1>
      </div>

      <div className="flex-1 p-3">
        {topFirst.length === 0 ? (
          <EmptyState
            icon={Layers}
            title="No layers yet"
            description="Add a product from the built-in catalog to draw its item footprints on the map."
          />
        ) : null}
      </div>
    </aside>
  );
}
```

`app/src/components/layout/SidebarNav.tsx` — add the icon import (lines 17-27; `Map` is imported under an alias so it never shadows the global `Map` constructor in this module):

```tsx
import {
  Activity,
  ChevronDown,
  Cpu,
  Database,
  Layers,
  Map as MapIcon,
  Plug,
  Puzzle,
  Search,
  Share2,
} from "lucide-react";
```

and the entry, after Products (lines 53-59):

```tsx
const OPERATE: NavItem[] = [
  { href: "/", label: "Products", icon: Layers, alsoMatches: ["/collections"] },
  { href: "/map", label: "Map", icon: MapIcon },
  { href: "/processes", label: "Processes", icon: Cpu },
  { href: "/connections", label: "Connections", icon: Plug },
  { href: "/graph", label: "Pipeline graph", icon: Share2 },
  { href: "/monitoring", label: "Monitoring", icon: Activity },
];
```

`app/src/components/layout/TopBar.tsx` — add to `ROUTE_TITLES` (after the `/monitoring` row, line 42):

```ts
  [/^\/map$/, "Map"],
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/map-page.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 5: Type-check the new route**

Run: `cd app && npm run check`
Expected: 0 errors. (This is the app-scoped `astro check` — never run it from the repo root, where there are no pages.)

- [ ] **Step 6: Commit**

```bash
git add app/src/pages/map.astro app/src/components/map/ app/src/components/layout/SidebarNav.tsx app/src/components/layout/TopBar.tsx app/src/__tests__/map-page.test.tsx
git commit -m "feat(map): /map route, island shell, sidebar entry and empty state (V-2, spec §4.1)"
```

---

### Task 7: Add-layer popover and footprint layers on the map

Spec §4.5's Products section (Footprints only) and §4.3's footprints half. Adding a product puts a row in the panel and a `FootprintLayer` on the map, drawn under the anchor of the layer above it.

Two things settled here and relied on later:

1. **Layer components are rendered topmost-first.** react-map-gl creates layers *during render*, in tree order, with `map.addLayer(options, beforeId)`. If the layer named by `beforeId` is not in the style yet, maplibre fires an error event and drops the call. Rendering the top of the stack first guarantees every chain target already exists.
2. **A footprints layer draws its whole span in V-2.** There is no axis yet; V-3 switches it to the current frame's items (spec §4.4). Until then `limit: frameSpan` items are all drawn.

**Files:**
- Create: `app/src/components/map/AddLayerPopover.tsx`
- Create: `app/src/components/map/FootprintsMapLayer.tsx`
- Create: `app/src/components/map/LayerRow.tsx`
- Modify: `app/src/components/map/MapPage.tsx` (dispatch + the drawn-layer list)
- Modify: `app/src/components/map/LayerPanel.tsx` (trigger + rows)
- Test: `app/src/__tests__/map-page.test.tsx` (append)

**Interfaces:**
- Consumes: `mapReducer`, `MapLayer`, `LayerKind`, `beforeIdFor` (Task 4); `FootprintLayer` with `id` / `opacity` / `visible` / `beforeId` (V-1 + Task 3); `useItems`, `useCollections`, `buildPreviewFrames`.
- Produces:
  ```ts
  export function AddLayerPopover(props: {
    collections: StacCollection[];
    isAdded: (kind: LayerKind, sourceId: string) => boolean;
    onAdd: (kind: LayerKind, collection: StacCollection) => void;
  }): JSX.Element;

  export function FootprintsMapLayer(props: {
    layer: MapLayer; catalogUrl: string; frameSpan: number; beforeId?: string;
  }): JSX.Element;

  export function LayerRow(props: {
    layer: MapLayer; catalogUrl: string; frameSpan: number;
    canMoveUp: boolean; canMoveDown: boolean;
    onVisibleChange: (visible: boolean) => void;
    onOpacityChange: (opacity: number) => void;
    onMove: (direction: "up" | "down") => void;
    onRemove: () => void;
  }): JSX.Element;
  ```
  Test ids: `map-add-layer` (trigger), `map-add-footprints-${collectionId}` (option), `map-layer-row` (row, carrying `data-layer-id`), `map-layer-visible`, `map-layer-opacity`, `map-layer-up`, `map-layer-down`, `map-layer-remove`.

- [ ] **Step 1: Write the failing tests**

Append to `app/src/__tests__/map-page.test.tsx`. Extend the top-of-file imports first:

```tsx
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { StacCollection, StacItem } from "@/lib/stac-api/types";
```

and add these helpers below `collection()`:

```tsx
function item(id: string, datetime: string | null): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "alpha",
    geometry: { type: "Point", coordinates: [0, 0] },
    properties: { datetime },
    links: [],
    assets: {},
  } as unknown as StacItem;
}

function sourceIds(): string[] {
  return screen
    .queryAllByTestId("source")
    .map((el) => JSON.parse(el.dataset.props as string).id as string);
}

function layerProps(): Record<string, unknown>[] {
  return screen
    .queryAllByTestId("layer")
    .map((el) => JSON.parse(el.dataset.props as string));
}

function rowIds(): string[] {
  return screen
    .queryAllByTestId("map-layer-row")
    .map((el) => el.dataset.layerId as string);
}

function addFootprints(collectionId: string) {
  fireEvent.click(screen.getByTestId("map-add-layer"));
  fireEvent.click(screen.getByTestId(`map-add-footprints-${collectionId}`));
}
```

Then append these tests inside the `describe("MapPage")` block:

```tsx
  it("adds a footprints layer from the picker, as a row and a namespaced source", () => {
    useItemsMock.mockReturnValue({
      data: { features: [item("i1", "2026-09-07T00:00:00Z")] },
      isLoading: false,
    });
    render(<MapPage />);

    addFootprints("alpha");

    expect(rowIds()).toEqual(["layer-0"]);
    expect(screen.getByText("Product alpha")).toBeTruthy();
    // The source id IS the layer id, so several products can share the map.
    expect(sourceIds()).toEqual(["layer-0"]);
    expect(layerProps().map((l) => l.id)).toEqual(["layer-0-fill", "layer-0-line"]);
  });

  it("asks the catalog for the span's newest items, per layer", () => {
    render(<MapPage />);
    addFootprints("alpha");

    expect(useItemsMock).toHaveBeenCalledWith(
      "http://localhost:8081",
      "alpha",
      { limit: 50, sortby: "-datetime" },
    );
  });

  it("disables a product that is already on the map", () => {
    render(<MapPage />);
    addFootprints("alpha");

    fireEvent.click(screen.getByTestId("map-add-layer"));
    expect(screen.getByTestId("map-add-footprints-alpha")).toBeDisabled();
    expect(screen.getByTestId("map-add-footprints-beta")).not.toBeDisabled();
  });

  it("says so quietly when a product has no timestamped items", () => {
    // Never an error (spec §4.7): the layer is on the map, it just draws
    // nothing the time axis can use.
    useItemsMock.mockReturnValue({ data: { features: [item("i1", null)] }, isLoading: false });
    render(<MapPage />);

    addFootprints("alpha");

    const row = screen.getByTestId("map-layer-row");
    expect(within(row).getByText(/no items with a timestamp/i)).toBeTruthy();
  });

  it("chains each layer beneath the one above it, topmost rendered first", () => {
    render(<MapPage />);
    addFootprints("alpha");
    addFootprints("beta");

    // Panel: topmost first. Draw order: bottom first.
    expect(rowIds()).toEqual(["layer-1", "layer-0"]);
    // Rendered topmost first so "layer-1-fill" exists by the time the layer
    // below it asks maplibre to insert beneath that id.
    expect(sourceIds()).toEqual(["layer-1", "layer-0"]);
    const beforeIds = layerProps().map((l) => l.beforeId);
    expect(beforeIds).toEqual([undefined, undefined, "layer-1-fill", "layer-1-fill"]);
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd app && npx vitest run src/__tests__/map-page.test.tsx`
Expected: FAIL — `map-add-layer` is not in the document.

- [ ] **Step 3: Build the popover, the row and the map-side layer**

`app/src/components/map/AddLayerPopover.tsx`:

```tsx
/**
 * The /map "Add layer" picker (spec §4.5).
 *
 * V-2 offers the Products section with Footprints only. The Imagery option
 * (gated on serving + a tileable-asset probe) is V-3 and the Vector tiles
 * section is V-4; both are marked below rather than stubbed, because a
 * disabled control with nothing behind it reads as a broken feature.
 */
import { useState } from "react";
import { Layers, Plus } from "lucide-react";
import { Button } from "@stac-higher/shared";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { StacCollection } from "@/lib/stac-api/types";
import type { LayerKind } from "@/lib/map/state";

interface AddLayerPopoverProps {
  collections: StacCollection[];
  isAdded: (kind: LayerKind, sourceId: string) => boolean;
  onAdd: (kind: LayerKind, collection: StacCollection) => void;
}

export function AddLayerPopover({
  collections,
  isAdded,
  onAdd,
}: AddLayerPopoverProps) {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="sm" data-testid="map-add-layer">
          <Plus />
          Add layer
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="px-3 pb-1 pt-3 text-[10px] font-bold uppercase tracking-[0.08em] text-muted-foreground">
          Products
        </div>
        {collections.length === 0 ? (
          <p className="px-3 pb-3 text-sm text-muted-foreground">
            The built-in catalog has no products yet.
          </p>
        ) : (
          <ul className="max-h-80 overflow-auto pb-2">
            {collections.map((collection) => {
              const added = isAdded("footprints", collection.id);
              return (
                <li
                  key={collection.id}
                  className="flex items-center gap-2 px-3 py-1.5"
                >
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">
                    {collection.title ?? collection.id}
                  </span>
                  <Button
                    size="xs"
                    variant={added ? "ghost" : "outline"}
                    disabled={added}
                    data-testid={`map-add-footprints-${collection.id}`}
                    onClick={() => {
                      onAdd("footprints", collection);
                      setOpen(false);
                    }}
                  >
                    <Layers />
                    {added ? "Added" : "Footprints"}
                  </Button>
                  {/* V-3 adds an Imagery button beside this one, shown only
                      when the product advertises serving AND a probe of its
                      newest items yields a tileable asset. */}
                </li>
              );
            })}
          </ul>
        )}
        {/* V-4 adds the Vector tiles section here, from tipg's /collections. */}
      </PopoverContent>
    </Popover>
  );
}
```

`app/src/components/map/FootprintsMapLayer.tsx`:

```tsx
/**
 * One footprints layer on the /map map (spec §4.3).
 *
 * The items query lives HERE rather than in the page because the layer list
 * is dynamic and a hook cannot be called in a loop. Two layers over the same
 * collection (footprints + imagery, V-3) share one request through TanStack's
 * key.
 *
 * V-2 draws every item in the span. V-3 narrows this to the items of the
 * current axis tick, so footprints and imagery stay in step.
 */
import { useMemo } from "react";
import { FootprintLayer } from "@stac-higher/shared";
import { useItems } from "@/lib/query/items";
import type { MapLayer } from "@/lib/map/state";

interface FootprintsMapLayerProps {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
  /** The anchor of the layer above this one; undefined when topmost. */
  beforeId?: string;
}

export function FootprintsMapLayer({
  layer,
  catalogUrl,
  frameSpan,
  beforeId,
}: FootprintsMapLayerProps) {
  const { data } = useItems(catalogUrl, layer.sourceId, {
    limit: frameSpan,
    sortby: "-datetime",
  });
  const items = useMemo(() => data?.features ?? [], [data]);

  return (
    <FootprintLayer
      id={layer.id}
      items={items}
      opacity={layer.opacity}
      visible={layer.visible}
      beforeId={beforeId}
    />
  );
}
```

`app/src/components/map/LayerRow.tsx`:

```tsx
/**
 * One row of the /map layer list (spec §4.5). The controls land in the next
 * task; this is the identity half plus the quiet "nothing to draw" line.
 */
import { Layers } from "lucide-react";
import { useItems } from "@/lib/query/items";
import { buildPreviewFrames } from "@/lib/serving/frames";
import type { MapLayer } from "@/lib/map/state";

/** Kind icons: `Image` (imagery, V-3) and `Hexagon` (vector, V-4) follow. */
const KIND_ICON = { footprints: Layers } as const;

/**
 * A layer whose items carry no parseable time draws nothing — say so under
 * the title rather than leaving an empty map unexplained. Never an error
 * (spec §4.7). Its own component so the hook stays unconditional while the
 * row itself renders for every kind.
 */
function FootprintStatusLine({
  layer,
  catalogUrl,
  frameSpan,
}: {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
}) {
  const { data, isLoading } = useItems(catalogUrl, layer.sourceId, {
    limit: frameSpan,
    sortby: "-datetime",
  });
  const items = data?.features ?? [];
  if (isLoading || buildPreviewFrames(items).length > 0) return null;

  return (
    <p className="text-xs text-muted-foreground">no items with a timestamp</p>
  );
}

export interface LayerRowProps {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
}

export function LayerRow({ layer, catalogUrl, frameSpan }: LayerRowProps) {
  const Icon = KIND_ICON[layer.kind as keyof typeof KIND_ICON] ?? Layers;

  return (
    <div
      data-testid="map-layer-row"
      data-layer-id={layer.id}
      className="rounded-md border border-border bg-background p-2.5"
    >
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {layer.title}
        </span>
      </div>
      {layer.kind === "footprints" && (
        <FootprintStatusLine
          layer={layer}
          catalogUrl={catalogUrl}
          frameSpan={frameSpan}
        />
      )}
    </div>
  );
}
```

`app/src/components/map/LayerPanel.tsx` — replace the whole file:

```tsx
/**
 * The /map layer list (spec §4.5), TOPMOST layer first — the order a reader
 * sees on the map, which is the reverse of the draw order the state keeps.
 */
import { Layers } from "lucide-react";
import { EmptyState } from "@stac-higher/shared";
import { AddLayerPopover } from "@/components/map/AddLayerPopover";
import { LayerRow } from "@/components/map/LayerRow";
import type { StacCollection } from "@/lib/stac-api/types";
import type { FrameSpan, LayerKind, MapLayer } from "@/lib/map/state";

export interface LayerPanelProps {
  /** Draw order, bottom first. */
  layers: MapLayer[];
  collections: StacCollection[];
  catalogUrl: string;
  frameSpan: FrameSpan;
  onAdd: (kind: LayerKind, collection: StacCollection) => void;
}

export function LayerPanel({
  layers,
  collections,
  catalogUrl,
  frameSpan,
  onAdd,
}: LayerPanelProps) {
  const topFirst = [...layers].reverse();
  const isAdded = (kind: LayerKind, sourceId: string) =>
    layers.some((l) => l.kind === kind && l.sourceId === sourceId);

  return (
    <aside
      data-testid="map-layer-panel"
      className="flex w-80 shrink-0 flex-col overflow-y-auto border-r border-border bg-card"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h1 className="text-sm font-bold tracking-tight">Layers</h1>
        <AddLayerPopover
          collections={collections}
          isAdded={isAdded}
          onAdd={onAdd}
        />
      </div>

      <div className="flex-1 space-y-2 p-3">
        {topFirst.length === 0 ? (
          <EmptyState
            icon={Layers}
            title="No layers yet"
            description="Add a product from the built-in catalog to draw its item footprints on the map."
          />
        ) : (
          topFirst.map((layer) => (
            <LayerRow
              key={layer.id}
              layer={layer}
              catalogUrl={catalogUrl}
              frameSpan={frameSpan}
            />
          ))
        )}
      </div>
    </aside>
  );
}
```

`app/src/components/map/MapPage.tsx` — replace `MapPageInner` with:

```tsx
function MapPageInner() {
  const catalog = useStore($builtInCatalog);
  const catalogUrl = catalog?.url ?? "";
  const [state, dispatch] = useReducer(mapReducer, INITIAL_MAP_STATE);

  const { data: collectionList } = useCollections(catalogUrl);
  const collections = collectionList?.collections ?? [];

  // Layer ids are a per-session counter rather than a UUID: stable, ordered,
  // and readable in a maplibre style inspector or a failing test.
  const nextLayerId = useRef(0);

  const addLayer = useCallback((kind: LayerKind, collection: StacCollection) => {
    dispatch({
      type: "add",
      layer: {
        id: `layer-${nextLayerId.current++}`,
        kind,
        sourceId: collection.id,
        title: collection.title ?? collection.id,
        visible: true,
        opacity: 1,
      },
    });
  }, []);

  // Draw order is bottom-first, but the layer COMPONENTS are rendered
  // topmost-first: react-map-gl creates layers during render, in tree order,
  // via map.addLayer(spec, beforeId), and maplibre fires an error and drops
  // the call when beforeId names a layer that is not in the style yet.
  const drawn = useMemo(
    () =>
      state.layers
        .map((layer, index) => ({ layer, beforeId: beforeIdFor(state.layers, index) }))
        .reverse(),
    [state.layers],
  );

  return (
    <main className="flex min-h-0 flex-1 overflow-hidden">
      <LayerPanel
        layers={state.layers}
        collections={collections}
        catalogUrl={catalogUrl}
        frameSpan={state.frameSpan}
        onAdd={addLayer}
      />
      <div className="relative flex min-w-0 flex-1 flex-col">
        <div className="relative min-h-0 flex-1">
          <StacMap className="h-full w-full">
            {drawn.map(({ layer, beforeId }) =>
              layer.kind === "footprints" ? (
                <FootprintsMapLayer
                  key={layer.id}
                  layer={layer}
                  catalogUrl={catalogUrl}
                  frameSpan={state.frameSpan}
                  beforeId={beforeId}
                />
              ) : null,
            )}
            {/* V-3 renders imagery layers (RasterFrameStack) and V-4 vector
                layers (VectorTileLayer) from the same list; both chain their
                beforeId through the same helper. */}
          </StacMap>
        </div>
        {/* V-3 docks the shared time bar here, below the map and above the
            page edge. Nothing is rendered in V-2: an empty bar would take
            height from the map for no reason. */}
      </div>
    </main>
  );
}
```

and its imports:

```tsx
import { useCallback, useMemo, useReducer, useRef } from "react";
import { useStore } from "@nanostores/react";
import { StacMap } from "@stac-higher/shared";
import { AppShell } from "@/components/layout/AppShell";
import { FootprintsMapLayer } from "@/components/map/FootprintsMapLayer";
import { LayerPanel } from "@/components/map/LayerPanel";
import { useCollections } from "@/lib/query/collections";
import { INITIAL_MAP_STATE, beforeIdFor, mapReducer } from "@/lib/map/state";
import type { LayerKind } from "@/lib/map/state";
import type { StacCollection } from "@/lib/stac-api/types";
import { $builtInCatalog } from "@/stores/catalogStore";
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/map-page.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/components/map/ app/src/__tests__/map-page.test.tsx
git commit -m "feat(map): add-layer popover and footprints layers with chained beforeIds (V-2, spec §4.3/§4.5)"
```

---

### Task 8: Layer row controls — visibility, opacity, order, remove

The rest of spec §4.5's row. Opacity is where the V-1 review's clamp lands: `opacityFromSlider` (Task 4) converts the slider's percent and clamps it, so nothing out of range ever reaches `footprintLayers`.

**Files:**
- Modify: `app/src/components/map/LayerRow.tsx`
- Modify: `app/src/components/map/LayerPanel.tsx`
- Modify: `app/src/components/map/MapPage.tsx` (the four dispatch callbacks)
- Test: `app/src/__tests__/map-page.test.tsx` (append)

**Interfaces:**
- Consumes: `opacityFromSlider`, `mapReducer` actions (Task 4).
- Produces: `LayerRowProps` gains `canMoveUp`, `canMoveDown`, `onVisibleChange`, `onOpacityChange`, `onMove`, `onRemove`; `LayerPanelProps` gains `onVisibleChange(id, visible)`, `onOpacityChange(id, opacity)`, `onMove(id, direction)`, `onRemove(id)`.

- [ ] **Step 1: Write the failing tests**

Append to `describe("MapPage")` in `app/src/__tests__/map-page.test.tsx`:

```tsx
  it("hides a layer without dropping its source", () => {
    render(<MapPage />);
    addFootprints("alpha");

    fireEvent.click(screen.getByTestId("map-layer-visible"));

    expect(sourceIds()).toEqual(["layer-0"]);
    expect(layerProps().map((l) => l.layout)).toEqual([
      { visibility: "none" },
      { visibility: "none" },
    ]);
  });

  it("removes a layer from the list and the map", () => {
    render(<MapPage />);
    addFootprints("alpha");
    addFootprints("beta");

    const first = screen.getAllByTestId("map-layer-row")[0];
    fireEvent.click(within(first).getByTestId("map-layer-remove"));

    expect(rowIds()).toEqual(["layer-0"]);
    expect(sourceIds()).toEqual(["layer-0"]);
    // The survivor is now topmost and chains to nothing.
    expect(layerProps().map((l) => l.beforeId)).toEqual([undefined, undefined]);
  });

  it("reorders layers and re-chains the beforeIds", () => {
    render(<MapPage />);
    addFootprints("alpha");
    addFootprints("beta");
    expect(rowIds()).toEqual(["layer-1", "layer-0"]);

    // Move the bottom row (layer-0) up: it becomes topmost.
    const bottom = screen.getAllByTestId("map-layer-row")[1];
    fireEvent.click(within(bottom).getByTestId("map-layer-up"));

    expect(rowIds()).toEqual(["layer-0", "layer-1"]);
    expect(sourceIds()).toEqual(["layer-0", "layer-1"]);
    expect(layerProps().map((l) => l.beforeId)).toEqual([
      undefined,
      undefined,
      "layer-0-fill",
      "layer-0-fill",
    ]);
  });

  it("disables the move buttons at the ends of the list", () => {
    render(<MapPage />);
    addFootprints("alpha");
    addFootprints("beta");

    const [top, bottom] = screen.getAllByTestId("map-layer-row");
    expect(within(top).getByTestId("map-layer-up")).toBeDisabled();
    expect(within(top).getByTestId("map-layer-down")).not.toBeDisabled();
    expect(within(bottom).getByTestId("map-layer-down")).toBeDisabled();
  });

  it("puts an opacity control on every row", () => {
    // The clamp itself is unit-tested on opacityFromSlider (map-state.test.ts);
    // what matters here is that the row's control feeds the layer opacity.
    render(<MapPage />);
    addFootprints("alpha");

    expect(screen.getByTestId("map-layer-opacity")).toBeTruthy();
    const [fill, line] = layerProps();
    expect((fill.paint as Record<string, unknown>)["fill-opacity"]).toEqual([
      "case",
      ["boolean", ["feature-state", "hover"], false],
      0.25,
      0.1,
    ]);
    expect(line.paint).not.toHaveProperty("line-opacity");
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd app && npx vitest run src/__tests__/map-page.test.tsx`
Expected: FAIL — `map-layer-visible` is not in the document.

- [ ] **Step 3: Add the controls**

`app/src/components/map/LayerRow.tsx` — replace the exported props and component (keep `FootprintStatusLine` and `KIND_ICON` as they are), and extend the imports:

```tsx
import { ChevronDown, ChevronUp, Eye, EyeOff, Layers, X } from "lucide-react";
import { Button, Slider } from "@stac-higher/shared";
import { useItems } from "@/lib/query/items";
import { buildPreviewFrames } from "@/lib/serving/frames";
import { opacityFromSlider, type MapLayer } from "@/lib/map/state";
```

```tsx
export interface LayerRowProps {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
  canMoveUp: boolean;
  canMoveDown: boolean;
  onVisibleChange: (visible: boolean) => void;
  onOpacityChange: (opacity: number) => void;
  onMove: (direction: "up" | "down") => void;
  onRemove: () => void;
}

export function LayerRow({
  layer,
  catalogUrl,
  frameSpan,
  canMoveUp,
  canMoveDown,
  onVisibleChange,
  onOpacityChange,
  onMove,
  onRemove,
}: LayerRowProps) {
  const Icon = KIND_ICON[layer.kind as keyof typeof KIND_ICON] ?? Layers;

  return (
    <div
      data-testid="map-layer-row"
      data-layer-id={layer.id}
      className="rounded-md border border-border bg-background p-2.5"
    >
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {layer.title}
        </span>
        <Button
          size="icon-xs"
          variant="ghost"
          data-testid="map-layer-visible"
          aria-label={layer.visible ? "Hide layer" : "Show layer"}
          onClick={() => onVisibleChange(!layer.visible)}
        >
          {layer.visible ? <Eye /> : <EyeOff />}
        </Button>
        <Button
          size="icon-xs"
          variant="ghost"
          data-testid="map-layer-up"
          aria-label="Move layer up"
          disabled={!canMoveUp}
          onClick={() => onMove("up")}
        >
          <ChevronUp />
        </Button>
        <Button
          size="icon-xs"
          variant="ghost"
          data-testid="map-layer-down"
          aria-label="Move layer down"
          disabled={!canMoveDown}
          onClick={() => onMove("down")}
        >
          <ChevronDown />
        </Button>
        <Button
          size="icon-xs"
          variant="ghost"
          data-testid="map-layer-remove"
          aria-label="Remove layer"
          onClick={onRemove}
        >
          <X />
        </Button>
      </div>

      {layer.kind === "footprints" && (
        <FootprintStatusLine
          layer={layer}
          catalogUrl={catalogUrl}
          frameSpan={frameSpan}
        />
      )}

      <Slider
        data-testid="map-layer-opacity"
        aria-label="Layer opacity"
        className="mt-2"
        min={0}
        max={100}
        step={1}
        value={[Math.round(layer.opacity * 100)]}
        // The ONE clamp (V-1 review): the layer components take the number
        // unchanged and maplibre rejects anything outside 0..1.
        onValueChange={(values) => onOpacityChange(opacityFromSlider(values))}
      />
    </div>
  );
}
```

`app/src/components/map/LayerPanel.tsx` — extend the props and the row render:

```tsx
export interface LayerPanelProps {
  /** Draw order, bottom first. */
  layers: MapLayer[];
  collections: StacCollection[];
  catalogUrl: string;
  frameSpan: FrameSpan;
  onAdd: (kind: LayerKind, collection: StacCollection) => void;
  onVisibleChange: (id: string, visible: boolean) => void;
  onOpacityChange: (id: string, opacity: number) => void;
  onMove: (id: string, direction: "up" | "down") => void;
  onRemove: (id: string) => void;
}

export function LayerPanel({
  layers,
  collections,
  catalogUrl,
  frameSpan,
  onAdd,
  onVisibleChange,
  onOpacityChange,
  onMove,
  onRemove,
}: LayerPanelProps) {
```

```tsx
          topFirst.map((layer, index) => (
            <LayerRow
              key={layer.id}
              layer={layer}
              catalogUrl={catalogUrl}
              frameSpan={frameSpan}
              // The list runs topmost first, so index 0 cannot go up and the
              // last row cannot go down.
              canMoveUp={index > 0}
              canMoveDown={index < topFirst.length - 1}
              onVisibleChange={(visible) => onVisibleChange(layer.id, visible)}
              onOpacityChange={(opacity) => onOpacityChange(layer.id, opacity)}
              onMove={(direction) => onMove(layer.id, direction)}
              onRemove={() => onRemove(layer.id)}
            />
          ))
```

`app/src/components/map/MapPage.tsx` — pass the four dispatchers to `LayerPanel`:

```tsx
      <LayerPanel
        layers={state.layers}
        collections={collections}
        catalogUrl={catalogUrl}
        frameSpan={state.frameSpan}
        onAdd={addLayer}
        onVisibleChange={(id, visible) => dispatch({ type: "setVisible", id, visible })}
        onOpacityChange={(id, opacity) => dispatch({ type: "setOpacity", id, opacity })}
        onMove={(id, direction) => dispatch({ type: "move", id, direction })}
        onRemove={(id) => dispatch({ type: "remove", id })}
      />
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/map-page.test.tsx src/__tests__/map-state.test.ts`
Expected: PASS (11 + 12 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/components/map/ app/src/__tests__/map-page.test.tsx
git commit -m "feat(map): layer row controls — visibility, clamped opacity, order, remove (V-2, spec §4.5)"
```

---

### Task 9: Hover, click-through and the first-add camera fit

Spec §4.6. Hover paints the `hover` feature-state the footprint styles have always read (nothing in the repo has ever set it) and shows a small tooltip at the pointer; a click opens the item page in a new tab; the camera moves exactly once, on the first layer added.

`interactiveLayerIds` is recomputed from the **visible** footprint layers, so a hidden layer stops answering the mouse.

**Files:**
- Create: `app/src/components/map/MapTooltip.tsx`
- Modify: `app/src/components/map/MapPage.tsx`
- Test: `app/src/__tests__/map-page.test.tsx` (append)

**Interfaces:**
- Consumes: `StacMap`'s `interactiveLayerIds` / `onMouseMove` / `onMouseLeave` / `cursor` (Task 5); `footprintLayerIds` and `bboxToLngLatBounds` from `@stac-higher/shared`.
- Produces:
  ```ts
  export function MapTooltip(props: {
    id: string; datetime: string; x: number; y: number;
  }): JSX.Element;      // rendered with data-testid="map-tooltip"
  ```

- [ ] **Step 1: Write the failing tests**

Append to `describe("MapPage")` in `app/src/__tests__/map-page.test.tsx`, and add this helper next to the others:

```tsx
function hoverFeature(feature: Record<string, unknown>, x = 12, y = 34) {
  const onMouseMove = mapProps.current?.onMouseMove as (e: unknown) => void;
  onMouseMove({ features: [feature], point: { x, y } });
}
```

```tsx
  it("makes the visible footprint fills the interactive layers", () => {
    render(<MapPage />);
    addFootprints("alpha");
    addFootprints("beta");

    expect(mapProps.current?.interactiveLayerIds).toEqual([
      "layer-0-fill",
      "layer-1-fill",
    ]);

    // A hidden layer stops answering the mouse.
    fireEvent.click(screen.getAllByTestId("map-layer-visible")[0]);
    expect(mapProps.current?.interactiveLayerIds).toEqual(["layer-0-fill"]);
  });

  it("shows a tooltip with the item id and time while a footprint is hovered", () => {
    render(<MapPage />);
    addFootprints("alpha");

    expect(screen.queryByTestId("map-tooltip")).toBeNull();

    hoverFeature({
      source: "layer-0",
      id: "i1",
      properties: { id: "i1", datetime: "2026-09-07T00:00:00Z" },
    });

    const tooltip = screen.getByTestId("map-tooltip");
    expect(within(tooltip).getByText("i1")).toBeTruthy();
    expect(within(tooltip).getByText(/2026-09-07/)).toBeTruthy();
    expect(mapProps.current?.cursor).toBe("pointer");

    // Moving off every feature clears it.
    (mapProps.current?.onMouseMove as (e: unknown) => void)({
      features: [],
      point: { x: 1, y: 1 },
    });
    expect(screen.queryByTestId("map-tooltip")).toBeNull();
    expect(mapProps.current?.cursor).toBeUndefined();
  });

  it("opens the item page in a new tab on click", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<MapPage />);
    addFootprints("alpha");

    (mapProps.current?.onClick as (e: unknown) => void)({
      features: [{ source: "layer-0", id: "i1", properties: { id: "i1" } }],
      point: { x: 1, y: 1 },
    });

    expect(open).toHaveBeenCalledWith(
      "/collections/alpha/items/i1",
      "_blank",
      "noopener",
    );
    open.mockRestore();
  });

  it("ignores a click that hit no footprint", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<MapPage />);
    addFootprints("alpha");

    (mapProps.current?.onClick as (e: unknown) => void)({ features: [], point: { x: 1, y: 1 } });

    expect(open).not.toHaveBeenCalled();
    open.mockRestore();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd app && npx vitest run src/__tests__/map-page.test.tsx`
Expected: FAIL — `mapProps.current.onMouseMove` is `undefined` (the page passes no handler yet).

- [ ] **Step 3: Implement hover, click and the camera fit**

`app/src/components/map/MapTooltip.tsx`:

```tsx
/**
 * The hover readout over a footprint (spec §4.6): item id and time, placed at
 * the pointer. Deliberately not a maplibre Popup — a Popup is anchored to a
 * coordinate and animates; this follows the cursor and must never intercept
 * the mouse events the map is listening for.
 */
interface MapTooltipProps {
  id: string;
  datetime: string;
  /** Pixel offset inside the map container, from the mouse event's `point`. */
  x: number;
  y: number;
}

export function MapTooltip({ id, datetime, x, y }: MapTooltipProps) {
  return (
    <div
      data-testid="map-tooltip"
      className="pointer-events-none absolute z-10 max-w-[16rem] rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md"
      style={{ left: x + 12, top: y + 12 }}
    >
      <div className="tech truncate font-medium">{id}</div>
      {datetime && <div className="text-muted-foreground">{datetime}</div>}
    </div>
  );
}
```

`app/src/components/map/MapPage.tsx` — add the imports:

```tsx
import { useCallback, useMemo, useReducer, useRef, useState } from "react";
import type { MapMouseEvent, MapRef } from "react-map-gl/maplibre";
import { StacMap, bboxToLngLatBounds, footprintLayerIds } from "@stac-higher/shared";
import { MapTooltip } from "@/components/map/MapTooltip";
```

and, inside `MapPageInner`, the map ref, hover state and handlers (place them after `drawn`):

```tsx
  const mapRef = useRef<MapRef | null>(null);
  const onMapRef = useCallback((map: MapRef) => {
    mapRef.current = map;
  }, []);

  // What the tooltip shows. The maplibre feature-state that DRAWS the hover
  // is tracked separately, in a ref: it is set on the map imperatively and
  // must be cleared on the previous feature before the next one is set.
  const [hovered, setHovered] = useState<{
    id: string;
    datetime: string;
    x: number;
    y: number;
  } | null>(null);
  const hoveredFeature = useRef<{ source: string; id: string } | null>(null);

  const clearHover = useCallback(() => {
    const current = hoveredFeature.current;
    if (current) mapRef.current?.setFeatureState(current, { hover: false });
    hoveredFeature.current = null;
    setHovered(null);
  }, []);

  const onMouseMove = useCallback(
    (e: MapMouseEvent) => {
      const feature = e.features?.[0];
      if (!feature || feature.id === undefined || feature.id === null) {
        clearHover();
        return;
      }

      const next = { source: feature.source, id: String(feature.id) };
      const current = hoveredFeature.current;
      if (current?.source !== next.source || current?.id !== next.id) {
        if (current) mapRef.current?.setFeatureState(current, { hover: false });
        // The styles have read this feature-state since the first map landed;
        // this page is the first thing that sets it.
        mapRef.current?.setFeatureState(next, { hover: true });
        hoveredFeature.current = next;
      }

      const properties = (feature.properties ?? {}) as Record<string, unknown>;
      setHovered({
        id: String(properties.id ?? next.id),
        datetime: properties.datetime ? String(properties.datetime) : "",
        x: e.point.x,
        y: e.point.y,
      });
    },
    [clearHover],
  );

  const onClick = useCallback(
    (e: MapMouseEvent) => {
      const feature = e.features?.[0];
      if (!feature) return;
      // The footprint source id IS the layer id, which carries the collection.
      const layer = state.layers.find((l) => l.id === feature.source);
      if (!layer) return;
      const properties = (feature.properties ?? {}) as Record<string, unknown>;
      const itemId = String(properties.id ?? feature.id ?? "");
      if (!itemId) return;
      window.open(
        `/collections/${encodeURIComponent(layer.sourceId)}/items/${encodeURIComponent(itemId)}`,
        "_blank",
        "noopener",
      );
    },
    [state.layers],
  );

  // Only the visible footprint layers answer the mouse; imagery and vector
  // layers have no hover or click behaviour in v1 (spec §4.6).
  const interactiveLayerIds = useMemo(
    () =>
      state.layers
        .filter((l) => l.kind === "footprints" && l.visible)
        .map((l) => footprintLayerIds(l.id).fill),
    [state.layers],
  );
```

Replace `addLayer` with the camera-aware version:

```tsx
  const addLayer = useCallback(
    (kind: LayerKind, collection: StacCollection) => {
      // The FIRST layer moves the camera to what was just added; after that
      // the camera is the viewer's (spec §4.6).
      const isFirst = state.layers.length === 0;
      dispatch({
        type: "add",
        layer: {
          id: `layer-${nextLayerId.current++}`,
          kind,
          sourceId: collection.id,
          title: collection.title ?? collection.id,
          visible: true,
          opacity: 1,
        },
      });
      const bbox = collection.extent?.spatial?.bbox?.[0];
      if (isFirst && bbox) {
        mapRef.current?.fitBounds(bboxToLngLatBounds(bbox), { padding: 50 });
      }
    },
    [state.layers.length],
  );
```

and wire the map:

```tsx
        <div className="relative min-h-0 flex-1">
          <StacMap
            className="h-full w-full"
            onMapRef={onMapRef}
            interactiveLayerIds={interactiveLayerIds}
            onMouseMove={onMouseMove}
            onMouseLeave={clearHover}
            onClick={onClick}
            cursor={hovered ? "pointer" : undefined}
          >
            {drawn.map(({ layer, beforeId }) =>
              layer.kind === "footprints" ? (
                <FootprintsMapLayer
                  key={layer.id}
                  layer={layer}
                  catalogUrl={catalogUrl}
                  frameSpan={state.frameSpan}
                  beforeId={beforeId}
                />
              ) : null,
            )}
            {/* V-3 renders imagery layers (RasterFrameStack) and V-4 vector
                layers (VectorTileLayer) from the same list; both chain their
                beforeId through the same helper. */}
          </StacMap>
          {hovered && <MapTooltip {...hovered} />}
        </div>
```

Note for the executor: `setFeatureState` and `fitBounds` are only reachable through a real `MapRef`, which jsdom never produces (the mocked `Map` never fires `onLoad`), so `mapRef.current` stays `null` in the unit tests and every call above is optional-chained. Those two behaviours are covered by V-3's live check on the standing GOES demo, not by vitest.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/map-page.test.tsx`
Expected: PASS (15 tests).

- [ ] **Step 5: Type-check**

Run: `cd app && npm run check`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add app/src/components/map/ app/src/__tests__/map-page.test.tsx
git commit -m "feat(map): footprint hover tooltip, click-through and first-add camera fit (V-2, spec §4.6)"
```

---

### Task 10: E2E smoke, docs, verify, record, merge

Spec §6's V-2 e2e row. The spec's "a footprint source under that layer's namespaced id" cannot be asserted from Playwright — maplibre's style is internal to the canvas and the page exposes no hook for it — so the browser test asserts the DOM contract (the route, the sidebar entry, the picker, the row) and the source id stays covered by the component test in Task 7.

**Files:**
- Create: `app/e2e/map.spec.ts`
- Modify: `docs/FEATURES.md` (one line under the serving entries)
- Modify: `TODO.md` (the V-2 checkbox and the V row of the queue table at line 19)

- [ ] **Step 1: Write the e2e spec**

`app/e2e/map.spec.ts`:

```ts
import { test, expect } from "@playwright/test";

/**
 * UI-surface e2e for /map (V-2, spec §4/§6). Needs only the pgstac-backed
 * STAC API the Docker stack serves as the built-in catalog — the same
 * precondition extensions.spec.ts and proxy.spec.ts already assume.
 *
 * Imagery (titiler) and vector tiles (tipg) are live-only paths and belong to
 * V-3/V-4; nothing here touches a tile server.
 *
 * maplibre's layer list lives inside the canvas and is not readable from the
 * page, so this asserts the DOM contract only. That a footprints layer mounts
 * a source under its namespaced id is covered by map-page.test.tsx.
 */
test.describe("Map page", () => {
  test("is reachable from the sidebar and starts empty", async ({ page }) => {
    await page.goto("/monitoring");
    await page.getByRole("link", { name: "Map" }).click();

    await expect(page).toHaveURL(/\/map$/);
    await expect(page.getByTestId("map-layer-panel")).toBeVisible();
    await expect(page.getByText("No layers yet")).toBeVisible();
    await expect(page.getByTestId("map-layer-row")).toHaveCount(0);
  });

  test("lists the built-in catalog's products and adds one as footprints", async ({
    page,
  }) => {
    await page.goto("/map");
    await page.getByTestId("map-add-layer").click();

    // The stack's built-in catalog carries the demo products; an empty
    // catalog would make this assertion, not the page, the thing that failed.
    const options = page.locator('[data-testid^="map-add-footprints-"]');
    await expect(options.first()).toBeVisible();

    await options.first().click();

    const row = page.getByTestId("map-layer-row");
    await expect(row).toHaveCount(1);
    await expect(row.getByTestId("map-layer-visible")).toBeVisible();
    await expect(row.getByTestId("map-layer-remove")).toBeVisible();
    // No hover has happened, so no tooltip — and no error surface anywhere
    // on the page (spec §4.7).
    await expect(page.getByTestId("map-tooltip")).toHaveCount(0);

    // Adding the same product again is offered as "Added" and disabled.
    await page.getByTestId("map-add-layer").click();
    await expect(options.first()).toBeDisabled();
  });

  test("removes the layer it added", async ({ page }) => {
    await page.goto("/map");
    await page.getByTestId("map-add-layer").click();
    await page.locator('[data-testid^="map-add-footprints-"]').first().click();
    await expect(page.getByTestId("map-layer-row")).toHaveCount(1);

    await page.getByTestId("map-layer-remove").click();

    await expect(page.getByTestId("map-layer-row")).toHaveCount(0);
    await expect(page.getByText("No layers yet")).toBeVisible();
  });
});
```

- [ ] **Step 2: Full gate**

Run from the worktree root: `npm run verify`
Expected: app-scoped typecheck, build and unit tests all green. Fix anything reported on this branch before continuing.

- [ ] **Step 3: Confirm the four preview test files are untouched**

Run: `git diff ai/main --stat -- app/src/__tests__/collection-preview-tab.test.tsx app/src/__tests__/collection-preview-frames.test.ts app/src/__tests__/raster-tile-layer.test.tsx app/src/__tests__/time-slider.test.tsx`
Expected: no output. (`raster-frame-stack.test.tsx` HAS changed — deliberately, with the reason in Task 2's commit.)

- [ ] **Step 4: Record**

`docs/FEATURES.md` — add one row directly beneath the "Collection raster preview (Preview tab)" row (line 576). V-4 owns the full documentation of this feature; this is the placeholder that keeps the page discoverable:

```markdown
| Map page (`/map`) | ✅ | V-2 (2026-09-07), spec `docs/superpowers/specs/2026-09-04-map-page-design.md`. `app/src/pages/map.astro` + `app/src/components/map/` — built-in-catalog products added as map layers from an Add-layer popover, with a layer list (visibility, opacity, order, remove), footprint hover tooltips and click-through to the item page. Layer state is the `app/src/lib/map/state.ts` reducer; draw order is chained through each layer's stable bottom layer id (a footprints fill, a raster stack's `${id}-anchor`), never a raster frame. Imagery + the shared time axis are V-3, tipg vector layers and the full docs are V-4 |
```

`TODO.md`:
- Tick the V-2 item: `- [x] **V-2 · Page shell, state, footprint layers.** …`
- Update the V row of the queue table (line 19) to: `Spec **approved 2026-09-04**. V-1 merged 2026-09-04, V-2 merged 2026-09-07; **V-3 next**; V-4 depends on V-2 only. No migrations`

```bash
git add docs/FEATURES.md TODO.md app/e2e/map.spec.ts
git commit -m "docs: V-2 done — /map page shell, layer state, footprint layers"
```

- [ ] **Step 5: Merge**

```bash
cd <repo root>
git checkout ai/main
git merge ai/v2-map-page --no-ff -m "Merge ai/v2-map-page: V-2 map page shell, state and footprint layers"
npm run verify
```

- [ ] **Step 6: Lead-only — the e2e run**

On `ai/main`, with the Docker stack up (`docker compose up -d`; pgstac on :8082, built-in catalog on :8081), read the `run-e2e` skill and then:

Run: `cd app && npm run test:e2e:ci -- map`
Expected: the three `map.spec.ts` tests pass. If the built-in catalog has no products, seed the demo first (`cd services/pipeline && uv run python -m pipeline.demo seed`) rather than weakening the spec.

- [ ] **Step 7: Clean up**

```bash
git worktree remove .claude/worktrees/v2-map-page
git branch -d ai/v2-map-page
```

Do not push `ai/main` — it stays local by the lead's standing instruction; a human promotes it via a PR.

---

## Self-review

**Spec coverage:**

- **§4.1 route and shell** — `map.astro` + `MapPage` island under `AppShell`, sidebar entry after Products, `ROUTE_TITLES` regex, panel/map/reserved-dock layout: Task 6. ✓
- **§4.2 state** — the verbatim `LayerKind` / `MapLayer` / `MapState` shapes, all eight actions including `setAsset`, `setFrameSpan` (resets `axisIndex`) and `setAxisIndex`, duplicate `(kind, sourceId)` refused, client-generated session-stable ids: Task 4. The ids come in through the action so the reducer stays pure; the counter lives in `MapPage` (Task 7). ✓
- **§4.3 data per layer, footprints half** — `useItems(catalogUrl, collectionId, { limit: frameSpan, sortby: "-datetime" })` per layer in `FootprintsMapLayer`, shared through the TanStack key with the row's own status query: Task 7. The spec's "footprints show their frame's items" is §4.4 and explicitly deferred to V-3 in the component's own docstring — V-2 draws the whole span. ✓
- **§4.5 layer list** — topmost first, kind icon, title, `Eye`/`EyeOff`, opacity `Slider`, move up/down, remove, the muted "no items with a timestamp" line, `EmptyState` when empty: Tasks 6-8. Products section with Footprints per collection and "Added"/disabled: Task 7. Imagery and Vector tiles are marked extension points with no dead UI. `beforeId` chaining so nothing covers the layer above it: Tasks 4 (`beforeIdFor`) + 7 (topmost-first render). ✓
- **§4.6 map interaction** — `hover` feature-state set and cleared, tooltip with item id and datetime placed from `e.point`, click → `/collections/{c}/items/{i}` in a new tab, `interactiveLayerIds` recomputed from the visible footprint layers, first-add `fitBounds` with `padding: 50` and nothing after: Task 9. ✓
- **§4.7 degradation** — no error state anywhere: an empty collection list is a sentence in the popover, a layer with no timestamped items is a muted line, a null catalog is an empty `catalogUrl` that disables the queries. Asserted in Tasks 6, 7 and the e2e. ✓
- **§6 testing (V-2 rows)** — reducer unit tests (add / duplicate / remove / move at the ends / visible / opacity / asset / span resets axis / setAxisIndex) and `clamp01`: Tasks 1 and 4. `visible` on each of the three layer components and the anchor's presence and position: Tasks 2-3. `MapPage` component tests in the `collection-preview-tab.test.tsx` mocking style: Tasks 6-9. `e2e/map.spec.ts`, pgstac only: Task 10. Storybook: none required in V-2, the two V-1 stories stand. ✓
- **§9** — reducer in the island (no nanostore), no drag-and-drop: Tasks 4 and 8. ✓
- **§11.1** — `visible` on `FootprintLayer`, `RasterFrameStack` (threaded into `RasterTileLayer`) and `VectorTileLayer`, `visibility: "none"` on every owned layer, no unmount: Task 3. ✓
- **§11.2** — `rasterFrameStackAnchorId`, the always-mounted anchor as the stack's bottom: Task 2, with the deliberate refinement from "empty-tile layer" to a sourceless `background` layer stated in the task. ✓
- **§11 opacity clamp** — `clamp01` once in `styles.ts`, applied by the row through `opacityFromSlider`, no clamping inside the components: Tasks 1, 4, 8. ✓
- **§11.3** (backward-step stacking, I-112) is V-3's and is not touched here. **§4.4** (axis) and the imagery half of §4.3/§4.5 are V-3; the tipg section of §4.5 is V-4. ✓

**Placeholder scan:** no "TBD", no "similar to Task N", no "add tests" without the test code. Every step that changes code shows the code; every test step shows the assertions. The one intentionally unspecified value is the commit trailer's session URL, which only the executing session knows — flagged as such in Global Constraints.

**Type consistency:** `clamp01` (Task 1) is the function `opacityFromSlider` (Task 4) calls and the one the row uses (Task 8). `rasterFrameStackAnchorId` (Task 2) is what `layerAnchorId` (Task 4) returns for `"imagery"`, and the anchor test's `"s-anchor"` matches the reducer test's `"a-anchor"`. `footprintLayerIds(id).fill` is the same expression in `layerAnchorId` and in `interactiveLayerIds` (Task 9), and both produce the `${id}-fill` the `beforeId` assertions in Tasks 7-8 expect. `MapLayer` / `FrameSpan` / `LayerKind` flow unchanged from Task 4 into `LayerPanelProps`, `LayerRowProps` and `FootprintsMapLayerProps`. `visible` is the prop name in all four components, the reducer field and the panel callback (`onVisibleChange`). `MapMouseEvent` is imported from `react-map-gl/maplibre` in both `StacMap` (Task 5) and `MapPage` (Task 9). The `mapProps` capture helper has the same shape in `stac-map.test.tsx` and `map-page.test.tsx`.
