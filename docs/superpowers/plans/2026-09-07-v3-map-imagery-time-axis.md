# V-3 · Imagery Layers + the Shared Time Axis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The `/map` page draws titiler-pgstac imagery layers beside footprint layers, and one docked time bar drives every time-aware layer through the union of their frames — with the backward-step draw-order bug (I-112) fixed inside `RasterFrameStack`.

**Architecture:** Each STAC layer keeps its own frames (`buildPreviewFrames` over its `useItems` query, exactly as the collection Preview tab does). A pure `lib/map/axis.ts` turns the layers' frame maps into one sorted, deduped tick list and resolves each layer's own frame index for a tick by "hold last". Frames flow UP from each layer's view component to the page (which owns the axis); the chosen tick flows DOWN. Inside `RasterFrameStack`, the mounted frames stop relying on React child order for maplibre draw order and chain `beforeId` explicitly — the one thing that makes react-map-gl call `moveLayer` on an already-mounted layer.

**Tech Stack:** React 19, TanStack Query v5, `react-map-gl/maplibre` 8, MapLibre GL 5, vitest + Testing Library (app tests in `app/src/__tests__/`, importing shared components from `@stac-higher/shared`).

**Spec:** `docs/superpowers/specs/2026-09-04-map-page-design.md` — §4.3 (imagery half), §4.4 (the shared time axis), §4.5 (the imagery option's gate, the per-row asset select), §5, §6 (the V-3 rows), §11.3 (the I-112 fix). Queue text and the carried-forward V-3 constraints: `TODO.md` lines 619–703. Issue closed: `docs/ISSUES.md` I-112.

## Global Constraints

- **Worktree**: `git worktree add .claude/worktrees/v3-map-axis -b ai/v3-map-axis ai/main`, then `npm install` at the worktree root (single lockfile, workspace symlinks).
- **Gate**: `npm run verify` from the worktree root — it must pass before this slice is declared done. One file: `cd app && npx vitest run src/__tests__/<file>`.
- **Teammates never run e2e, the dev server or Docker.** The live check (Task 7) is the lead's, on `ai/main`, after merging.
- **V-3 starts from `ai/main` with V-2 merged.** Everything under "V-2 facts" below exists, exactly as `docs/superpowers/plans/2026-09-07-v2-map-page-shell.md` specifies it — that plan is the authority on the code this slice edits, and every "current state" quoted below is copied from it. Where a step edits a file V-2 created, the code being replaced is quoted rather than located by line number (V-2's line numbers depend on how its own tasks landed); where a step edits a file that exists on `ai/main` today, current line numbers are given.
- Shared components live in `packages/shared/src/components/map/` and are exported from `packages/shared/src/index.ts`; the app imports them from `@stac-higher/shared`, never by relative path. Inside the shared package use `@shared/*`.
- **Never edit `packages/shared/src/components/ui/*` or `app/src/components/ui/*`** — a PreToolUse hook blocks it. New primitives come from `npx shadcn@latest add <component>`. Everything this slice needs (`Button`, `Select`, `Slider`, `Popover`) already exists.
- The `astro check` PostToolUse hook runs after every `.ts`/`.tsx` edit; fix what it reports before moving on.
- **No new dependencies.**
- The tile-URL rules from spec §3 are not touched: the `datetime` string is passed to the tiler VERBATIM (never normalised), the lookahead stays exactly one frame, opacity swaps have no transition, and frames stay gated behind `hintSettled` (react-map-gl applies one changed source key per render and warns on the rest, and a maplibre source's zoom range is fixed at creation).
- **Test files that must pass UNCHANGED**: `app/src/__tests__/collection-preview-frames.test.ts`, `app/src/__tests__/raster-tile-layer.test.tsx`, `app/src/__tests__/time-slider.test.tsx`.
- `app/src/__tests__/collection-preview-tab.test.tsx` changes ONLY where the I-112 fix inverts React tree order (its `layers()` helper, plus one added `beforeId` assertion) — with the reason in the commit message. `app/src/__tests__/raster-frame-stack.test.tsx` changes only for the chain/`beforeId`/anchor assertions and its helpers.
- Commit messages end with the executing session's attribution trailer:
  ```
  Co-Authored-By: Claude <noreply@anthropic.com>
  Claude-Session: <this session's URL>
  ```
  (the executor fills in its own model name and session URL).

### V-2 facts this plan builds on

Names below are exact; V-3 consumes them and introduces no synonym.

**`app/src/lib/map/state.ts`** (V-2 Task 4) exports:
```ts
export type LayerKind = "footprints" | "imagery" | "vector";
export interface MapLayer { id: string; kind: LayerKind; sourceId: string; title: string;
                            asset?: string; visible: boolean; opacity: number }
export type FrameSpan = 25 | 50 | 100 | 200;
export const FRAME_SPANS: readonly FrameSpan[];
export const DEFAULT_FRAME_SPAN: FrameSpan;               // 50
export interface MapState { layers: MapLayer[]; frameSpan: FrameSpan; axisIndex: number | null }
export const INITIAL_MAP_STATE: MapState;
export type MapAction =
  | { type: "add"; layer: MapLayer } | { type: "remove"; id: string }
  | { type: "move"; id: string; direction: "up" | "down" }
  | { type: "setVisible"; id: string; visible: boolean }
  | { type: "setOpacity"; id: string; opacity: number }
  | { type: "setAsset"; id: string; asset: string }
  | { type: "setFrameSpan"; frameSpan: FrameSpan }        // resets axisIndex to null
  | { type: "setAxisIndex"; axisIndex: number | null };
export function mapReducer(state: MapState, action: MapAction): MapState;
export function layerAnchorId(layer: MapLayer): string;   // imagery → `${id}-anchor`,
                                                          // footprints/vector → `${id}-fill`
export function beforeIdFor(layers: MapLayer[], index: number): string | undefined;
export function opacityFromSlider(values: number[]): number;
```
`layers` is in DRAW order, bottom first; `add` refuses a duplicate `(kind, sourceId)`. **V-3 adds no helper here** — `layerAnchorId` and `beforeIdFor` are the chain.

**Components** (`app/src/components/map/`): `MapPage.tsx` (island in `<AppShell>`, holds `useReducer(mapReducer, INITIAL_MAP_STATE)` as `[state, dispatch]`, a `mapRef` + `onMapRef` from V-2 Task 9, and `drawn`), `LayerPanel.tsx`, `LayerRow.tsx`, `AddLayerPopover.tsx`, `FootprintsMapLayer.tsx`, `MapTooltip.tsx`. Props:
```ts
AddLayerPopoverProps { collections: StacCollection[];
                       isAdded: (kind: LayerKind, sourceId: string) => boolean;
                       onAdd: (kind: LayerKind, collection: StacCollection) => void }
FootprintsMapLayerProps { layer: MapLayer; catalogUrl: string; frameSpan: number; beforeId?: string }
LayerRowProps { layer: MapLayer; catalogUrl: string; frameSpan: number;
                canMoveUp: boolean; canMoveDown: boolean;
                onVisibleChange: (visible: boolean) => void;
                onOpacityChange: (opacity: number) => void;
                onMove: (direction: "up" | "down") => void; onRemove: () => void }
```
`LayerRow` also holds `const KIND_ICON = { footprints: Layers } as const;` (with a comment reserving `Image` for V-3) and a hook-bearing child `FootprintStatusLine` that already renders the muted **"no items with a timestamp"** line. Test ids: `map-add-layer`, `map-add-footprints-${collectionId}`, `map-layer-row` (carrying `data-layer-id`), `map-layer-visible`, `map-layer-opacity`, `map-layer-up`, `map-layer-down`, `map-layer-remove`, `map-tooltip`, `map-layer-panel`.

**Draw order**: `MapPage` computes
```tsx
const drawn = useMemo(
  () => state.layers
    .map((layer, index) => ({ layer, beforeId: beforeIdFor(state.layers, index) }))
    .reverse(),
  [state.layers],
);
```
and renders `drawn` inside `<StacMap>` — topmost first, because react-map-gl creates layers during render in tree order and maplibre drops an `addLayer` whose `beforeId` is not in the style yet.

**Shared package**: `visible?: boolean` on `FootprintLayer`, `RasterTileLayer`, `RasterFrameStack` and `VectorTileLayer` (`layout: { visibility: "visible" | "none" }`, always sent explicitly); `rasterFrameStackAnchorId(id)` = `${id}-anchor`, a `background` layer with `paint: { "background-opacity": 0 }`, no source, **no `layout`** (the anchor is never hidden), rendered as the FIRST child of the stack and present even for an empty series; `clamp01` in `packages/shared/src/lib/map/styles.ts`. `StacMap` has `onMapRef`, `interactiveLayerIds`, `onMouseMove`, `onMouseLeave`, `onClick`, `cursor`. A stack's frame source ids are `${layerId}-frame-${n}`; `RasterTileLayer` names that source's layer `${layerId}-frame-${n}-layer`.

**V-2 test files** (V-3 edits only what is listed in each task): `map-page.test.tsx`, `map-state.test.ts`, `map-styles.test.ts`, `stac-map.test.tsx`, `map-page.spec.ts`-equivalent e2e `app/e2e/map.spec.ts`, and the modified `raster-frame-stack.test.tsx`, `footprint-layer.test.tsx`, `vector-tile-layer.test.tsx`. `map-page.test.tsx` mocks `@/components/layout/AppShell`, `@/stores/catalogStore`, `@/lib/query/collections` (`useCollections(catalogUrl)` → `{ data: { collections } }`), `@/lib/query/items`, and `react-map-gl/maplibre` (recording the Map's props in `mapProps.current`).

---

### Task 1: The shared time axis — `lib/map/axis.ts`

**Files:**
- Create: `app/src/lib/map/axis.ts`
- Modify: `app/src/lib/serving/frames.ts:41-45` (make the label formatter exported and named) and `:69` (its one caller)
- Test: `app/src/__tests__/map-axis.test.ts` (new)

**Interfaces:**
- Consumes: `PreviewFrame { datetime: string; label: string; itemIds: string[] }` and `buildPreviewFrames(items: StacItem[]): PreviewFrame[]` from `@/lib/serving/frames` (frames are oldest-first and already sorted).
- Produces:
  ```ts
  // app/src/lib/serving/frames.ts
  export function formatFrameLabel(ms: number): string;   // `2026-09-04 06:11 UTC`

  // app/src/lib/map/axis.ts
  export interface AxisTick { instant: number; label: string }
  export function frameInstant(frame: PreviewFrame): number;
  export function buildAxis(layerFrames: Map<string, PreviewFrame[]>): AxisTick[];
  export function resolveLayerFrame(tickInstant: number, frames: PreviewFrame[]): number | null;
  ```

- [ ] **Step 1: Write the failing test**

Create `app/src/__tests__/map-axis.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { buildAxis, frameInstant, resolveLayerFrame } from "@/lib/map/axis";
import { buildPreviewFrames, type PreviewFrame } from "@/lib/serving/frames";
import type { StacItem } from "@/lib/stac-api/types";

/** A frame as `buildPreviewFrames` would emit it, without going through items. */
function frame(datetime: string, itemIds: string[] = ["i"]): PreviewFrame {
  const start = datetime.includes("/") ? datetime.split("/")[0] : datetime;
  const iso = new Date(Date.parse(start)).toISOString();
  return { datetime, label: `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`, itemIds };
}

const GEOCOLOR = [
  frame("2026-09-04T06:00:00Z"),
  frame("2026-09-04T06:05:00Z"),
  frame("2026-09-04T06:10:00Z"),
];
// A slower product, offset from the fast one, sharing exactly one instant.
const MCMIPC = [frame("2026-09-04T06:05:00Z"), frame("2026-09-04T06:20:00Z")];

describe("buildAxis", () => {
  it("is the sorted union of every layer's frame instants", () => {
    const axis = buildAxis(
      new Map([
        ["b", MCMIPC],
        ["a", GEOCOLOR],
      ]),
    );

    expect(axis.map((t) => t.label)).toEqual([
      "2026-09-04 06:00 UTC",
      "2026-09-04 06:05 UTC",
      "2026-09-04 06:10 UTC",
      "2026-09-04 06:20 UTC",
    ]);
    expect(axis.map((t) => t.instant)).toEqual([...axis].sort((x, y) => x.instant - y.instant).map((t) => t.instant));
  });

  it("collapses an instant two layers share into one tick", () => {
    const axis = buildAxis(new Map([["a", GEOCOLOR], ["b", MCMIPC]]));
    const shared = axis.filter((t) => t.instant === Date.parse("2026-09-04T06:05:00Z"));

    expect(shared).toHaveLength(1);
  });

  it("labels a tick exactly as buildPreviewFrames labels its frame", () => {
    // The bar reads one label; two spellings of the same instant would look
    // like two products disagreeing about the time.
    const items: StacItem[] = [
      {
        type: "Feature",
        stac_version: "1.0.0",
        id: "a",
        collection: "goes-geocolor",
        geometry: null,
        properties: { datetime: "2026-09-04T06:11:17.300000Z" },
        links: [],
        assets: {},
      },
    ];
    const frames = buildPreviewFrames(items);
    const [tick] = buildAxis(new Map([["a", frames]]));

    expect(tick.label).toBe(frames[0].label);
  });

  it("takes the START of an interval-form datetime", () => {
    const [tick] = buildAxis(
      new Map([["a", [frame("2026-09-04T06:00:00Z/2026-09-04T06:05:00Z")]]]),
    );

    expect(tick.instant).toBe(Date.parse("2026-09-04T06:00:00Z"));
    expect(tick.label).toBe("2026-09-04 06:00 UTC");
  });

  it("is empty for no layers and for layers with no frames", () => {
    expect(buildAxis(new Map())).toEqual([]);
    expect(buildAxis(new Map([["a", []]]))).toEqual([]);
  });
});

describe("frameInstant", () => {
  it("parses a plain instant and an interval start alike", () => {
    expect(frameInstant(frame("2026-09-04T06:00:00Z"))).toBe(Date.parse("2026-09-04T06:00:00Z"));
    expect(frameInstant(frame("2026-09-04T06:00:00Z/2026-09-04T06:05:00Z"))).toBe(
      Date.parse("2026-09-04T06:00:00Z"),
    );
  });
});

describe("resolveLayerFrame", () => {
  it("reproduces a single layer's own indices when the axis is its own frames", () => {
    const axis = buildAxis(new Map([["a", GEOCOLOR]]));

    expect(axis.map((t) => resolveLayerFrame(t.instant, GEOCOLOR))).toEqual([0, 1, 2]);
  });

  it("holds the last frame at or before the tick", () => {
    // The union's 06:00 and 06:10 ticks are not this layer's; the slow product
    // keeps showing 06:05 until its own next frame arrives.
    const axis = buildAxis(new Map([["a", GEOCOLOR], ["b", MCMIPC]]));

    expect(axis.map((t) => resolveLayerFrame(t.instant, MCMIPC))).toEqual([null, 0, 0, 1]);
  });

  it("returns null when the layer has nothing that early", () => {
    expect(resolveLayerFrame(Date.parse("2026-09-04T05:59:00Z"), GEOCOLOR)).toBeNull();
  });

  it("returns null for an empty series", () => {
    expect(resolveLayerFrame(Date.parse("2026-09-04T06:00:00Z"), [])).toBeNull();
  });

  it("never returns an index outside the series", () => {
    // A stack handed frames[-1] throws and frames[count] paints everything at
    // zero opacity, which reads as "the tiler is down".
    for (const instant of [0, Date.parse("2999-01-01T00:00:00Z")]) {
      const resolved = resolveLayerFrame(instant, GEOCOLOR);
      expect(resolved === null || (resolved >= 0 && resolved < GEOCOLOR.length)).toBe(true);
    }
    expect(resolveLayerFrame(Date.parse("2999-01-01T00:00:00Z"), GEOCOLOR)).toBe(2);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-axis.test.ts`
Expected: FAIL — `Failed to resolve import "@/lib/map/axis"`.

- [ ] **Step 3: Export the label formatter from `frames.ts`**

In `app/src/lib/serving/frames.ts`, rename the private `formatLabel` (lines 41-45) and export it, so the axis cannot drift from the frames it is built out of:

```ts
/**
 * `2026-09-04 06:11 UTC` — minutes are the finest cadence worth reading.
 *
 * Exported because the /map page's shared axis (`lib/map/axis.ts`) labels its
 * ticks with it: a tick and the frame it came from must read identically.
 */
export function formatFrameLabel(ms: number): string {
  const iso = new Date(ms).toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}
```

and update its one caller (line 69) to `label: formatFrameLabel(start)`.

- [ ] **Step 4: Write `axis.ts`**

Create `app/src/lib/map/axis.ts`:

```ts
/**
 * The /map page's shared time axis (spec §4.4).
 *
 * Every time-aware layer keeps its own frames — products have their own
 * cadences and an axis taken from one "primary" layer breaks the moment a
 * second product is added. The page's axis is the UNION of their frame
 * instants; each layer then resolves its own frame for the chosen tick by
 * holding its last frame at or before it.
 *
 * Pure: no React, no fetching, no notion of layers beyond their frames.
 */
import { formatFrameLabel, type PreviewFrame } from "@/lib/serving/frames";

export interface AxisTick {
  /** Epoch ms — the identity of the tick. */
  instant: number;
  /** What the time bar reads out; formatted exactly as a frame's label is. */
  label: string;
}

/**
 * The instant a frame starts. A frame's `datetime` is the tiler's filter
 * string, which is either an instant or a `start/end` interval (STAC's ranged
 * form); the start is what it sorts and labels by, in both cases.
 */
export function frameInstant(frame: PreviewFrame): number {
  const slash = frame.datetime.indexOf("/");
  return Date.parse(slash === -1 ? frame.datetime : frame.datetime.slice(0, slash));
}

/**
 * One tick per distinct frame instant across all time-aware layers, oldest
 * first. Two layers whose items share an instant produce one tick.
 */
export function buildAxis(layerFrames: Map<string, PreviewFrame[]>): AxisTick[] {
  const byInstant = new Map<number, AxisTick>();

  for (const frames of layerFrames.values()) {
    for (const frame of frames) {
      const instant = frameInstant(frame);
      // buildPreviewFrames already drops unparseable times; belt and braces so
      // a NaN can never sort into the axis and poison every comparison.
      if (Number.isNaN(instant)) continue;
      if (!byInstant.has(instant)) {
        byInstant.set(instant, { instant, label: formatFrameLabel(instant) });
      }
    }
  }

  return [...byInstant.values()].sort((a, b) => a.instant - b.instant);
}

/**
 * The index of the layer's newest frame at or before `tickInstant` — "hold
 * last", so a slow product stays on screen through a fast one's ticks — or
 * `null` when the layer has nothing that early and must draw nothing.
 *
 * Binary search: frames are already sorted oldest-first by
 * `buildPreviewFrames`, and this runs once per layer per tick during playback.
 * The result is always a valid index into `frames` or `null`; a stack handed a
 * negative index throws and one handed an index past the end paints every
 * frame at zero opacity, which reads as a dead tile server.
 */
export function resolveLayerFrame(
  tickInstant: number,
  frames: PreviewFrame[],
): number | null {
  let low = 0;
  let high = frames.length - 1;
  let found: number | null = null;

  while (low <= high) {
    const mid = (low + high) >> 1;
    if (frameInstant(frames[mid]) <= tickInstant) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }

  return found;
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/map-axis.test.ts`
Expected: PASS (11 tests).

- [ ] **Step 6: Prove the preview's own frame tests still pass**

Run: `cd app && npx vitest run src/__tests__/collection-preview-frames.test.ts`
Expected: PASS, unchanged — the formatter was renamed, not changed.

- [ ] **Step 7: Commit**

```bash
git add app/src/lib/map/axis.ts app/src/lib/serving/frames.ts app/src/__tests__/map-axis.test.ts
git commit -m "feat(map): shared time axis — buildAxis + resolveLayerFrame (V-3)"
```

---

### Task 2: Chain `beforeId` inside `RasterFrameStack` (I-112)

**Files:**
- Modify: `packages/shared/src/components/map/RasterFrameStack.tsx` — the body from `const anchor = (` to the end of the component (V-2 Task 2 rewrote it; V-2 Task 3 threaded `visible` into each frame). The quoted "current state" in Step 3 is that exact code.
- Test: `app/src/__tests__/raster-frame-stack.test.tsx` — the `layers()` helper (V-2 Task 2 replaced it with `allLayers()` + a raster-filtered `layers()`); V-2's "mounts the anchor first, beneath the frames, at zero opacity" test; V-2 Task 3's "hides every mounted frame when not visible, and leaves the anchor alone"; the original "passes bounds, zoom limits and beforeId to every frame"; plus one new I-112 test.
- Test: `app/src/__tests__/collection-preview-tab.test.tsx` — the `layers()` helper (lines 80-88, untouched by V-2) and the "keeps the previous frame painted beneath the current one" test (lines 162-172).

**Interfaces:**
- Consumes: `rasterFrameStackAnchorId(id: string): string` (= `${id}-anchor`, V-2) and `RasterTileLayer`'s id contract — given `id={X}` it mounts source `X` and layer `${X}-layer`.
- Produces: no signature change. `RasterFrameStackProps` keeps `{ id, frames, index, bounds?, minzoom?, maxzoom?, opacity?, visible?, beforeId? }`. Two behaviours become contractual for later tasks:
  - the mounted frames are ordered, in maplibre, anchor (bottom) → previous → current → lookahead (top), by an explicit `beforeId` chain rather than by insertion order — so any reorder is a `moveLayer`;
  - an empty `frames` array still renders only the anchor (V-2's behaviour, kept), now with the caller's `beforeId` as its target, so `rasterFrameStackAnchorId(id)` is a chain target the page can rely on whether or not the layer currently has frames.

**Why the child order inverts, and why the anchor moves.** react-map-gl creates and updates layers DURING RENDER, in tree order, and maplibre fires an error and no-ops an `addLayer` whose `beforeId` names a layer that is not in the style yet. V-2 got its order for free: every frame carried the CALLER's `beforeId`, so each new frame inserted just below that target and above the frames already there, and the anchor — rendered first — stayed lowest. A chain cannot work that way: each frame's target is the frame above it, so the frames must be rendered top-first, and the anchor, whose target is now the bottom-most frame, must be rendered last.

Resulting order and ids for `id="s"`, mounted window `[previous, current, lookahead]` = `[2, 1, 2]` deduped to `[2, 1]` (the classic backward step 1 → 2 → 1), caller `beforeId="top"`:

| Tree position (render order) | Source | Layer id | `beforeId` | maplibre position |
|---|---|---|---|---|
| 1st | `s-frame-1` | `s-frame-1-layer` | `top` (the caller's) | topmost of the stack |
| 2nd | `s-frame-2` | `s-frame-2-layer` | `s-frame-1-layer` | beneath the current frame |
| 3rd (last) | — (background) | `s-anchor` | `s-frame-2-layer` | the stack's bottom |

- [ ] **Step 1: Write the failing tests**

V-2 left this file with `allLayers()` (every rendered layer, tree order) and a raster-filtered `layers()` built on it. V-3 keeps both names and adds the reversal plus two readers. Replace both of V-2's helpers (`allLayers` and `layers`) with the four below; V-2's `Rendered` interface above them is unchanged:

```tsx
function allLayers(): Record<string, unknown>[] {
  return screen.queryAllByTestId("layer").map((el) => JSON.parse(el.dataset.props as string));
}

/**
 * The FRAME layers only, BOTTOM of the stack first.
 *
 * Tree order is now the REVERSE of draw order: the frames chain `beforeId` on
 * each other, react-map-gl creates layers during render in tree order, and
 * maplibre drops an `addLayer` whose target is not in the style yet — so the
 * top frame has to be rendered first (and the anchor last). Reversing here
 * keeps these assertions reading the way the stack draws, bottom to top.
 */
function layers(): Rendered[] {
  return frameProps().map((props) => {
    const paint = props.paint as Record<string, unknown>;
    return {
      source: props.source as string,
      opacity: paint["raster-opacity"] as number,
      transition: paint["raster-opacity-transition"],
    };
  });
}

/** Frame layer props, bottom first. */
function frameProps(): Record<string, unknown>[] {
  return allLayers()
    .filter((props) => props.type === "raster")
    .reverse();
}

/** `[layer id, beforeId]` per frame, bottom first — the draw-order chain. */
function chain(): [string, string | undefined][] {
  return frameProps().map((p) => [p.id as string, p.beforeId as string | undefined]);
}

/** The always-mounted anchor the page chains on (V-2 Task 2, spec §11.2). */
function anchor(): Record<string, unknown> | undefined {
  return allLayers().find((p) => p.type !== "raster");
}
```

`allLayers()` stays in TREE order, because the two tests that read it are about tree position — and both now flip. Replace V-2's "mounts the anchor first, beneath the frames, at zero opacity" test with:

```tsx
  it("mounts the anchor LAST in the tree, and lowest on the map", () => {
    render(<RasterFrameStack id="s" frames={FRAMES} index={0} beforeId="top" />);

    expect(rasterFrameStackAnchorId("s")).toBe("s-anchor");
    // V-2 rendered the anchor first and let every frame carry the caller's
    // beforeId; the chain (I-112) makes each frame's target the frame above
    // it, so the tree runs top-down and the anchor — whose target is the
    // bottom-most frame — must come last or its target would not exist yet.
    const rendered = allLayers();
    expect(rendered[rendered.length - 1]).toMatchObject({
      id: "s-anchor",
      type: "background",
      beforeId: "s-frame-0-layer",
      paint: { "background-opacity": 0 },
    });
    // A background layer takes no source — that is why it can never 404 or
    // sit in the style waiting for empty tiles.
    expect(rendered[rendered.length - 1]).not.toHaveProperty("source");
    // Still the bottom of the stack in maplibre terms: everything above it
    // chains down to it.
    expect(chain()[0]?.[0]).toBe("s-frame-0-layer");
  });
```

and V-2 Task 3's "hides every mounted frame when not visible, and leaves the anchor alone" test — whose `const [anchor, ...frames] = allLayers();` destructuring assumed the anchor was first — with:

```tsx
  it("hides every mounted frame when not visible, and leaves the anchor alone", () => {
    render(<RasterFrameStack id="s" frames={FRAMES} index={1} visible={false} />);

    expect(frameProps().map((l) => l.layout)).toEqual([
      { visibility: "none" },
      { visibility: "none" },
    ]);
    // The anchor paints nothing in either state; hiding it would only risk
    // maplibre dropping it as a chaining target.
    expect(anchor()).not.toHaveProperty("layout");
    expect(screen.queryAllByTestId("source")).toHaveLength(2);
  });
```

Replace the "passes bounds, zoom limits and beforeId to every frame" test (the original, untouched by V-2) with:

```tsx
  it("passes bounds and zoom limits to every frame and chains beforeId downward", () => {
    // Only the TOP frame carries the caller's target; every frame below draws
    // beneath the one above it, and the anchor beneath the lowest frame.
    render(
      <RasterFrameStack
        id="s"
        frames={FRAMES}
        index={0}
        bounds={[-1, -1, 1, 1]}
        minzoom={2}
        maxzoom={7}
        beforeId="top"
      />,
    );

    const sources = screen
      .getAllByTestId("source")
      .map((el) => JSON.parse(el.dataset.props as string));
    for (const s of sources) {
      expect(s).toMatchObject({ bounds: [-1, -1, 1, 1], minzoom: 2, maxzoom: 7 });
    }

    expect(chain()).toEqual([
      ["s-frame-0-layer", "s-frame-1-layer"],
      ["s-frame-1-layer", "top"],
    ]);
    expect(anchor()).toMatchObject({ id: "s-anchor", beforeId: "s-frame-0-layer" });
  });

  it("re-chains a backward step so the previous frame moves back underneath (I-112)", () => {
    // Walking 1 → 2 → 1: frame 2 was added ABOVE frame 1 on the way up and
    // maplibre keeps it there unless something moves it. react-map-gl calls
    // moveLayer only when `beforeId` changes, so the chain has to say it.
    const { rerender } = render(<RasterFrameStack id="s" frames={FRAMES} index={1} beforeId="top" />);
    rerender(<RasterFrameStack id="s" frames={FRAMES} index={2} beforeId="top" />);
    rerender(<RasterFrameStack id="s" frames={FRAMES} index={1} beforeId="top" />);

    expect(layers().map((l) => [l.source, l.opacity])).toEqual([
      ["s-frame-2", 1],
      ["s-frame-1", 1],
    ]);
    expect(chain()).toEqual([
      ["s-frame-2-layer", "s-frame-1-layer"],
      ["s-frame-1-layer", "top"],
    ]);
  });
```

V-2's "keeps the anchor mounted for an empty series, but no sources" test is **not** edited: with no frames there is no chain, the anchor keeps the caller's `beforeId`, and `allLayers().map((l) => l.id)` is still `["s-anchor"]`. Append one assertion to it, because that fallback is what Task 5 relies on:

```tsx
    // With nothing to chain to, the anchor takes the caller's target — which
    // is what lets an imagery layer with no frames stay a chain target.
    expect(anchor()).toMatchObject({ beforeId: undefined });
```

Every other test in the file is untouched: they read `layers()`, which is bottom-first before and after.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd app && npx vitest run src/__tests__/raster-frame-stack.test.tsx`
Expected: FAIL — the chain assertions report every frame carrying `beforeId: "top"` (V-2's behaviour), the anchor is found first in the tree rather than last, and the backward-step test shows `["s-frame-1", …], ["s-frame-2", …]` in the wrong order.

- [ ] **Step 3: Chain the frames inside the stack**

**Current state** (V-2 Task 2's body, with V-2 Task 3's `visible` threaded into each frame) — everything from `const anchor = (` to the end of the component:

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

  const safeIndex = Number.isFinite(index) ? index : 0;
  const current = ((safeIndex % count) + count) % count;
  const previous = ((previousIndex.current % count) + count) % count;

  const mounted = [ /* previous, current, lookahead — deduped */ ];

  return (
    <>
      {anchor}
      {mounted.map((frameIndex) => (
        <RasterTileLayer … beforeId={beforeId} />
      ))}
    </>
  );
```

**Replacement.** Keep the `previousIndex` / `lastIndex` refs at the top of the component untouched, and replace everything from `const anchor = (` to the end with:

```tsx
  const count = frames.length;

  // A non-finite index shows the first frame rather than nothing.
  const safeIndex = Number.isFinite(index) ? index : 0;

  // Normalised into the series so an index from a caller's own arithmetic
  // (a shared time axis, a series that shrank under a stale ref) can never
  // reach past the array: previous and current are both taken modulo the
  // series, which also keeps the previous frame painted when the series
  // shrinks below the index it remembers.
  const current = count === 0 ? 0 : ((safeIndex % count) + count) % count;
  const previous = count === 0 ? 0 : ((previousIndex.current % count) + count) % count;

  // Draw order, bottom to top: previous, current, then the lookahead frames
  // loading invisibly. Deduped — a series shorter than the window would
  // otherwise repeat itself.
  const mounted =
    count === 0
      ? []
      : [
          ...new Set([
            previous,
            ...Array.from(
              { length: RASTER_FRAME_LOOKAHEAD + 1 },
              (_, offset) => (current + offset) % count,
            ),
          ]),
        ];

  // The layer ids RasterTileLayer will mint for that window, bottom first —
  // the links of the chain.
  const layerIds = mounted.map((frameIndex) => `${id}-frame-${frameIndex}-layer`);

  // Draw order is STATED, not inherited from React child order (I-112).
  // maplibre fixes a layer's position at addLayer time and react-map-gl calls
  // moveLayer only when `beforeId` changes, so a step that reorders the window
  // — every backward step — has to change some layer's `beforeId` or the frame
  // left over from the way up keeps painting over the current one.
  //
  // Each frame draws beneath the frame above it; the top frame beneath the
  // caller's target. Rendered TOP FIRST because react-map-gl creates layers
  // during render, in tree order, and maplibre drops a layer whose `beforeId`
  // names a layer that does not exist yet — every target must already be on
  // the map. The anchor, the stack's stable bottom, is rendered last for the
  // same reason.
  const frameLayers = mounted.map((frameIndex, position) => (
    <RasterTileLayer
      key={frames[frameIndex].key}
      id={`${id}-frame-${frameIndex}`}
      tiles={frames[frameIndex].tiles}
      bounds={bounds}
      minzoom={minzoom}
      maxzoom={maxzoom}
      visible={visible}
      opacity={frameIndex === current || frameIndex === previous ? opacity : 0}
      opacityTransitionMs={0}
      beforeId={layerIds[position + 1] ?? beforeId}
    />
  ));
  frameLayers.reverse();

  // Always mounted, even for an empty series: a chaining target that appears
  // only once data arrives is not a chaining target. A `background` layer
  // needs no source and fetches nothing. It carries no `layout`: it paints
  // nothing in either state, and hiding it would only risk maplibre dropping
  // the target the layer below chains to.
  //
  // Rendered LAST because its own target is the bottom-most frame, which must
  // exist by the time it mounts. With no frames it falls back to the caller's
  // target, exactly as V-2 had it.
  return (
    <>
      {frameLayers}
      <Layer
        id={rasterFrameStackAnchorId(id)}
        type="background"
        beforeId={layerIds[0] ?? beforeId}
        paint={{ "background-opacity": 0 }}
      />
    </>
  );
```

Two notes for whoever applies this:

1. **The `count === 0` early return goes away**, but its behaviour does not: with no frames, `mounted` and `layerIds` are empty, `frameLayers` renders nothing, and the anchor is emitted with the caller's `beforeId` — the same output V-2's `if (count === 0) return anchor;` produced.
2. **The anchor keeps V-2's shape exactly** — `type="background"`, `paint: { "background-opacity": 0 }`, no `layout`, no source. Only its position in the tree and its `beforeId` change.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/raster-frame-stack.test.tsx`
Expected: PASS (12 tests) — including everything not edited here: `warms exactly one frame ahead`, `keeps the previous frame painted beneath the current one`, `wraps the lookahead around the end of the series`, `applies the layer opacity to the visible frames only`, the one-frame series, the out-of-range index, the negative/non-finite index, and V-2's empty-series test. They all read `layers()`, which is bottom-first before and after.

- [ ] **Step 5: Update the preview tab's helper and its draw-order assertion**

The preview tab mounts the same stack, so its tree order inverts too. V-2 left this file untouched — its `layers()` reads `paint["raster-opacity"]` off every layer node, so V-2's anchor arrived as `{ source: undefined, opacity: undefined }` and matched no assertion. The chain makes the file's order meaningful again, so the helper is made explicit. In `app/src/__tests__/collection-preview-tab.test.tsx`, replace `layers()` (lines 80-88) with:

```tsx
/**
 * The stack's raster layers, BOTTOM first. Tree order is now top-first — the
 * frames chain `beforeId` on each other and react-map-gl creates layers during
 * render, so each target must exist before its dependant mounts — and the
 * always-mounted anchor is a background layer, not a frame.
 */
function frameProps(): Record<string, unknown>[] {
  return screen
    .queryAllByTestId("layer")
    .map((el) => JSON.parse(el.dataset.props as string) as Record<string, unknown>)
    .filter((p) => p.type === "raster")
    .reverse();
}

function layers(): RenderedLayer[] {
  return frameProps().map((props) => ({
    source: props.source as string,
    opacity: (props.paint as Record<string, number>)["raster-opacity"],
  }));
}
```

and extend the "keeps the previous frame painted beneath the current one" test (lines 162-172) so it asserts the chain, not just the child order — with the chain in place, child order alone no longer decides what maplibre draws:

```tsx
  it("keeps the previous frame painted beneath the current one", () => {
    // Tiles render far slower than a step, so the outgoing frame stays up
    // until the incoming one covers it — otherwise every step flashes empty.
    renderTab();
    fireEvent.click(screen.getByLabelText("Previous frame"));

    const opaque = layers().filter((l) => l.opacity === 1);
    expect(opaque).toHaveLength(2);
    expect(opaque[0].source).toBe("preview-frame-2");
    expect(opaque[1].source).toBe("preview-frame-1");

    // "Beneath" is now stated by the chain rather than implied by child order
    // (I-112): the previous frame draws below the current frame's layer.
    expect(
      frameProps().map((p) => [p.id, p.beforeId]),
    ).toEqual([
      ["preview-frame-2-layer", "preview-frame-1-layer"],
      ["preview-frame-1-layer", undefined],
    ]);
  });
```

(V-2 left this file alone, so `RenderedLayer` and the rest of the helpers are the originals; only `layers()` is replaced, and `frameProps()` is new.)

- [ ] **Step 6: Run the preview suites**

Run: `cd app && npx vitest run src/__tests__/collection-preview-tab.test.tsx src/__tests__/collection-preview-frames.test.ts src/__tests__/raster-tile-layer.test.tsx src/__tests__/time-slider.test.tsx`
Expected: PASS. Only `collection-preview-tab.test.tsx` was edited; the other three must be untouched — confirm with `git diff --stat`.

- [ ] **Step 7: Commit**

```bash
git add packages/shared/src/components/map/RasterFrameStack.tsx \
        app/src/__tests__/raster-frame-stack.test.tsx \
        app/src/__tests__/collection-preview-tab.test.tsx
git commit -m "fix(map): chain beforeId inside RasterFrameStack so a backward step reorders (I-112)

Frames are rendered top-first and each draws beneath the frame above it, with
the anchor last and lowest. react-map-gl creates layers during render in tree
order and maplibre drops a layer whose beforeId target does not exist yet, so
a chain can only be built downward. That inverts React child order, which is
what collection-preview-tab.test.tsx's layers() helper read: it now reverses
to bottom-first and the draw-order test additionally asserts the chain. The
behaviour it protects — two opaque frames, the current one on top — is
unchanged. An empty series now keeps the anchor mounted so the /map page's
beforeId chain always has a target. Permitted by the lead in the V-1 final
review (TODO.md, carried-forward V-3)."
```

---

### Task 3: One data hook per STAC layer — `useLayerData`

**Files:**
- Create: `app/src/components/map/useLayerData.ts`
- Test: `app/src/__tests__/map-layer-data.test.tsx` (new)

**Interfaces:**
- Consumes: `MapLayer` from `@/lib/map/state` (V-2); `useItems` from `@/lib/query/items`; `useCollectionSettings` from `@/lib/collections/settings-client`; `useItemTileJson` + `TileJson` from `@/lib/serving/queries`; `buildPreviewFrames` from `@/lib/serving/frames`; `previewAssetCandidates` from `@/lib/serving/preview`.
- Produces:
  ```ts
  export interface LayerData {
    items: StacItem[];            // newest first, as the catalog returned them
    frames: PreviewFrame[];       // oldest first; stable identity across renders
    candidates: string[];         // tileable asset keys, best first
    asset: string | undefined;    // layer.asset when still offered, else candidates[0]
    hint: TileJson | undefined;   // newest item's TileJSON: bounds + zoom range
    hintSettled: boolean;         // the hint query has settled (succeeded OR failed)
    servingEnabled: boolean;
  }
  export function useLayerData(
    layer: MapLayer,
    catalogUrl: string,
    frameSpan: number,
  ): LayerData;
  ```
  Called by a per-layer component, so its hooks are unconditional. A `footprints` layer gets the same object and simply ignores `hint` / `hintSettled` — the tiler is never asked for one.

- [ ] **Step 1: Write the failing test**

Create `app/src/__tests__/map-layer-data.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import type { MapLayer } from "@/lib/map/state";
import type { StacItem } from "@/lib/stac-api/types";

const { useItemsMock, useCollectionSettingsMock, useItemTileJsonMock } = vi.hoisted(() => ({
  useItemsMock: vi.fn(),
  useCollectionSettingsMock: vi.fn(),
  useItemTileJsonMock: vi.fn(),
}));

vi.mock("@/lib/query/items", () => ({ useItems: (...a: unknown[]) => useItemsMock(...a) }));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: (...a: unknown[]) => useCollectionSettingsMock(...a),
}));
vi.mock("@/lib/serving/queries", () => ({
  useItemTileJson: (...a: unknown[]) => useItemTileJsonMock(...a),
}));

import { useLayerData } from "@/components/map/useLayerData";

function item(id: string, datetime: string): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "goes-geocolor",
    geometry: null,
    properties: { datetime },
    links: [],
    assets: {
      visual: { href: `/api/assets/goes-geocolor/${id}/visual.tif`, roles: ["visual"] },
      cmi: { href: `/api/assets/goes-geocolor/${id}/cmi.tif`, type: "image/tiff" },
    },
  };
}

const ITEMS = [
  item("c", "2026-09-04T06:11:17.300000Z"),
  item("b", "2026-09-04T06:06:17.300000Z"),
  item("a", "2026-09-04T06:01:17.300000Z"),
];

function layer(overrides: Partial<MapLayer> = {}): MapLayer {
  return {
    id: "l1",
    kind: "imagery",
    sourceId: "goes-geocolor",
    title: "GeoColor",
    visible: true,
    opacity: 1,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useItemsMock.mockReturnValue({ data: { features: ITEMS }, isLoading: false });
  useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: true } });
  useItemTileJsonMock.mockReturnValue({ data: { tiles: ["t"], minzoom: 2 }, isFetched: true });
});

describe("useLayerData", () => {
  it("asks the catalog for the page's span, newest first", () => {
    renderHook(() => useLayerData(layer(), "http://localhost:8081", 100));

    expect(useItemsMock).toHaveBeenCalledWith(
      "http://localhost:8081",
      "goes-geocolor",
      expect.objectContaining({ limit: 100, sortby: "-datetime" }),
    );
  });

  it("derives frames oldest-first and the tileable asset keys", () => {
    const { result } = renderHook(() => useLayerData(layer(), "http://localhost:8081", 50));

    expect(result.current.frames.map((f) => f.itemIds[0])).toEqual(["a", "b", "c"]);
    expect(result.current.candidates).toEqual(["visual", "cmi"]);
    expect(result.current.asset).toBe("visual");
  });

  it("keeps the layer's chosen asset while the collection still offers it", () => {
    const { result } = renderHook(() =>
      useLayerData(layer({ asset: "cmi" }), "http://localhost:8081", 50),
    );

    expect(result.current.asset).toBe("cmi");
  });

  it("falls back to the best candidate when the chosen asset is gone", () => {
    const { result } = renderHook(() =>
      useLayerData(layer({ asset: "retired" }), "http://localhost:8081", 50),
    );

    expect(result.current.asset).toBe("visual");
  });

  it("asks the tiler for the zoom hint of the newest item and chosen asset", () => {
    renderHook(() => useLayerData(layer({ asset: "cmi" }), "http://localhost:8081", 50));

    expect(useItemTileJsonMock).toHaveBeenCalledWith("goes-geocolor", "c", "cmi", true);
  });

  it("never asks the tiler for a footprints layer", () => {
    // Footprints need no tiles; a hint request per footprints layer would be
    // one wasted round trip per layer on a page built to hold several.
    const { result } = renderHook(() =>
      useLayerData(layer({ kind: "footprints" }), "http://localhost:8081", 50),
    );

    expect(useItemTileJsonMock).toHaveBeenCalledWith("goes-geocolor", "c", null, false);
    expect(result.current.frames).toHaveLength(3);
  });

  it("asks for no hint when serving is off", () => {
    useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: false } });
    const { result } = renderHook(() => useLayerData(layer(), "http://localhost:8081", 50));

    expect(useItemTileJsonMock).toHaveBeenCalledWith("goes-geocolor", "c", null, false);
    expect(result.current.servingEnabled).toBe(false);
  });

  it("holds frame identity stable across renders", () => {
    // The page stores each layer's frames in state; a fresh array every render
    // would loop the page through setState forever.
    const { result, rerender } = renderHook(() =>
      useLayerData(layer(), "http://localhost:8081", 50),
    );
    const first = result.current.frames;
    rerender();

    expect(result.current.frames).toBe(first);
  });

  it("survives a collection with no items", () => {
    useItemsMock.mockReturnValue({ data: undefined, isLoading: true });
    const { result } = renderHook(() => useLayerData(layer(), "http://localhost:8081", 50));

    expect(result.current.items).toEqual([]);
    expect(result.current.frames).toEqual([]);
    expect(result.current.asset).toBeUndefined();
    expect(useItemTileJsonMock).toHaveBeenCalledWith("goes-geocolor", "", null, true);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-layer-data.test.tsx`
Expected: FAIL — `Failed to resolve import "@/components/map/useLayerData"`.

- [ ] **Step 3: Write the hook**

Create `app/src/components/map/useLayerData.ts`:

```ts
/**
 * Everything one STAC layer of the /map page needs from the network, in one
 * hook (spec §4.3).
 *
 * Footprints and imagery on the same collection ask the same items query and
 * share it through TanStack's key, so a product added twice costs one request.
 * The hook is deliberately unconditional — a footprints layer runs the same
 * hooks and ignores the tiler fields — because React forbids branching on
 * hooks and because a layer's kind is the one thing about it that never
 * changes.
 *
 * Degradation is the preview's (spec §4.7): no serving, no tileable asset, no
 * timestamped items all mean "this layer draws nothing", never an error.
 */
import { useMemo } from "react";
import type { MapLayer } from "@/lib/map/state";
import type { StacItem } from "@/lib/stac-api/types";
import { useItems } from "@/lib/query/items";
import { useCollectionSettings } from "@/lib/collections/settings-client";
import { useItemTileJson, type TileJson } from "@/lib/serving/queries";
import { buildPreviewFrames, type PreviewFrame } from "@/lib/serving/frames";
import { previewAssetCandidates } from "@/lib/serving/preview";

export interface LayerData {
  /** Newest first — the order the catalog returns under `sortby=-datetime`. */
  items: StacItem[];
  /** Oldest first. Stable across renders: the page keeps these in state. */
  frames: PreviewFrame[];
  /** Tileable asset keys across the span, best first. */
  candidates: string[];
  /** The key this layer renders: its own choice while offered, else the best. */
  asset: string | undefined;
  /** The newest item's TileJSON — bounds and zoom range only. */
  hint: TileJson | undefined;
  /** The hint query has SETTLED (succeeded or failed). Frames wait on this. */
  hintSettled: boolean;
  servingEnabled: boolean;
}

export function useLayerData(
  layer: MapLayer,
  catalogUrl: string,
  frameSpan: number,
): LayerData {
  const { data: settings } = useCollectionSettings(layer.sourceId);
  const servingEnabled = settings?.servingEnabled === true;

  const { data } = useItems(catalogUrl, layer.sourceId, {
    limit: frameSpan,
    sortby: "-datetime",
  });
  const items = useMemo(() => data?.features ?? [], [data]);

  const frames = useMemo(() => buildPreviewFrames(items), [items]);
  const candidates = useMemo(() => previewAssetCandidates(items), [items]);
  const asset =
    layer.asset && candidates.includes(layer.asset) ? layer.asset : candidates[0];

  // One TileJSON request, for the newest item, purely to learn the asset's
  // zoom range and footprint: the collection mosaic advertises 0–24 over the
  // whole extent, which would have maplibre oversampling a five-level pyramid.
  // Only imagery layers need it, and only when the product advertises serving.
  const wantsTiles = layer.kind === "imagery" && servingEnabled;
  const { data: hint, isFetched: hintSettled } = useItemTileJson(
    layer.sourceId,
    items[0]?.id ?? "",
    wantsTiles && items.length > 0 ? (asset ?? null) : null,
    wantsTiles,
  );

  return { items, frames, candidates, asset, hint, hintSettled, servingEnabled };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/map-layer-data.test.tsx`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/components/map/useLayerData.ts app/src/__tests__/map-layer-data.test.tsx
git commit -m "feat(map): one items/frames/asset/hint hook per STAC layer (V-3)"
```

---

### Task 4: The Imagery option and the per-row asset select

**Files:**
- Create: `app/src/components/map/AddLayerCollectionRow.tsx` — an EXTRACTION of the `<li>` V-2's `AddLayerPopover` renders per collection. The extraction is what makes the two per-collection hooks legal (a hook cannot be called in a `.map()`).
- Create: `app/src/components/map/LayerAssetSelect.tsx`
- Modify: `app/src/components/map/AddLayerPopover.tsx` (V-2 Task 7 — the `<li>` block becomes the new component; `AddLayerPopoverProps` gains `catalogUrl`)
- Modify: `app/src/components/map/LayerPanel.tsx` (V-2 Task 7/8 — pass its existing `catalogUrl` down to `AddLayerPopover`)
- Modify: `app/src/components/map/LayerRow.tsx` (V-2 Task 7/8 — `KIND_ICON` gains `imagery: Image`; an imagery row renders `LayerAssetSelect`; `LayerRowProps` gains `onAssetChange`)
- Modify: `app/src/components/map/MapPage.tsx` and `LayerPanel.tsx` (thread `onAssetChange` through, in the same shape as V-2's `onVisibleChange`)
- Modify: `app/src/__tests__/map-page.test.tsx` (V-2) — **mocks only, no assertion changes**: `AddLayerCollectionRow` calls `useCollectionSettings` for every listed collection, and V-2's file does not mock that module (nor `@/lib/serving/queries`, which Task 5 pulls in through `MapLayerView`). Add both mocks in Step 3.
- Test: `app/src/__tests__/map-add-layer-row.test.tsx` (new)
- Test: `app/src/__tests__/map-layer-asset-select.test.tsx` (new)

**Interfaces:**
- Consumes: `useLayerData` (Task 3); `useCollectionSettings`; `useItems`; `previewAssetCandidates`; `MapLayer` / `LayerKind` from `@/lib/map/state`; V-2's `AddLayerPopoverProps.isAdded` and `onAdd`.
- Produces:
  ```ts
  // app/src/components/map/AddLayerCollectionRow.tsx
  export const ADD_LAYER_PROBE_LIMIT = 5;
  export interface AddLayerCollectionRowProps {
    collection: StacCollection;
    catalogUrl: string;
    /** V-2's popover predicate, unchanged. */
    isAdded: (kind: LayerKind, sourceId: string) => boolean;
    /** V-2's popover callback, unchanged (it also closes the popover). */
    onAdd: (kind: LayerKind, collection: StacCollection) => void;
  }
  export function AddLayerCollectionRow(props: AddLayerCollectionRowProps): JSX.Element;

  // app/src/components/map/LayerAssetSelect.tsx
  export interface LayerAssetSelectProps {
    layer: MapLayer;
    catalogUrl: string;
    frameSpan: number;
    onAssetChange: (asset: string) => void;
  }
  export function LayerAssetSelect(props: LayerAssetSelectProps): JSX.Element | null;

  // V-2's props, extended:
  // AddLayerPopoverProps gains `catalogUrl: string`
  // LayerRowProps gains    `onAssetChange: (asset: string) => void`
  // LayerPanelProps gains  `onAssetChange: (id: string, asset: string) => void`
  ```
  New test id: `map-add-imagery-${collectionId}` (beside V-2's `map-add-footprints-${collectionId}`, which is kept verbatim).

- [ ] **Step 1: Write the failing test for the picker row**

Create `app/src/__tests__/map-add-layer-row.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { StacCollection, StacItem } from "@/lib/stac-api/types";

const { useItemsMock, useCollectionSettingsMock } = vi.hoisted(() => ({
  useItemsMock: vi.fn(),
  useCollectionSettingsMock: vi.fn(),
}));

vi.mock("@/lib/query/items", () => ({ useItems: (...a: unknown[]) => useItemsMock(...a) }));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: (...a: unknown[]) => useCollectionSettingsMock(...a),
}));

import {
  AddLayerCollectionRow,
  ADD_LAYER_PROBE_LIMIT,
} from "@/components/map/AddLayerCollectionRow";

const COLLECTION = {
  type: "Collection",
  stac_version: "1.0.0",
  id: "goes-geocolor",
  title: "GOES GeoColor",
  description: "",
  license: "proprietary",
  extent: { spatial: { bbox: [[-137, 14, -52, 54]] }, temporal: { interval: [[null, null]] } },
  links: [],
} as unknown as StacCollection;

function item(id: string, assets: StacItem["assets"]): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "goes-geocolor",
    geometry: null,
    properties: { datetime: "2026-09-04T06:11:17.300000Z" },
    links: [],
    assets,
  };
}

const TILEABLE = [item("c", { visual: { href: "v.tif", roles: ["visual"] } })];
const NOT_TILEABLE = [item("c", { thumb: { href: "t.png", type: "image/png" } })];

function renderRow(
  added: Array<"footprints" | "imagery"> = [],
  onAdd = vi.fn(),
) {
  render(
    <AddLayerCollectionRow
      collection={COLLECTION}
      catalogUrl="http://localhost:8081"
      isAdded={(kind) => added.includes(kind as "footprints" | "imagery")}
      onAdd={onAdd}
    />,
  );
  return onAdd;
}

beforeEach(() => {
  vi.clearAllMocks();
  useItemsMock.mockReturnValue({ data: { features: TILEABLE } });
  useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: true } });
});

describe("AddLayerCollectionRow", () => {
  it("always offers footprints, under V-2's test id and copy", () => {
    renderRow();

    const footprints = screen.getByTestId("map-add-footprints-goes-geocolor");
    expect(footprints.textContent).toContain("Footprints");
    expect(screen.getByText("GOES GeoColor")).toBeTruthy();
  });

  it("offers imagery when serving is on and the newest items carry a tileable asset", () => {
    renderRow();

    expect(screen.getByTestId("map-add-imagery-goes-geocolor")).toBeTruthy();
    expect(useItemsMock).toHaveBeenCalledWith(
      "http://localhost:8081",
      "goes-geocolor",
      expect.objectContaining({ limit: ADD_LAYER_PROBE_LIMIT, sortby: "-datetime" }),
    );
  });

  it("hides imagery when the product does not advertise serving", () => {
    useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: false } });
    renderRow();

    expect(screen.queryByTestId("map-add-imagery-goes-geocolor")).toBeNull();
    expect(screen.getByTestId("map-add-footprints-goes-geocolor")).toBeTruthy();
  });

  it("hides imagery when nothing recent carries a tileable asset", () => {
    useItemsMock.mockReturnValue({ data: { features: NOT_TILEABLE } });
    renderRow();

    expect(screen.queryByTestId("map-add-imagery-goes-geocolor")).toBeNull();
  });

  it("hides imagery while the probe is still in flight", () => {
    useItemsMock.mockReturnValue({ data: undefined });
    renderRow();

    expect(screen.queryByTestId("map-add-imagery-goes-geocolor")).toBeNull();
  });

  it("shows a kind already on the map as Added and disabled", () => {
    renderRow(["footprints"]);

    const footprints = screen.getByTestId("map-add-footprints-goes-geocolor");
    expect(footprints).toBeDisabled();
    expect(footprints.textContent).toContain("Added");
    expect(screen.getByTestId("map-add-imagery-goes-geocolor")).not.toBeDisabled();
  });

  it("reports the kind and collection when a button is pressed", async () => {
    const onAdd = renderRow();

    await userEvent.click(screen.getByTestId("map-add-imagery-goes-geocolor"));

    expect(onAdd).toHaveBeenCalledWith("imagery", COLLECTION);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-add-layer-row.test.tsx`
Expected: FAIL — `Failed to resolve import "@/components/map/AddLayerCollectionRow"`.

- [ ] **Step 3: Write the picker row**

Create `app/src/components/map/AddLayerCollectionRow.tsx`:

```tsx
/**
 * One collection's entry in the Add-layer popover's Products section
 * (spec §4.5).
 *
 * A component per collection rather than markup in a loop, because each row
 * runs its own two queries: the settings that say whether the product
 * advertises serving, and a small probe of its newest items that says whether
 * anything there is tileable. Both are cheap and both are needed before the
 * Imagery option can honestly be offered — an imagery layer with no candidate
 * asset would add a row that draws nothing, which spec §4.7 forbids.
 */
import { Image, Layers } from "lucide-react";
import { Button } from "@stac-higher/shared";
import type { StacCollection } from "@/lib/stac-api/types";
import type { LayerKind } from "@/lib/map/state";
import { useItems } from "@/lib/query/items";
import { useCollectionSettings } from "@/lib/collections/settings-client";
import { previewAssetCandidates } from "@/lib/serving/preview";

/** Items the imagery probe looks at. Enough to see the product's assets. */
export const ADD_LAYER_PROBE_LIMIT = 5;

export interface AddLayerCollectionRowProps {
  collection: StacCollection;
  catalogUrl: string;
  isAdded: (kind: LayerKind, sourceId: string) => boolean;
  onAdd: (kind: LayerKind, collection: StacCollection) => void;
}

export function AddLayerCollectionRow({
  collection,
  catalogUrl,
  isAdded,
  onAdd,
}: AddLayerCollectionRowProps) {
  const { data: settings } = useCollectionSettings(collection.id);
  const { data } = useItems(catalogUrl, collection.id, {
    limit: ADD_LAYER_PROBE_LIMIT,
    sortby: "-datetime",
  });
  const imageryOffered =
    settings?.servingEnabled === true &&
    previewAssetCandidates(data?.features ?? []).length > 0;

  const footprintsAdded = isAdded("footprints", collection.id);
  const imageryAdded = isAdded("imagery", collection.id);

  return (
    <li className="flex items-center gap-2 px-3 py-1.5">
      <span className="min-w-0 flex-1 truncate text-sm font-medium">
        {collection.title ?? collection.id}
      </span>
      <Button
        size="xs"
        variant={footprintsAdded ? "ghost" : "outline"}
        disabled={footprintsAdded}
        data-testid={`map-add-footprints-${collection.id}`}
        onClick={() => onAdd("footprints", collection)}
      >
        <Layers />
        {footprintsAdded ? "Added" : "Footprints"}
      </Button>
      {imageryOffered && (
        <Button
          size="xs"
          variant={imageryAdded ? "ghost" : "outline"}
          disabled={imageryAdded}
          data-testid={`map-add-imagery-${collection.id}`}
          onClick={() => onAdd("imagery", collection)}
        >
          <Image />
          {imageryAdded ? "Added" : "Imagery"}
        </Button>
      )}
    </li>
  );
}
```

The `<li>`, the `size="xs"` / `variant` / `disabled` / `data-testid` / `Added` copy of the footprints button, and the `min-w-0 flex-1 truncate text-sm font-medium` title span are V-2's markup, moved verbatim — that is why V-2's popover tests keep passing.

Then, in `app/src/components/map/AddLayerPopover.tsx` (V-2 Task 7), add `catalogUrl: string` to `AddLayerPopoverProps`, destructure it, and replace the whole `<li>` block inside `{collections.map((collection) => { … })}` with:

```tsx
            {collections.map((collection) => (
              <AddLayerCollectionRow
                key={collection.id}
                collection={collection}
                catalogUrl={catalogUrl}
                isAdded={isAdded}
                onAdd={(kind, added) => {
                  onAdd(kind, added);
                  setOpen(false);
                }}
              />
            ))}
```

The `setOpen(false)` V-2 had inside the button's `onClick` moves here, so both kinds close the popover. Delete V-2's `{/* V-3 adds an Imagery button beside this one … */}` comment and the now-unused `Layers` import from `AddLayerPopover.tsx`. In `LayerPanel.tsx`, pass its existing `catalogUrl` prop to `<AddLayerPopover … catalogUrl={catalogUrl} />`.

Finally, in `app/src/__tests__/map-page.test.tsx` (V-2), add the two mocks the new per-collection hooks need — **no assertion changes**:

```tsx
const { useCollectionSettingsMock, useItemTileJsonMock } = vi.hoisted(() => ({
  useCollectionSettingsMock: vi.fn(),
  useItemTileJsonMock: vi.fn(),
}));

vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: (...a: unknown[]) => useCollectionSettingsMock(...a),
}));
vi.mock("@/lib/serving/queries", () => ({
  useItemTileJson: (...a: unknown[]) => useItemTileJsonMock(...a),
}));
```

with, in its `beforeEach`:

```tsx
  // No serving in V-2's fixtures: the picker offers Footprints only, exactly
  // as it did before V-3, so every assertion in this file still holds.
  useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: false } });
  useItemTileJsonMock.mockReturnValue({ data: undefined, isFetched: true });
```

(Without these, `useCollectionSettings` would issue a real `fetch` per listed collection inside `QueryProvider` — noise, not a failure, but the kind that hides a real one.)

These V-2 tests must still pass unchanged after this task: **"adds a footprints layer from the picker, as a row and a namespaced source"**, **"disables a product that is already on the map"** (`map-add-footprints-alpha` disabled, `map-add-footprints-beta` not), **"asks the catalog for the span's newest items, per layer"**, **"says so quietly when a product has no timestamped items"**, and the `addFootprints()` helper that drives them.

- [ ] **Step 4: Run it to verify it passes**

Run: `cd app && npx vitest run src/__tests__/map-add-layer-row.test.tsx`
Expected: PASS (7 tests).

- [ ] **Step 5: Write the failing test for the row's asset select**

Create `app/src/__tests__/map-layer-asset-select.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { MapLayer } from "@/lib/map/state";

const { useLayerDataMock } = vi.hoisted(() => ({ useLayerDataMock: vi.fn() }));
vi.mock("@/components/map/useLayerData", () => ({
  useLayerData: (...a: unknown[]) => useLayerDataMock(...a),
}));

import { LayerAssetSelect } from "@/components/map/LayerAssetSelect";

const LAYER: MapLayer = {
  id: "l1",
  kind: "imagery",
  sourceId: "goes-geocolor",
  title: "GeoColor",
  asset: "visual",
  visible: true,
  opacity: 1,
};

function renderSelect(candidates: string[], asset: string | undefined = candidates[0]) {
  useLayerDataMock.mockReturnValue({
    items: [],
    frames: [],
    candidates,
    asset,
    hint: undefined,
    hintSettled: true,
    servingEnabled: true,
  });
  const onAssetChange = vi.fn();
  render(
    <LayerAssetSelect
      layer={LAYER}
      catalogUrl="http://localhost:8081"
      frameSpan={50}
      onAssetChange={onAssetChange}
    />,
  );
  return onAssetChange;
}

beforeEach(() => vi.clearAllMocks());

describe("LayerAssetSelect", () => {
  it("offers the collection's tileable keys when there is a choice", () => {
    renderSelect(["visual", "cmi"]);

    expect(screen.getByRole("combobox", { name: "Layer asset" })).toBeTruthy();
  });

  it("renders nothing when the collection publishes one tileable key", () => {
    // A select with one option is furniture, not a control.
    renderSelect(["visual"]);

    expect(screen.queryByRole("combobox", { name: "Layer asset" })).toBeNull();
  });

  it("reads the layer's data through the page's span", () => {
    renderSelect(["visual", "cmi"]);

    expect(useLayerDataMock).toHaveBeenCalledWith(LAYER, "http://localhost:8081", 50);
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-layer-asset-select.test.tsx`
Expected: FAIL — `Failed to resolve import "@/components/map/LayerAssetSelect"`.

- [ ] **Step 7: Write the asset select and wire it into the row**

Create `app/src/components/map/LayerAssetSelect.tsx`:

```tsx
/**
 * The asset picker on an imagery layer's row (spec §4.5) — the same control
 * the collection Preview tab offers, over the same candidate list.
 *
 * It reads the layer's data through `useLayerData`, which is the very query
 * the layer itself runs: same TanStack key, so the row costs no extra request.
 */
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@stac-higher/shared";
import type { MapLayer } from "@/lib/map/state";
import { useLayerData } from "./useLayerData";

export interface LayerAssetSelectProps {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
  onAssetChange: (asset: string) => void;
}

export function LayerAssetSelect({
  layer,
  catalogUrl,
  frameSpan,
  onAssetChange,
}: LayerAssetSelectProps) {
  const { candidates, asset } = useLayerData(layer, catalogUrl, frameSpan);
  if (candidates.length < 2 || !asset) return null;

  return (
    <Select value={asset} onValueChange={onAssetChange}>
      <SelectTrigger className="h-7 w-[8.5rem]" aria-label="Layer asset">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {candidates.map((key) => (
          <SelectItem key={key} value={key}>
            {key}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
```

In `app/src/components/map/LayerRow.tsx` (V-2), make three edits:

1. Fill in the entry V-2's constant reserved — `Image` joins the lucide import (`ChevronDown, ChevronUp, Eye, EyeOff, Image, Layers, X`), and the comment above it loses the `Image` half:
   ```tsx
   /** Kind icons: `Hexagon` (vector, V-4) follows. */
   const KIND_ICON = { footprints: Layers, imagery: Image } as const;
   ```
   The `?? Layers` fallback in `const Icon = KIND_ICON[layer.kind as keyof typeof KIND_ICON] ?? Layers;` stays — it is what carries `vector` until V-4.
2. Add one prop to `LayerRowProps`, beside V-2's four callbacks:
   ```ts
     onAssetChange: (asset: string) => void;
   ```
   destructure it, and render the select directly under the `FootprintStatusLine` block, above the opacity `Slider`:
   ```tsx
      {layer.kind === "imagery" && (
        <div className="mt-2">
          <LayerAssetSelect
            layer={layer}
            catalogUrl={catalogUrl}
            frameSpan={frameSpan}
            onAssetChange={onAssetChange}
          />
        </div>
      )}
   ```
   with `import { LayerAssetSelect } from "@/components/map/LayerAssetSelect";`.
3. Thread it through, exactly as V-2 threads `onVisibleChange`. In `LayerPanel.tsx`, add `onAssetChange: (id: string, asset: string) => void` to `LayerPanelProps`, destructure it, and pass `onAssetChange={(asset) => onAssetChange(layer.id, asset)}` on the `<LayerRow>`. In `MapPage.tsx`, add to the `<LayerPanel>` call:
   ```tsx
        onAssetChange={(id, asset) => dispatch({ type: "setAsset", id, asset })}
   ```

- [ ] **Step 8: Run both new suites**

Run: `cd app && npx vitest run src/__tests__/map-add-layer-row.test.tsx src/__tests__/map-layer-asset-select.test.tsx src/__tests__/map-page.test.tsx`
Expected: PASS — 10 new tests, and all 15 of V-2's `map-page.test.tsx` tests unchanged.

- [ ] **Step 9: Commit**

```bash
git add app/src/components/map/AddLayerCollectionRow.tsx \
        app/src/components/map/LayerAssetSelect.tsx \
        app/src/components/map/AddLayerPopover.tsx \
        app/src/components/map/LayerPanel.tsx \
        app/src/components/map/LayerRow.tsx \
        app/src/components/map/MapPage.tsx \
        app/src/__tests__/map-add-layer-row.test.tsx \
        app/src/__tests__/map-layer-asset-select.test.tsx \
        app/src/__tests__/map-page.test.tsx
git commit -m "feat(map): imagery option gated on serving + a tileable probe, asset select per row (V-3)

The popover's per-collection <li> becomes AddLayerCollectionRow so the
serving-settings and newest-items probe hooks are legal; its markup, test id
and copy are V-2's, moved verbatim. map-page.test.tsx gains mocks for the two
query modules those hooks reach — no assertion changed."
```

---

### Task 5: One view per STAC layer — footprints per frame, imagery as a stack

**Files:**
- Create: `app/src/components/map/MapLayerView.tsx`
- **Delete**: `app/src/components/map/FootprintsMapLayer.tsx` (V-2 Task 7) — `MapLayerView` is its successor: same query, same `FootprintLayer` call, plus the axis and the imagery branch. Two components rendering the same layer would be two sources fighting for one id.
- Modify: `app/src/components/map/MapPage.tsx` (V-2 Task 9 — the `drawn` render inside `<StacMap>`; Task 6 then adds the axis around it)
- Test: `app/src/__tests__/map-layer-view.test.tsx` (new)
- No change to `app/src/lib/map/state.ts`: the chain is V-2's `layerAnchorId` / `beforeIdFor`, already covering the imagery case (`map-state.test.ts` asserts `layerAnchorId(imagery) === "a-anchor"`).

**Interfaces:**
- Consumes: `useLayerData` (Task 3); `resolveLayerFrame` (Task 1); `RasterFrameStack` and `FootprintLayer` from `@stac-higher/shared`; `collectionTileUrlTemplate` from `@/lib/serving/urls`; `beforeIdFor` / `layerAnchorId` (V-2 Task 4) — used by `MapPage`, unchanged.
- Produces:
  ```ts
  // app/src/components/map/MapLayerView.tsx
  export interface MapLayerViewProps {
    layer: MapLayer;
    catalogUrl: string;
    frameSpan: number;
    /** The instant the page's axis is parked on; null when there is no axis. */
    tickInstant: number | null;
    /** Draw beneath this layer id — the bottom layer of the layer above. */
    beforeId?: string;
    /** Reports this layer's frames up so the page can build the axis. */
    onFramesChange: (layerId: string, frames: PreviewFrame[]) => void;
    /** Called on unmount so a removed layer leaves the axis. */
    onFramesRemove: (layerId: string) => void;
  }
  export function MapLayerView(props: MapLayerViewProps): JSX.Element | null;
  ```

- [ ] **Step 1: Write the failing test for the layer view**

Create `app/src/__tests__/map-layer-view.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { MapLayer } from "@/lib/map/state";
import type { StacItem } from "@/lib/stac-api/types";
import { buildPreviewFrames } from "@/lib/serving/frames";

const { useLayerDataMock } = vi.hoisted(() => ({ useLayerDataMock: vi.fn() }));
vi.mock("@/components/map/useLayerData", () => ({
  useLayerData: (...a: unknown[]) => useLayerDataMock(...a),
}));

// The shared map components are stand-ins here: what is under test is which
// items and which frame index this view hands them, not how they draw.
vi.mock("@stac-higher/shared", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("@stac-higher/shared");
  return {
    ...actual,
    FootprintLayer: (props: Record<string, unknown>) => (
      <div
        data-testid="footprints"
        data-ids={JSON.stringify((props.items as StacItem[]).map((i) => i.id))}
        data-before={String(props.beforeId)}
      />
    ),
    RasterFrameStack: (props: Record<string, unknown>) => (
      <div
        data-testid="stack"
        data-props={JSON.stringify({
          id: props.id,
          index: props.index,
          beforeId: props.beforeId,
          keys: (props.frames as { key: string }[]).map((f) => f.key),
        })}
      />
    ),
  };
});

import { MapLayerView } from "@/components/map/MapLayerView";

function item(id: string, datetime: string): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "goes-geocolor",
    geometry: null,
    properties: { datetime },
    links: [],
    assets: { visual: { href: `${id}.tif`, roles: ["visual"] } },
  };
}

const ITEMS = [
  item("c", "2026-09-04T06:10:00Z"),
  item("b", "2026-09-04T06:05:00Z"),
  item("a", "2026-09-04T06:00:00Z"),
];
const FRAMES = buildPreviewFrames(ITEMS);
const T = (iso: string) => Date.parse(iso);

function layer(overrides: Partial<MapLayer> = {}): MapLayer {
  return {
    id: "l1",
    kind: "imagery",
    sourceId: "goes-geocolor",
    title: "GeoColor",
    visible: true,
    opacity: 1,
    ...overrides,
  };
}

function renderView(props: Partial<Record<string, unknown>> = {}, l: MapLayer = layer()) {
  const onFramesChange = vi.fn();
  const onFramesRemove = vi.fn();
  const result = render(
    <MapLayerView
      layer={l}
      catalogUrl="http://localhost:8081"
      frameSpan={50}
      tickInstant={T("2026-09-04T06:10:00Z")}
      beforeId="above-fill"
      onFramesChange={onFramesChange}
      onFramesRemove={onFramesRemove}
      {...props}
    />,
  );
  return { ...result, onFramesChange, onFramesRemove };
}

function stackProps() {
  return JSON.parse(screen.getByTestId("stack").dataset.props as string);
}

beforeEach(() => {
  vi.clearAllMocks();
  useLayerDataMock.mockReturnValue({
    items: ITEMS,
    frames: FRAMES,
    candidates: ["visual"],
    asset: "visual",
    hint: { tiles: ["ignored"], bounds: [-137, 14, -52, 54], minzoom: 2, maxzoom: 5 },
    hintSettled: true,
    servingEnabled: true,
  });
});

describe("MapLayerView — imagery", () => {
  it("mounts one tile template per frame, datetime pinned verbatim", () => {
    renderView();

    expect(stackProps().keys).toEqual(FRAMES.map((f) => f.datetime));
    expect(stackProps().id).toBe("l1");
    expect(stackProps().beforeId).toBe("above-fill");
  });

  it("shows the frame the axis tick resolves to", () => {
    renderView({ tickInstant: T("2026-09-04T06:07:00Z") });

    // Hold-last: 06:07 is between this layer's 06:05 and 06:10 frames.
    expect(stackProps().index).toBe(1);
  });

  it("draws no frames — but keeps its anchor — before its first frame", () => {
    renderView({ tickInstant: T("2026-09-04T05:00:00Z") });

    expect(stackProps().keys).toEqual([]);
    expect(stackProps().index).toBe(0);
  });

  it("holds the frames back until the zoom hint settles", () => {
    useLayerDataMock.mockReturnValue({
      items: ITEMS,
      frames: FRAMES,
      candidates: ["visual"],
      asset: "visual",
      hint: undefined,
      hintSettled: false,
      servingEnabled: true,
    });
    renderView();

    // The stack itself stays mounted so the page's beforeId chain keeps its
    // target; only the frames wait (a source's zoom range is fixed at
    // creation, so mounting before the hint pins the wrong range).
    expect(stackProps().keys).toEqual([]);
  });

  it("shows the newest frame when there is no axis yet", () => {
    renderView({ tickInstant: null });

    expect(stackProps().index).toBe(FRAMES.length - 1);
  });

  it("draws nothing when the product publishes no tileable asset", () => {
    useLayerDataMock.mockReturnValue({
      items: ITEMS,
      frames: FRAMES,
      candidates: [],
      asset: undefined,
      hint: undefined,
      hintSettled: true,
      servingEnabled: true,
    });
    renderView();

    expect(stackProps().keys).toEqual([]);
  });
});

describe("MapLayerView — footprints", () => {
  it("draws the items of the frame the tick resolves to, not the whole span", () => {
    renderView({ tickInstant: T("2026-09-04T06:05:00Z") }, layer({ kind: "footprints" }));

    expect(screen.getByTestId("footprints").dataset.ids).toBe(JSON.stringify(["b"]));
  });

  it("draws nothing before its first frame", () => {
    renderView({ tickInstant: T("2026-09-04T05:00:00Z") }, layer({ kind: "footprints" }));

    expect(screen.getByTestId("footprints").dataset.ids).toBe(JSON.stringify([]));
  });

  it("passes the page's beforeId through", () => {
    renderView({}, layer({ kind: "footprints" }));

    expect(screen.getByTestId("footprints").dataset.before).toBe("above-fill");
  });
});

describe("MapLayerView — the axis", () => {
  it("reports its frames up and withdraws them when it unmounts", () => {
    const { onFramesChange, onFramesRemove, unmount } = renderView();

    expect(onFramesChange).toHaveBeenCalledWith("l1", FRAMES);
    unmount();
    expect(onFramesRemove).toHaveBeenCalledWith("l1");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-layer-view.test.tsx`
Expected: FAIL — `Failed to resolve import "@/components/map/MapLayerView"`.

- [ ] **Step 3: Write the layer view**

Create `app/src/components/map/MapLayerView.tsx`:

```tsx
/**
 * One STAC layer of the /map page, drawn (spec §4.3, §4.4).
 *
 * A component per layer because each layer runs its own queries and React
 * forbids hooks in a loop. It reports its frames UP to the page, which owns
 * the shared axis, and takes the chosen tick back DOWN — resolving the tick to
 * its OWN frame index, since products have their own cadences and the page's
 * axis is their union.
 *
 * Footprints show the items of their current frame rather than the whole span:
 * a frame's items are exactly what the imagery of the same timestep renders,
 * so the two kinds line up (spec §5, §9).
 */
import { useEffect, useMemo } from "react";
import {
  FootprintLayer,
  RasterFrameStack,
  type RasterFrame,
} from "@stac-higher/shared";
import type { MapLayer } from "@/lib/map/state";
import type { StacItem } from "@/lib/stac-api/types";
import type { PreviewFrame } from "@/lib/serving/frames";
import { resolveLayerFrame } from "@/lib/map/axis";
import { collectionTileUrlTemplate } from "@/lib/serving/urls";
import { useLayerData } from "./useLayerData";

export interface MapLayerViewProps {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
  /** The instant the page's axis is parked on; null when there is no axis. */
  tickInstant: number | null;
  /** Draw beneath this layer id — the bottom layer of the layer above. */
  beforeId?: string;
  onFramesChange: (layerId: string, frames: PreviewFrame[]) => void;
  onFramesRemove: (layerId: string) => void;
}

export function MapLayerView({
  layer,
  catalogUrl,
  frameSpan,
  tickInstant,
  beforeId,
  onFramesChange,
  onFramesRemove,
}: MapLayerViewProps) {
  const { items, frames, asset, hint, hintSettled } = useLayerData(
    layer,
    catalogUrl,
    frameSpan,
  );

  // The page's axis is the union of these; `frames` keeps its identity across
  // renders (useLayerData memoises it), so this settles after one pass.
  useEffect(() => {
    onFramesChange(layer.id, frames);
  }, [layer.id, frames, onFramesChange]);

  useEffect(() => () => onFramesRemove(layer.id), [layer.id, onFramesRemove]);

  // Hold-last against this layer's own frames. Before the page has an axis
  // (nothing has loaded yet) the newest frame is what the preview opens on.
  const resolved =
    tickInstant === null
      ? frames.length > 0
        ? frames.length - 1
        : null
      : resolveLayerFrame(tickInstant, frames);

  const rasterFrames: RasterFrame[] = useMemo(
    () =>
      asset
        ? frames.map((f) => ({
            key: f.datetime,
            tiles: [collectionTileUrlTemplate(layer.sourceId, asset, f.datetime)],
          }))
        : [],
    [frames, asset, layer.sourceId],
  );

  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const frameItems = useMemo(() => {
    if (resolved === null) return [];
    return (frames[resolved]?.itemIds ?? [])
      .map((id) => byId.get(id))
      .filter((i): i is StacItem => i !== undefined);
  }, [resolved, frames, byId]);

  if (layer.kind === "footprints") {
    return (
      <FootprintLayer
        id={layer.id}
        items={frameItems}
        opacity={layer.opacity}
        visible={layer.visible}
        beforeId={beforeId}
      />
    );
  }

  if (layer.kind === "imagery") {
    // Frames wait for the zoom hint to SETTLE (a maplibre source's zoom range
    // is fixed at creation) and for a tick this layer actually reaches. The
    // stack stays mounted either way: its anchor is what the layer below
    // chains its `beforeId` to, and a target that comes and goes drops that
    // layer off the map.
    const showFrames = hintSettled && resolved !== null && rasterFrames.length > 0;
    return (
      <RasterFrameStack
        id={layer.id}
        frames={showFrames ? rasterFrames : []}
        index={
          showFrames
            ? Math.min(Math.max(resolved, 0), rasterFrames.length - 1)
            : 0
        }
        bounds={hint?.bounds}
        minzoom={hint?.minzoom}
        maxzoom={hint?.maxzoom}
        opacity={layer.opacity}
        visible={layer.visible}
        beforeId={beforeId}
      />
    );
  }

  // Vector layers ignore the axis and arrive in V-4.
  return null;
}
```

Then delete `app/src/components/map/FootprintsMapLayer.tsx` and swap it out in `MapPage.tsx`. V-2 Task 9 left this inside `<StacMap>`:

```tsx
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
```

Add three placeholders just above `drawn` — Task 6 deletes all three and supplies the real axis wiring in their place, so the page compiles and V-2's tests pass at the end of THIS task:

```tsx
  // Task 6 replaces these with the shared axis: the layers' reported frames,
  // the tick the time bar is parked on, and the two collectors.
  const tickInstant: number | null = null;
  const handleFrames = useCallback(() => {}, []);
  const dropFrames = useCallback(() => {}, []);
```

Then replace both the map call and the comment with:

```tsx
            {drawn.map(({ layer, beforeId }) => (
              <MapLayerView
                key={layer.id}
                layer={layer}
                catalogUrl={catalogUrl}
                frameSpan={state.frameSpan}
                tickInstant={tickInstant}
                beforeId={beforeId}
                onFramesChange={handleFrames}
                onFramesRemove={dropFrames}
              />
            ))}
            {/* V-4 adds the vector branch inside MapLayerView; it chains its
                beforeId through the same beforeIdFor helper. */}
```

and change the import line `import { FootprintsMapLayer } from "@/components/map/FootprintsMapLayer";` to `import { MapLayerView } from "@/components/map/MapLayerView";`. `drawn`, `beforeIdFor` and the `.reverse()` that makes the list topmost-first are V-2's and stay exactly as they are.

**What this does to V-2's `map-page.test.tsx`** (nothing, and here is why, per test):
- *"adds a footprints layer from the picker…"* — one item with a datetime ⇒ one frame ⇒ a one-tick axis ⇒ the resolved frame is that item. `FootprintLayer` still receives `id={layer.id}`, so `sourceIds()` is `["layer-0"]` and the layer ids are `layer-0-fill` / `layer-0-line`. ✓
- *"asks the catalog for the span's newest items, per layer"* — `useLayerData` issues the identical `useItems(catalogUrl, sourceId, { limit: frameSpan, sortby: "-datetime" })` call. ✓
- *"says so quietly when a product has no timestamped items"* — no frames ⇒ empty axis ⇒ `tickInstant === null` and no frames to resolve ⇒ the footprints draw nothing, and the row's `FootprintStatusLine` (V-2, untouched) still prints the line. ✓
- *"chains each layer beneath the one above it"*, *"hides a layer without dropping its source"*, *"removes a layer…"*, *"reorders layers and re-chains the beforeIds"*, *"makes the visible footprint fills the interactive layers"* — all use the default `{ features: [] }` mock: no frames, no axis, and `FootprintLayer` mounts with zero items but the same ids, `layout` and `beforeId`. ✓
- The hover/click/tooltip tests drive `mapProps.current` handlers directly and never touch layer data. ✓

The one real behavioural change is the one spec §4.4 asks for: once an axis exists, a footprints layer draws the items of its CURRENT frame rather than the whole span. V-2's fixtures carry at most one frame per layer, so no assertion of theirs can see the difference.

- [ ] **Step 4: Run it to verify it passes**

Run: `cd app && npx vitest run src/__tests__/map-layer-view.test.tsx src/__tests__/map-page.test.tsx`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/components/map/MapLayerView.tsx app/src/components/map/MapPage.tsx \
        app/src/__tests__/map-layer-view.test.tsx
git rm app/src/components/map/FootprintsMapLayer.tsx
git commit -m "feat(map): per-layer view — footprints per frame, imagery as a frame stack (V-3)

MapLayerView replaces V-2's FootprintsMapLayer: same items query and the same
FootprintLayer call, plus the axis resolution and the imagery branch. The
draw-order chain is unchanged — MapPage still maps `drawn` from beforeIdFor."
```

---

### Task 6: The docked time bar and the page's axis

**Files:**
- Create: `app/src/components/map/TimeBar.tsx`
- Modify: `app/src/lib/serving/frames.ts` (add the shared playback cap next to the frame builder)
- Modify: `app/src/components/collections/CollectionPreviewTab.tsx:29-30` (use that constant instead of its local one) and `:184`
- Modify: `app/src/components/map/MapPage.tsx` — the axis state replaces Task 5's three placeholders, and `TimeBar` replaces V-2 Task 6's reserved dock comment.
- Test: `app/src/__tests__/map-time-bar.test.tsx` (new)
- Test: `app/src/__tests__/map-page-imagery.test.tsx` (new)

**Interfaces:**
- Consumes: `buildAxis` / `AxisTick` (Task 1); `MapLayerView` (Task 5); `TimeSlider` from `@stac-higher/shared`; V-2's `FrameSpan`, `FRAME_SPANS` and the reducer actions `{ type: "setAxisIndex"; axisIndex }` / `{ type: "setFrameSpan"; frameSpan }`; V-2 Task 9's `mapRef` + `onMapRef` (already on the page — this task adds `canAdvance` beside them, and does NOT declare a second ref).
- Produces:
  ```ts
  // app/src/lib/serving/frames.ts
  /** Ticks a player waits for tiles before advancing anyway — 10s at 4 fps. */
  export const FRAME_MAX_WAIT_TICKS = 40;

  // app/src/components/map/TimeBar.tsx
  export interface TimeBarProps {
    axis: AxisTick[];
    index: number;
    onIndexChange: (index: number) => void;
    frameSpan: FrameSpan;                       // V-2's type, imported
    onFrameSpanChange: (span: FrameSpan) => void;
    canAdvance: () => boolean;
  }
  export function TimeBar(props: TimeBarProps): JSX.Element | null;
  ```
  `FRAME_SPANS` is V-2's export from `@/lib/map/state` — the bar imports it, and declares no list of its own.

- [ ] **Step 1: Write the failing test for the bar**

Create `app/src/__tests__/map-time-bar.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeAll } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { AxisTick } from "@/lib/map/axis";
import { TimeBar } from "@/components/map/TimeBar";

beforeAll(() => {
  // Radix Select needs these in jsdom (the house pattern — see
  // `ingest-form-window.test.tsx`).
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.HTMLElement.prototype.hasPointerCapture = vi.fn() as never;
});

const AXIS: AxisTick[] = [
  { instant: Date.parse("2026-09-04T06:00:00Z"), label: "2026-09-04 06:00 UTC" },
  { instant: Date.parse("2026-09-04T06:05:00Z"), label: "2026-09-04 06:05 UTC" },
  { instant: Date.parse("2026-09-04T06:10:00Z"), label: "2026-09-04 06:10 UTC" },
];

function renderBar(axis = AXIS, index = axis.length - 1) {
  const onIndexChange = vi.fn();
  const onFrameSpanChange = vi.fn();
  render(
    <TimeBar
      axis={axis}
      index={index}
      onIndexChange={onIndexChange}
      frameSpan={50}
      onFrameSpanChange={onFrameSpanChange}
      canAdvance={() => true}
    />,
  );
  return { onIndexChange, onFrameSpanChange };
}

describe("TimeBar", () => {
  it("reads out the tick it is parked on, over the whole axis", () => {
    renderBar();

    expect(screen.getByText("2026-09-04 06:10 UTC")).toBeTruthy();
    expect(screen.getByText("3 / 3")).toBeTruthy();
  });

  it("reports a step back as an axis index", () => {
    const { onIndexChange } = renderBar();

    fireEvent.click(screen.getByLabelText("Previous frame"));

    expect(onIndexChange).toHaveBeenCalledWith(1);
  });

  it("renders nothing when no layer is time-aware", () => {
    const { container } = render(
      <TimeBar
        axis={[]}
        index={-1}
        onIndexChange={vi.fn()}
        frameSpan={50}
        onFrameSpanChange={vi.fn()}
        canAdvance={() => true}
      />,
    );

    expect(container.textContent).toBe("");
  });

  it("offers the four spans", () => {
    renderBar();

    expect(screen.getByRole("combobox", { name: "Frame span" })).toBeTruthy();
    expect(screen.getByText("50 frames")).toBeTruthy();
  });

  it("reports a span change", () => {
    const { onFrameSpanChange } = renderBar();

    fireEvent.click(screen.getByRole("combobox", { name: "Frame span" }));
    fireEvent.click(screen.getByRole("option", { name: "100 frames" }));

    expect(onFrameSpanChange).toHaveBeenCalledWith(100);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-time-bar.test.tsx`
Expected: FAIL — `Failed to resolve import "@/components/map/TimeBar"`.

- [ ] **Step 3: Share the playback cap, then write the bar**

In `app/src/lib/serving/frames.ts`, add next to the frame builder:

```ts
/**
 * Ticks a player waits on `canAdvance` before advancing regardless — 10s at
 * the shared 4 fps default. One frame the tile server never finishes would
 * otherwise stop playback for good. Shared by the collection Preview tab and
 * the /map page's time bar so the two play at the same pace.
 */
export const FRAME_MAX_WAIT_TICKS = 40;
```

In `app/src/components/collections/CollectionPreviewTab.tsx`, delete the local constant (lines 29-30), import `FRAME_MAX_WAIT_TICKS` from `@/lib/serving/frames` alongside `buildPreviewFrames`, and pass it at line 184: `maxWaitTicks={FRAME_MAX_WAIT_TICKS}`.

Create `app/src/components/map/TimeBar.tsx`:

```tsx
/**
 * The /map page's docked time bar (spec §4.4): one axis over every time-aware
 * layer, plus the page-wide frame span.
 *
 * It shows only while an axis exists — a page of vector layers, or of products
 * whose items carry no timestamp, has nothing to scrub.
 */
import { useMemo } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  TimeSlider,
} from "@stac-higher/shared";
import type { AxisTick } from "@/lib/map/axis";
import { FRAME_SPANS, type FrameSpan } from "@/lib/map/state";
import { FRAME_MAX_WAIT_TICKS } from "@/lib/serving/frames";

export interface TimeBarProps {
  axis: AxisTick[];
  index: number;
  onIndexChange: (index: number) => void;
  frameSpan: FrameSpan;
  onFrameSpanChange: (span: FrameSpan) => void;
  /**
   * Whether playback may advance — the preview's rule: the map has finished
   * the tiles of every mounted source, so the incoming frame is complete.
   */
  canAdvance: () => boolean;
}

export function TimeBar({
  axis,
  index,
  onIndexChange,
  frameSpan,
  onFrameSpanChange,
  canAdvance,
}: TimeBarProps) {
  const labels = useMemo(() => axis.map((tick) => tick.label), [axis]);
  if (axis.length === 0) return null;

  return (
    <div className="border-t border-border bg-background px-3 py-2" data-testid="map-time-bar">
      <TimeSlider
        labels={labels}
        index={index}
        onIndexChange={onIndexChange}
        canAdvance={canAdvance}
        maxWaitTicks={FRAME_MAX_WAIT_TICKS}
      >
        <Select
          value={String(frameSpan)}
          onValueChange={(value) => onFrameSpanChange(Number(value) as FrameSpan)}
        >
          <SelectTrigger className="w-[7.5rem]" aria-label="Frame span">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FRAME_SPANS.map((span) => (
              <SelectItem key={span} value={String(span)}>
                {span} frames
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </TimeSlider>
    </div>
  );
}
```

`FRAME_SPANS` and `FrameSpan` are V-2's exports from `@/lib/map/state` (`[25, 50, 100, 200]`, `DEFAULT_FRAME_SPAN` 50) — the bar reads them and defines neither.

- [ ] **Step 4: Run it to verify it passes**

Run: `cd app && npx vitest run src/__tests__/map-time-bar.test.tsx src/__tests__/time-slider.test.tsx src/__tests__/collection-preview-tab.test.tsx`
Expected: PASS — the bar's five tests, and both preview suites unaffected by the moved constant.

- [ ] **Step 5: Write the failing page test**

Create `app/src/__tests__/map-page-imagery.test.tsx`:

```tsx
/**
 * `/map` (V-3, spec §4.4): the docked time bar exists only while a time-aware
 * layer does, and the tick it is parked on drives every layer on the map.
 *
 * Query hooks are mocked; what is under test is the page's wiring — frames up,
 * tick down — not the fetch.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { StacCollection, StacItem } from "@/lib/stac-api/types";

const { useItemsMock, useCollectionsMock, useCollectionSettingsMock, useItemTileJsonMock } =
  vi.hoisted(() => ({
    useItemsMock: vi.fn(),
    useCollectionsMock: vi.fn(),
    useCollectionSettingsMock: vi.fn(),
    useItemTileJsonMock: vi.fn(),
  }));

vi.mock("@/components/layout/AppShell", async () => {
  const { QueryProvider } = await import("@/components/layout/QueryProvider");
  return {
    AppShell: ({ children }: { children: React.ReactNode }) => (
      <QueryProvider>{children}</QueryProvider>
    ),
  };
});
vi.mock("@/lib/query/items", () => ({ useItems: (...a: unknown[]) => useItemsMock(...a) }));
vi.mock("@/lib/query/collections", () => ({
  useCollections: (...a: unknown[]) => useCollectionsMock(...a),
}));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: (...a: unknown[]) => useCollectionSettingsMock(...a),
}));
vi.mock("@/lib/serving/queries", () => ({
  useItemTileJson: (...a: unknown[]) => useItemTileJsonMock(...a),
}));
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
// maplibre needs WebGL; inert stand-ins keep the tree — and the source/layer
// specs — intact.
vi.mock("react-map-gl/maplibre", () => ({
  default: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="map">{children}</div>
  ),
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

const COLLECTION = {
  type: "Collection",
  stac_version: "1.0.0",
  id: "goes-geocolor",
  title: "GOES GeoColor",
  description: "",
  license: "proprietary",
  extent: { spatial: { bbox: [[-137, 14, -52, 54]] }, temporal: { interval: [[null, null]] } },
  links: [],
} as unknown as StacCollection;

function item(id: string, datetime: string): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "goes-geocolor",
    geometry: { type: "Polygon", coordinates: [[[-1, -1], [1, -1], [1, 1], [-1, -1]]] },
    properties: { datetime },
    links: [],
    assets: { visual: { href: `${id}.tif`, roles: ["visual"] } },
  } as unknown as StacItem;
}

const ITEMS = [
  item("c", "2026-09-04T06:10:00Z"),
  item("b", "2026-09-04T06:05:00Z"),
  item("a", "2026-09-04T06:00:00Z"),
];

beforeAll(() => {
  // The QueryProvider pulls in sonner, which reads matchMedia on mount.
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia);
  // Radix Select needs these in jsdom.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.HTMLElement.prototype.hasPointerCapture = vi.fn() as never;
});

beforeEach(() => {
  vi.clearAllMocks();
  // V-2's shape: useCollections(catalogUrl) → { data: { collections } }.
  useCollectionsMock.mockReturnValue({
    data: { collections: [COLLECTION] },
    isLoading: false,
  });
  useItemsMock.mockReturnValue({ data: { features: ITEMS }, isLoading: false });
  useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: true } });
  useItemTileJsonMock.mockReturnValue({
    data: { tiles: ["ignored"], bounds: [-137, 14, -52, 54], minzoom: 2, maxzoom: 5 },
    isFetched: true,
  });
});

/** Open the Add-layer popover and add one kind, through V-2's test ids. */
async function addLayer(kind: "footprints" | "imagery") {
  const user = userEvent.setup();
  await user.click(screen.getByTestId("map-add-layer"));
  await user.click(screen.getByTestId(`map-add-${kind}-goes-geocolor`));
  return user;
}

describe("MapPage — the shared time axis", () => {
  it("docks no time bar until a time-aware layer is added", async () => {
    render(<MapPage />);
    expect(screen.queryByTestId("map-time-bar")).toBeNull();

    await addLayer("footprints");

    expect(screen.getByTestId("map-time-bar")).toBeTruthy();
    expect(screen.getByText("2026-09-04 06:10 UTC")).toBeTruthy();
    expect(screen.getByText("3 / 3")).toBeTruthy();
  });

  it("undocks the bar when the last time-aware layer goes", async () => {
    render(<MapPage />);
    const user = await addLayer("footprints");
    expect(screen.getByTestId("map-time-bar")).toBeTruthy();

    await user.click(screen.getByTestId("map-layer-remove"));

    // The removed layer withdraws its frames, so the axis empties with it.
    expect(screen.queryByTestId("map-time-bar")).toBeNull();
  });

  it("draws only the current frame's footprints", async () => {
    render(<MapPage />);
    const user = await addLayer("footprints");

    const features = () =>
      JSON.parse(
        screen.getAllByTestId("source").map((el) => el.dataset.props as string)[0],
      ).data.features as { id: string }[];
    expect(features().map((f) => f.id)).toEqual(["c"]);

    await user.click(screen.getByLabelText("Previous frame"));

    expect(screen.getByText("2026-09-04 06:05 UTC")).toBeTruthy();
    expect(features().map((f) => f.id)).toEqual(["b"]);
  });

  it("pins an imagery layer's tiles to the tick, verbatim", async () => {
    render(<MapPage />);
    await addLayer("imagery");

    const tiles = screen
      .getAllByTestId("source")
      .map((el) => JSON.parse(el.dataset.props as string))
      .flatMap((p) => (p.tiles as string[] | undefined) ?? []);
    expect(tiles.some((t) => t.includes("datetime=2026-09-04T06%3A10%3A00Z"))).toBe(true);
    expect(tiles.every((t) => t.includes("assets=visual"))).toBe(true);
  });

  it("returns to the newest tick when the span changes", async () => {
    render(<MapPage />);
    const user = await addLayer("footprints");
    await user.click(screen.getByLabelText("Previous frame"));
    expect(screen.getByText("2026-09-04 06:05 UTC")).toBeTruthy();

    // fireEvent, not userEvent: the house pattern for driving a Radix Select
    // in jsdom (`ingest-form-window.test.tsx`).
    fireEvent.click(screen.getByRole("combobox", { name: "Frame span" }));
    fireEvent.click(screen.getByRole("option", { name: "100 frames" }));

    expect(screen.getByText("2026-09-04 06:10 UTC")).toBeTruthy();
    expect(useItemsMock).toHaveBeenCalledWith(
      "http://localhost:8081",
      "goes-geocolor",
      expect.objectContaining({ limit: 100, sortby: "-datetime" }),
    );
  });
});
```

This file mirrors V-2's `map-page.test.tsx` setup deliberately (same AppShell/catalog-store/react-map-gl mocks, same `mapProps` idea) and drives the UI through V-2's test ids: `map-add-layer`, `map-add-footprints-${id}`, `map-layer-remove`. `map-add-imagery-${id}` is Task 4's. `MapPage` takes no props.

- [ ] **Step 6: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-page-imagery.test.tsx`
Expected: FAIL — no time bar is rendered (`map-time-bar` not found).

- [ ] **Step 7: Wire the page**

In `app/src/components/map/MapPage.tsx`, delete Task 5's three placeholders (`tickInstant`, `handleFrames`, `dropFrames`) and put the real axis in their place, directly above V-2's `drawn`:

```tsx
  // Each layer reports its own frames; the axis is their union (spec §4.4).
  // Identity-checked so a layer re-reporting the same array is a no-op — the
  // frames arrive from an effect, and a fresh Map every render would loop.
  const [layerFrames, setLayerFrames] = useState<Map<string, PreviewFrame[]>>(
    () => new Map(),
  );
  const handleFrames = useCallback((layerId: string, frames: PreviewFrame[]) => {
    setLayerFrames((prev) =>
      prev.get(layerId) === frames ? prev : new Map(prev).set(layerId, frames),
    );
  }, []);
  const dropFrames = useCallback((layerId: string) => {
    setLayerFrames((prev) => {
      if (!prev.has(layerId)) return prev;
      const next = new Map(prev);
      next.delete(layerId);
      return next;
    });
  }, []);

  const axis = useMemo(() => buildAxis(layerFrames), [layerFrames]);
  // `axisIndex: null` means "newest", as the collection preview does — held as
  // no-choice-yet rather than an index set by an effect, so the first render is
  // already the newest tick and never steps through a stale one. Clamped
  // because the axis reshapes under a span change or a removed layer.
  const index =
    axis.length === 0
      ? -1
      : Math.min(state.axisIndex ?? axis.length - 1, axis.length - 1);
  const tickInstant = index >= 0 ? axis[index].instant : null;

  // Playback waits for tiles rather than dropping frames: `areTilesLoaded`
  // covers every mounted source, so advancing means the incoming frame of
  // EVERY layer is complete. It is asked a full tick after the change, by
  // which point maplibre has requested the new tiles.
  const canAdvance = useCallback(() => mapRef.current?.areTilesLoaded() ?? true, []);
```

`mapRef` and `onMapRef` are V-2 Task 9's, already on the page and already passed to `<StacMap onMapRef={onMapRef} …>` — do not declare a second ref. Extend V-2's React import to `import { useCallback, useMemo, useReducer, useRef, useState } from "react";` and add:

```tsx
import { buildAxis } from "@/lib/map/axis";
import type { PreviewFrame } from "@/lib/serving/frames";
import { TimeBar } from "@/components/map/TimeBar";
```

The layer loop is unchanged from Task 5 — `drawn`, `beforeIdFor` and the `.reverse()` stay V-2's; `tickInstant`, `handleFrames` and `dropFrames` now carry real values.

Finally, replace V-2 Task 6's reserved dock comment in the map column:

```tsx
        {/* V-3 docks the shared time bar here, below the map and above the
            page edge. Nothing is rendered in V-2: an empty bar would take
            height from the map for no reason. */}
```

with the bar itself (it renders `null` while the axis is empty, so the map keeps its full height until there is something to scrub):

```tsx
        <TimeBar
          axis={axis}
          index={index}
          onIndexChange={(axisIndex) => dispatch({ type: "setAxisIndex", axisIndex })}
          frameSpan={state.frameSpan}
          onFrameSpanChange={(frameSpan) => dispatch({ type: "setFrameSpan", frameSpan })}
          canAdvance={canAdvance}
        />
```

The action payload field names are V-2's (`axisIndex`, `frameSpan`), and `setFrameSpan` already resets `axisIndex` to `null` — which is why changing the span returns the bar to the newest tick with no extra wiring here.

- [ ] **Step 8: Run the page test**

Run: `cd app && npx vitest run src/__tests__/map-page-imagery.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 9: Run every map and preview suite together**

Run:
```bash
cd app && npx vitest run \
  src/__tests__/map-axis.test.ts \
  src/__tests__/map-layer-data.test.tsx \
  src/__tests__/map-layer-view.test.tsx \
  src/__tests__/map-add-layer-row.test.tsx \
  src/__tests__/map-layer-asset-select.test.tsx \
  src/__tests__/map-time-bar.test.tsx \
  src/__tests__/map-page-imagery.test.tsx \
  src/__tests__/map-page.test.tsx \
  src/__tests__/map-state.test.ts \
  src/__tests__/map-styles.test.ts \
  src/__tests__/raster-frame-stack.test.tsx \
  src/__tests__/raster-tile-layer.test.tsx \
  src/__tests__/footprint-layer.test.tsx \
  src/__tests__/vector-tile-layer.test.tsx \
  src/__tests__/time-slider.test.tsx \
  src/__tests__/collection-preview-tab.test.tsx \
  src/__tests__/collection-preview-frames.test.ts
```
Expected: all green — V-2's `map-page.test.tsx` (15 tests) included, with only the two mocks Task 4 added.

- [ ] **Step 10: Commit**

```bash
git add app/src/components/map/TimeBar.tsx app/src/components/map/MapPage.tsx \
        app/src/lib/serving/frames.ts app/src/components/collections/CollectionPreviewTab.tsx \
        app/src/__tests__/map-time-bar.test.tsx app/src/__tests__/map-page-imagery.test.tsx
git commit -m "feat(map): docked time bar over the shared axis; footprints per frame (V-3)"
```

---

### Task 7: Verify, record, merge, live-check

**Files:**
- Modify: `docs/ISSUES.md:421-448` (the I-112 entry → 🟢 resolved)
- Modify: `docs/FEATURES.md` — the `| Map page (\`/map\`) |` row V-2 Task 10 added beneath "Collection raster preview (Preview tab)"; V-3 appends to its cell.
- Modify: `TODO.md` — line 19 (the V row of the queue table, as V-2 Task 10 left it) and the V-3 checkbox (lines 653-661)

- [ ] **Step 1: Full gate**

Run from the worktree root: `npm run verify`
Expected: app-scoped typecheck, build and unit tests all green. Fix anything reported on this branch before continuing.

- [ ] **Step 2: Confirm the untouchable test files are untouched**

```bash
git diff ai/main --stat -- \
  app/src/__tests__/collection-preview-frames.test.ts \
  app/src/__tests__/raster-tile-layer.test.tsx \
  app/src/__tests__/time-slider.test.tsx
```
Expected: no output. `collection-preview-tab.test.tsx` and `raster-frame-stack.test.tsx` DO appear in a wider diff — that is the permitted change, explained in Task 2's commit message.

- [ ] **Step 3: Close I-112**

In `docs/ISSUES.md`, replace the I-112 entry (lines 421-448) with:

```markdown
### I-112 · Stepping a raster frame series backward showed the wrong frame — 🟢 resolved (V-3, 2026-09-07)

**Was.** `RasterFrameStack` (and the collection Preview tab before it) kept the
previous frame painted beneath the current one and relied on React child order
for draw order. maplibre fixes draw order at `addLayer` time and only
`moveLayer`s when `beforeId` changes, so a frame that was ALREADY mounted
stayed where it was. Walking 1 → 2 → 1 left both frames at full opacity with 2
on top — the viewer saw frame 2 while the slider said 1. Forward playback was
unaffected (the incoming frame is always newly mounted, hence on top), which is
why the live GOES check never saw it.

**Fixed by V-3.** The stack states its draw order instead of inheriting it:
each mounted frame chains `beforeId` to the frame above it, the top frame to
the caller's target, and the always-mounted anchor to the lowest frame — so any
reorder changes a `beforeId` and react-map-gl issues the `moveLayer`. The
frames are rendered top-first because react-map-gl creates layers during
render, in tree order, and maplibre drops a layer whose `beforeId` target does
not exist yet; the anchor is rendered last for the same reason. An empty series
now keeps the anchor mounted, so the `/map` page's chain never points at a
target that comes and goes. Regression tests:
`app/src/__tests__/raster-frame-stack.test.tsx` (the chain after 1 → 2 → 1) and
`app/src/__tests__/collection-preview-tab.test.tsx` (the previous frame's layer
draws beneath the current frame's). Spec:
`docs/superpowers/specs/2026-09-04-map-page-design.md` §11.3.
```

- [ ] **Step 4: Record the feature**

In `docs/FEATURES.md`, the Map page row V-2 added ends "… Imagery + the shared time axis are V-3, tipg vector layers and the full docs are V-4". Replace that closing clause with "… tipg vector layers and the full docs are V-4" and append to the same cell:

```
V-3 (2026-09-07) added imagery layers (titiler-pgstac's collection mosaic with `datetime` pinned, the Imagery option offered only when the product advertises serving AND a five-item probe finds a tileable asset, with a per-row asset select) and the docked time bar: `app/src/lib/map/axis.ts` builds one axis from the union of every time-aware layer's frame instants and resolves each layer's own frame by hold-last, so a slow product stays on screen through a fast one's ticks; footprints draw the items of their current frame, playback waits on `map.areTilesLoaded()` with the shared 10 s cap, and the page-wide span (25/50/100/200) returns the axis to the newest tick.
```

- [ ] **Step 5: Record the queue**

In `TODO.md`: tick V-3 (`- [x]`) and change the V row of the queue table (line 19) to:

```
| **V** | Map page: the catalog's products as map layers (footprints, titiler imagery, tipg vector tiles) on one time axis | Spec **approved 2026-09-04**. V-1 merged 2026-09-04, V-2 + V-3 merged 2026-09-07 (V-3 closes I-112); **V-4 next**, depends on V-2 only. No migrations |
```

```bash
git add docs/ISSUES.md docs/FEATURES.md TODO.md
git commit -m "docs: V-3 done — imagery layers, the shared time axis, I-112 closed"
```

- [ ] **Step 6: Merge**

```bash
cd <repo root>
git checkout ai/main
git merge ai/v3-map-axis --no-ff -m "Merge ai/v3-map-axis: V-3 imagery layers + the shared time axis"
npm run verify
git worktree remove .claude/worktrees/v3-map-axis
git branch -d ai/v3-map-axis
```

Do not push `ai/main` — it stays local by the lead's standing instruction; a human promotes it via PR.

- [ ] **Step 7: Live check (LEAD ONLY — Docker + the standing GOES demo)**

Not a teammate step. On `ai/main` after the merge, with the stack up (`docker compose up -d`) and the standing GOES demo seeded:

1. `cd app && npm run dev`, open http://localhost:4321/map.
2. Add **Imagery** for `goes-geocolor` and **Footprints** for `goes-abi-mcmipc`.
3. Set the span to **50 frames**.
4. Press play: frames advance at the tiler's pace, the terminator moves west across the disc, and the footprints change with the imagery.
5. Scrub BACKWARD several steps: the frame on screen matches the readout at every step (this is I-112).
6. Toggle each layer's visibility and drag its opacity: neither refetches tiles (watch the network panel) and imagery never covers the footprints of a layer above it.
7. The browser console shows no maplibre errors — in particular no "layer … does not exist" from a `beforeId`.

Record the result in `TODO.md` under the V queue (one line, with the date). If step 5 or 7 fails, that is a V-3 defect: fix on `ai/main` and commit there.

---

## Self-review

**Spec coverage.**
- §4.3 imagery half — one items query per layer shared by kind through TanStack's key, `buildPreviewFrames`, `previewAssetCandidates`, the zoom hint for the newest item + chosen asset, frames held back until it settles → Task 3 (`useLayerData`) + Task 5 (`MapLayerView`). ✓
- §4.4 the axis — `buildAxis` (union, dedupe, sorted), `resolveLayerFrame` (hold-last, `null` when nothing is that early, binary search), the page rendering `TimeSlider` over the labels with `index = axisIndex ?? last`, `areTilesLoaded` pacing with the same 10 s cap, footprints per frame, vector layers ignoring the axis, the bar hidden with no time-aware layer, the span resetting to the newest tick → Tasks 1, 5, 6. ✓
- §4.5 — the Imagery option gated on `servingEnabled` AND a five-item probe with a `previewAssetCandidates` key, V-2's "Added" + disabled state extended to it, the per-row asset select when a collection publishes more than one tileable key, `KIND_ICON.imagery = Image` → Task 4. The "no items with a timestamp" muted line already exists (V-2's `FootprintStatusLine`) and is untouched. ✓
- §5 — costs accepted as written: N small probes per popover open (Task 4), several stacks contending for the tiler (lookahead stays 1), footprints per frame not per span (Task 5). ✓
- §6 V-3 rows — `axis.ts` unit tests (Task 1, 11 cases including union, dedupe, hold-last, nothing-that-early, empty input, a single layer reproducing its own indices, interval form); "time bar appears only with a time-aware layer" (Task 6, `map-page-imagery.test.tsx`); the live check (Task 7 Step 7). ✓
- §11.3 / I-112 — the chain, its creation-order consequence, the table of ids, the regression tests, the issue closed → Tasks 2 and 7. ✓
- Carried-forward V-3 bullet (`TODO.md`) — `resolveLayerFrame` never returns an out-of-range index (Task 1's last test), `MapLayerView` clamps again before handing an index to a stack (Task 5), the draw-order fix chains `beforeId` inside the stack (Task 2), the preview assertion change is used and explained (Task 2 Steps 5 and 7), and the `hintSettled` gate is kept (Task 5's fourth imagery test). ✓
- Docs — ISSUES, FEATURES, TODO → Task 7. ✓

**Placeholder scan.** No "TBD", no "similar to Task N", no "add tests" without the test body: every code step carries the code, every test step the assertions. No "if V-2 named it differently" hedges remain — V-2's plan is fixed, every seam name below is copied from it, and each V-2 file this plan edits is quoted in its current state before the replacement.

**V-2 seam.** `layerAnchorId` / `beforeIdFor` / `drawn` (V-2 Task 4 + 9) are consumed unchanged; V-3 adds no chain helper of its own and `app/src/lib/map/state.ts` is not edited. `MapLayerView` (Task 5) replaces `FootprintsMapLayer`, which is deleted. `AddLayerCollectionRow` (Task 4) is an extraction of V-2's popover `<li>`, keeping `map-add-footprints-${id}`, its `size="xs"` / `variant` / `Added` copy, and `isAdded` / `onAdd`. `LayerRow` gains `KIND_ICON.imagery` and `onAssetChange`; `AddLayerPopover` gains `catalogUrl`; `LayerPanel` threads both. `TimeBar` replaces V-2's reserved dock comment and imports V-2's `FRAME_SPANS` / `FrameSpan`. Edits to V-2 test files: `map-page.test.tsx` gains two mocks and no assertion change (Task 4); `raster-frame-stack.test.tsx` flips its two tree-order assertions (Task 2).

**Type consistency.** `PreviewFrame { datetime, label, itemIds }` is the same shape in Tasks 1, 3, 5, 6. `AxisTick { instant, label }` is produced in Task 1 and consumed in Tasks 5 (`tickInstant: number`) and 6 (`axis: AxisTick[]`). `LayerData` (Task 3) is destructured in Tasks 4 (`candidates`, `asset`) and 5 (`items`, `frames`, `asset`, `hint`, `hintSettled`) with those exact names. `resolveLayerFrame(tickInstant, frames): number | null` — the `null` branch is handled in Task 5 for both kinds. The chain target is V-2's `layerAnchorId`, reached only through V-2's `beforeIdFor` in `MapPage`'s `drawn`; no V-3 file names a layer id itself. `FrameSpan` and `FRAME_SPANS` are V-2's, used in `TimeBarProps` and the span `Select`. `FRAME_MAX_WAIT_TICKS` (Task 6) replaces the preview's local `MAX_WAIT_TICKS` at its single call site. `rasterFrameStackAnchorId(id)` = `${id}-anchor` is used identically in Task 2's component and tests and in V-2's `layerAnchorId`. `RasterFrame { key, tiles }` in Task 5 matches the shared type unchanged since V-1. `MapLayerViewProps` (Task 5) is what Task 6's `drawn` loop passes, field for field.
