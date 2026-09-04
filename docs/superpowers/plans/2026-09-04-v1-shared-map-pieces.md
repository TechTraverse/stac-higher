# V-1 · Shared Map Pieces + Preview Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every map layer the `/map` page will draw exists as a shared, id-namespaced component, and the collection preview tab runs on the same frame-stack component the map page will use, with its tests unchanged.

**Architecture:** The preview tab's frame machinery (previous + current + one lookahead raster frame, swapped by opacity) becomes `RasterFrameStack` in the shared package. `FootprintLayer` gets the same `id` namespacing `RasterTileLayer` already has, which means the layer specs in `lib/map/styles.ts` become functions of the source id. A new `VectorTileLayer` draws an MVT TileJSON source. The tipg base URL gets a builder beside `titilerBaseUrl`, replacing two inline env reads. Nothing on the page side is built here; V-2…V-4 consume these pieces.

**Tech Stack:** React 19, `react-map-gl/maplibre` 8, MapLibre GL 5, vitest + Testing Library (tests live in `app/src/__tests__/` and import from `@stac-higher/shared`), Storybook (`packages/shared/`).

**Spec:** `docs/superpowers/specs/2026-09-04-map-page-design.md` §3 (all of it), §6 (the V-1 rows: `RasterFrameStack`, `VectorTileLayer`, `FootprintLayer` ids, refactor proof, stories), §10 V-1.

## Global Constraints

- Worktree off `ai/main`: `git worktree add .claude/worktrees/v1-shared-map -b ai/v1-shared-map ai/main`, then `npm install` at the worktree root.
- Gate: `npm run verify` from the repo root of the worktree. Run one test file with `cd app && npx vitest run src/__tests__/<file>`.
- **The four preview test files must pass UNCHANGED**: `collection-preview-frames.test.ts`, `collection-preview-tab.test.tsx`, `raster-tile-layer.test.tsx`, `time-slider.test.tsx`. Do not edit them. If a refactor step breaks one, the refactor is wrong, not the test.
- Existing default ids stay exactly as they are (`stac-footprints`, `stac-footprint-fill`, `stac-footprint-line`, `stac-raster-preview`, `stac-raster-preview-layer`): the item, search and browse pages rely on them.
- The preview tab's frame source ids must remain `preview-frame-{n}` — the preview tests assert them.
- Shared components live in `packages/shared/src/components/map/` and are exported from `packages/shared/src/index.ts`; app code imports them from `@stac-higher/shared`, never by relative path (`project-conventions`).
- Never edit `packages/shared/src/components/ui/*` or `app/src/components/ui/*` (hook-blocked). The `astro check` hook runs after every `.ts`/`.tsx` edit; fix what it reports before moving on.
- No new dependencies.
- The tile-URL rules from spec §3 are not touched: the `datetime` string is never normalised, lookahead is exactly one, opacity swaps have no transition.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01YPWS2PpmqDhtm7VoKhRndR
  ```

---

### Task 1: Footprint layer specs as functions of the source id

**Files:**
- Modify: `packages/shared/src/lib/map/styles.ts`
- Modify: `packages/shared/src/index.ts:41-52` (the styles export block)
- Test: `app/src/__tests__/map-styles.test.ts` (new)

**Interfaces:**
- Produces:
  ```ts
  export function footprintLayerIds(sourceId: string): { fill: string; line: string };
  export function footprintLayers(sourceId: string, opacity?: number): { fill: LayerSpecification; line: LayerSpecification };
  ```
  `footprintLayerIds("stac-footprints")` → `{ fill: "stac-footprint-fill", line: "stac-footprint-line" }` (the legacy names, kept verbatim); any other source id → `{ fill: "${sourceId}-fill", line: "${sourceId}-line" }`. `opacity` (default 1) scales the fill's hover expression and sets `line-opacity`. The existing `footprintFillLayer` / `footprintLineLayer` constants stay exported, now defined as `footprintLayers(FOOTPRINT_SOURCE)`.

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/map-styles.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  FOOTPRINT_SOURCE,
  footprintFillLayer,
  footprintLineLayer,
  footprintLayerIds,
  footprintLayers,
} from "@stac-higher/shared";

describe("footprint layer specs", () => {
  it("keeps the legacy ids for the default source", () => {
    // The item, search and browse pages mount one FootprintLayer with these
    // exact ids; renaming them would silently detach any interactivity.
    expect(footprintLayerIds(FOOTPRINT_SOURCE)).toEqual({
      fill: "stac-footprint-fill",
      line: "stac-footprint-line",
    });
    expect(footprintFillLayer.id).toBe("stac-footprint-fill");
    expect(footprintLineLayer.id).toBe("stac-footprint-line");
    expect(footprintFillLayer.source).toBe(FOOTPRINT_SOURCE);
  });

  it("namespaces ids and the source for any other source id", () => {
    const { fill, line } = footprintLayers("layer-a");
    expect(fill).toMatchObject({ id: "layer-a-fill", type: "fill", source: "layer-a" });
    expect(line).toMatchObject({ id: "layer-a-line", type: "line", source: "layer-a" });
  });

  it("scales the fill's hover expression and the line by the given opacity", () => {
    const { fill, line } = footprintLayers("layer-a", 0.5);
    expect(fill.paint).toMatchObject({
      "fill-opacity": ["*", 0.5, ["case", ["boolean", ["feature-state", "hover"], false], 0.25, 0.1]],
    });
    expect(line.paint).toMatchObject({ "line-opacity": 0.5 });
  });

  it("emits the plain hover expression at full opacity", () => {
    // The default spec must be byte-identical to what the single-layer pages
    // rendered before: no wrapping expression, no line-opacity key.
    const { fill, line } = footprintLayers(FOOTPRINT_SOURCE);
    expect(fill.paint).toEqual(footprintFillLayer.paint);
    expect(line.paint).toEqual(footprintLineLayer.paint);
    expect((fill.paint as Record<string, unknown>)["fill-opacity"]).toEqual([
      "case",
      ["boolean", ["feature-state", "hover"], false],
      0.25,
      0.1,
    ]);
    expect(line.paint).not.toHaveProperty("line-opacity");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/map-styles.test.ts`
Expected: FAIL — `footprintLayerIds` / `footprintLayers` are not exported from `@stac-higher/shared`.

- [ ] **Step 3: Make the specs functions of the source id**

Replace the two footprint constants in `packages/shared/src/lib/map/styles.ts` (leave the extent, selected and raster-preview constants exactly as they are) with:

```ts
/** The hover-aware fill opacity every footprint layer uses. */
const FOOTPRINT_FILL_OPACITY: ExpressionSpecification = [
  "case",
  ["boolean", ["feature-state", "hover"], false],
  0.25,
  0.1,
];

/**
 * Layer ids for a footprint source. The default source keeps the ids the
 * single-layer pages have always used; any other source is namespaced so
 * several footprint layers can share one map (the /map page).
 */
export function footprintLayerIds(sourceId: string): { fill: string; line: string } {
  if (sourceId === FOOTPRINT_SOURCE) {
    return { fill: "stac-footprint-fill", line: "stac-footprint-line" };
  }
  return { fill: `${sourceId}-fill`, line: `${sourceId}-line` };
}

/**
 * Fill + line specs for a footprint source. `opacity` (0..1) scales the whole
 * layer — the hover contrast is preserved inside it — and is omitted from
 * the spec entirely at 1 so the default output is unchanged.
 */
export function footprintLayers(
  sourceId: string,
  opacity = 1,
): { fill: LayerSpecification; line: LayerSpecification } {
  const ids = footprintLayerIds(sourceId);
  const scaled = opacity === 1;
  return {
    fill: {
      id: ids.fill,
      type: "fill",
      source: sourceId,
      paint: {
        "fill-color": "#3b82f6",
        "fill-opacity": scaled
          ? FOOTPRINT_FILL_OPACITY
          : (["*", opacity, FOOTPRINT_FILL_OPACITY] as ExpressionSpecification),
      },
    },
    line: {
      id: ids.line,
      type: "line",
      source: sourceId,
      paint: {
        "line-color": "#3b82f6",
        "line-width": [
          "case",
          ["boolean", ["feature-state", "hover"], false],
          3,
          1.5,
        ],
        ...(scaled ? {} : { "line-opacity": opacity }),
      },
    },
  };
}

export const footprintFillLayer: LayerSpecification = footprintLayers(FOOTPRINT_SOURCE).fill;
export const footprintLineLayer: LayerSpecification = footprintLayers(FOOTPRINT_SOURCE).line;
```

Change the file's type import to `import type { ExpressionSpecification, LayerSpecification } from "maplibre-gl";`.

Add the two functions to the styles export block in `packages/shared/src/index.ts`:

```ts
export {
  FOOTPRINT_SOURCE,
  EXTENT_SOURCE,
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
} from "@shared/lib/map/styles";
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/map-styles.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/shared/src/lib/map/styles.ts packages/shared/src/index.ts app/src/__tests__/map-styles.test.ts
git commit -m "feat(map): footprint layer specs as functions of the source id (V-1)"
```

---

### Task 2: `FootprintLayer` gains `id`, `opacity`, `beforeId`

**Files:**
- Modify: `packages/shared/src/components/map/FootprintLayer.tsx`
- Modify: `packages/shared/src/index.ts:89` (also export the props type)
- Test: `app/src/__tests__/footprint-layer.test.tsx` (new)

**Interfaces:**
- Consumes: `footprintLayers(sourceId, opacity)` from Task 1.
- Produces:
  ```ts
  export interface FootprintLayerProps {
    items: StacItem[];
    selectedId?: string;
    /** Namespace for the maplibre source; omit for the single-layer pages. */
    id?: string;
    /** 0..1, default 1. */
    opacity?: number;
    /** Draw beneath this layer id. */
    beforeId?: string;
  }
  export function FootprintLayer(props: FootprintLayerProps): JSX.Element;
  ```
  With `id="layer-a"` the source is `layer-a` and the layers `layer-a-fill` / `layer-a-line`. Without `id`, everything is exactly as before.

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/footprint-layer.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { StacItem } from "@/lib/stac-api/types";

vi.mock("react-map-gl/maplibre", () => ({
  Source: ({ children, ...props }: Record<string, unknown> & { children?: React.ReactNode }) => (
    <div data-testid="source" data-props={JSON.stringify(props)}>
      {children}
    </div>
  ),
  Layer: (props: Record<string, unknown>) => (
    <div data-testid="layer" data-props={JSON.stringify(props)} />
  ),
}));

import { FootprintLayer } from "@stac-higher/shared";

function item(id: string): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "c",
    geometry: { type: "Point", coordinates: [0, 0] },
    properties: { datetime: "2026-09-04T00:00:00Z" },
    links: [],
    assets: {},
  };
}

function sources(): Record<string, unknown>[] {
  return screen.getAllByTestId("source").map((el) => JSON.parse(el.dataset.props as string));
}
function layers(): Record<string, unknown>[] {
  return screen.getAllByTestId("layer").map((el) => JSON.parse(el.dataset.props as string));
}

describe("FootprintLayer", () => {
  it("keeps the legacy source and layer ids when no id is given", () => {
    render(<FootprintLayer items={[item("a")]} />);

    expect(sources()[0]).toMatchObject({ id: "stac-footprints", type: "geojson" });
    expect(layers().map((l) => l.id)).toEqual(["stac-footprint-fill", "stac-footprint-line"]);
    expect(layers()[0]).not.toHaveProperty("beforeId");
  });

  it("namespaces the source and layers by id so two can share a map", () => {
    render(
      <>
        <FootprintLayer id="layer-a" items={[item("a")]} />
        <FootprintLayer id="layer-b" items={[item("b")]} />
      </>,
    );

    expect(sources().map((s) => s.id)).toEqual(["layer-a", "layer-b"]);
    expect(layers().map((l) => l.id)).toEqual([
      "layer-a-fill",
      "layer-a-line",
      "layer-b-fill",
      "layer-b-line",
    ]);
    expect(layers()[2]).toMatchObject({ source: "layer-b" });
  });

  it("applies opacity and beforeId to both layers", () => {
    render(<FootprintLayer id="layer-a" items={[item("a")]} opacity={0.4} beforeId="top" />);

    const [fill, line] = layers();
    expect(fill).toMatchObject({ beforeId: "top" });
    expect(line).toMatchObject({ beforeId: "top", paint: { "line-opacity": 0.4 } });
    expect((fill.paint as Record<string, unknown>)["fill-opacity"]).toEqual([
      "*",
      0.4,
      ["case", ["boolean", ["feature-state", "hover"], false], 0.25, 0.1],
    ]);
  });

  it("drops items without geometry and marks the selected one", () => {
    render(<FootprintLayer items={[item("a"), { ...item("b"), geometry: null }]} selectedId="a" />);

    const data = sources()[0].data as GeoJSON.FeatureCollection;
    expect(data.features).toHaveLength(1);
    expect(data.features[0].properties).toMatchObject({ id: "a", selected: true });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/footprint-layer.test.tsx`
Expected: FAIL — the second test finds both sources with id `stac-footprints`; the third finds no `beforeId` and no `line-opacity`.

- [ ] **Step 3: Rewrite `FootprintLayer`**

Replace `packages/shared/src/components/map/FootprintLayer.tsx` with:

```tsx
import { Source, Layer } from "react-map-gl/maplibre";
import { FOOTPRINT_SOURCE, footprintLayers } from "@shared/lib/map/styles";
import type { StacItem } from "@shared/lib/stac-api/types";

export interface FootprintLayerProps {
  items: StacItem[];
  selectedId?: string;
  /**
   * Namespace for this layer's maplibre source. Omit for the single-layer
   * pages (item, search, browse), which keep the legacy ids; pass a
   * per-instance value when a map mounts several footprint layers at once.
   */
  id?: string;
  /** 0..1, default 1 — scales fill and line together. */
  opacity?: number;
  /** Draw beneath this layer id. */
  beforeId?: string;
}

/**
 * Item footprints as one GeoJSON source with a fill and a line layer.
 * Pure presentation over `items`; hover contrast comes from the `hover`
 * feature-state, which the page owning the map sets.
 */
export function FootprintLayer({
  items,
  selectedId,
  id,
  opacity = 1,
  beforeId,
}: FootprintLayerProps) {
  const sourceId = id ?? FOOTPRINT_SOURCE;
  const { fill, line } = footprintLayers(sourceId, opacity);

  const geojson: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: items
      .filter((item) => item.geometry)
      .map((item) => ({
        type: "Feature" as const,
        id: item.id,
        properties: {
          id: item.id,
          datetime: item.properties.datetime,
          selected: item.id === selectedId,
        },
        geometry: item.geometry!,
      })),
  };

  return (
    <Source id={sourceId} type="geojson" data={geojson}>
      <Layer {...fill} beforeId={beforeId} />
      <Layer {...line} beforeId={beforeId} />
    </Source>
  );
}
```

Note: react-map-gl's `Layer` drops an `undefined` `beforeId`, so the first test's `not.toHaveProperty("beforeId")` holds because `JSON.stringify` omits undefined values.

Update the export in `packages/shared/src/index.ts`:

```ts
export { FootprintLayer } from "@shared/components/map/FootprintLayer";
export type { FootprintLayerProps } from "@shared/components/map/FootprintLayer";
```

- [ ] **Step 4: Run the new test and the pages' existing tests**

Run: `cd app && npx vitest run src/__tests__/footprint-layer.test.tsx src/__tests__/map-styles.test.ts`
Expected: PASS.

Run: `cd app && npx vitest run`
Expected: PASS — nothing else changed behaviour (the default ids are identical).

- [ ] **Step 5: Commit**

```bash
git add packages/shared/src/components/map/FootprintLayer.tsx packages/shared/src/index.ts app/src/__tests__/footprint-layer.test.tsx
git commit -m "feat(map): FootprintLayer takes id, opacity and beforeId (V-1)"
```

---

### Task 3: `RasterFrameStack` — the frame machinery, extracted

**Files:**
- Create: `packages/shared/src/components/map/RasterFrameStack.tsx`
- Create: `packages/shared/src/components/map/RasterFrameStack.stories.tsx`
- Modify: `packages/shared/src/index.ts` (export component + props type)
- Test: `app/src/__tests__/raster-frame-stack.test.tsx` (new)

**Interfaces:**
- Consumes: `RasterTileLayer` (`id`, `tiles`, `bounds`, `minzoom`, `maxzoom`, `opacity`, `opacityTransitionMs`, `beforeId`).
- Produces:
  ```ts
  export interface RasterFrame {
    /** Stable identity for React keys — the preview uses the frame's datetime. */
    key: string;
    /** XYZ tile templates for this frame. */
    tiles: string[];
  }
  export interface RasterFrameStackProps {
    /** Namespace: frame n mounts as source `${id}-frame-${n}`. */
    id: string;
    frames: RasterFrame[];
    /** Index of the frame to show; the caller clamps it to `frames`. */
    index: number;
    bounds?: [number, number, number, number];
    minzoom?: number;
    maxzoom?: number;
    /** The layer's own opacity (0..1, default 1), applied to the visible frames. */
    opacity?: number;
    beforeId?: string;
  }
  export const RASTER_FRAME_LOOKAHEAD = 1;
  export function RasterFrameStack(props: RasterFrameStackProps): JSX.Element | null;
  ```
  Mount set, in draw order: previous frame, then `index`, then `LOOKAHEAD` frames after it (all `% frames.length`, deduped). Previous and current draw at `opacity`; lookahead at 0. Every frame passes `opacityTransitionMs={0}`. With zero frames it renders nothing.

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/raster-frame-stack.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("react-map-gl/maplibre", () => ({
  Source: ({ children, ...props }: Record<string, unknown> & { children?: React.ReactNode }) => (
    <div data-testid="source" data-props={JSON.stringify(props)}>
      {children}
    </div>
  ),
  Layer: (props: Record<string, unknown>) => (
    <div data-testid="layer" data-props={JSON.stringify(props)} />
  ),
}));

import { RasterFrameStack, RASTER_FRAME_LOOKAHEAD } from "@stac-higher/shared";

const FRAMES = ["a", "b", "c", "d"].map((key) => ({
  key,
  tiles: [`http://t/${key}/{z}/{x}/{y}`],
}));

interface Rendered {
  source: string;
  opacity: number;
  transition: unknown;
}

function layers(): Rendered[] {
  return screen.queryAllByTestId("layer").map((el) => {
    const props = JSON.parse(el.dataset.props as string);
    const paint = props.paint as Record<string, unknown>;
    return {
      source: props.source as string,
      opacity: paint["raster-opacity"] as number,
      transition: paint["raster-opacity-transition"],
    };
  });
}

describe("RasterFrameStack", () => {
  it("warms exactly one frame ahead, at zero opacity", () => {
    expect(RASTER_FRAME_LOOKAHEAD).toBe(1);
    render(<RasterFrameStack id="s" frames={FRAMES} index={1} />);

    expect(layers()).toEqual([
      { source: "s-frame-1", opacity: 1, transition: { duration: 0, delay: 0 } },
      { source: "s-frame-2", opacity: 0, transition: { duration: 0, delay: 0 } },
    ]);
  });

  it("keeps the previous frame painted beneath the current one", () => {
    // Tiles render far slower than a step; the outgoing frame stays up until
    // the incoming one covers it, otherwise every step flashes empty.
    const { rerender } = render(<RasterFrameStack id="s" frames={FRAMES} index={2} />);
    rerender(<RasterFrameStack id="s" frames={FRAMES} index={1} />);

    // The raw window is [previous=2, current=1, lookahead=2]; deduped, frame 2
    // is mounted once, as the previous frame, at full opacity.
    expect(layers().map((l) => [l.source, l.opacity])).toEqual([
      ["s-frame-2", 1],
      ["s-frame-1", 1],
    ]);
  });

  it("wraps the lookahead around the end of the series", () => {
    render(<RasterFrameStack id="s" frames={FRAMES} index={3} />);

    expect(layers().map((l) => l.source)).toEqual(["s-frame-3", "s-frame-0"]);
  });

  it("applies the layer opacity to the visible frames only", () => {
    const { rerender } = render(<RasterFrameStack id="s" frames={FRAMES} index={0} opacity={0.5} />);
    rerender(<RasterFrameStack id="s" frames={FRAMES} index={1} opacity={0.5} />);

    expect(layers().map((l) => l.opacity)).toEqual([0.5, 0.5, 0]);
  });

  it("passes bounds, zoom limits and beforeId to every frame", () => {
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
    for (const el of screen.getAllByTestId("layer")) {
      expect(JSON.parse(el.dataset.props as string)).toMatchObject({ beforeId: "top" });
    }
  });

  it("renders nothing for an empty series", () => {
    render(<RasterFrameStack id="s" frames={[]} index={0} />);
    expect(screen.queryAllByTestId("source")).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/raster-frame-stack.test.tsx`
Expected: FAIL — `RasterFrameStack` is not exported.

- [ ] **Step 3: Create the component**

`packages/shared/src/components/map/RasterFrameStack.tsx`:

```tsx
import { useRef } from "react";
import { RasterTileLayer } from "./RasterTileLayer";

export interface RasterFrame {
  /** Stable identity for React keys — a collection preview uses the frame's datetime. */
  key: string;
  /** XYZ tile templates for this frame. */
  tiles: string[];
}

export interface RasterFrameStackProps {
  /** Namespace: frame n mounts as maplibre source `${id}-frame-${n}`. */
  id: string;
  /** The series, oldest first. */
  frames: RasterFrame[];
  /** The frame to show. The caller clamps it to `frames`. */
  index: number;
  /** [w, s, e, n] so maplibre asks only for covered tiles. */
  bounds?: [number, number, number, number];
  minzoom?: number;
  maxzoom?: number;
  /** The layer's own opacity (0..1), applied to the visible frames. */
  opacity?: number;
  /** Draw beneath this layer id. */
  beforeId?: string;
}

/**
 * Frames kept mounted ahead of the current one, warming while it plays. ONE:
 * every mounted frame competes for the same handful of connections to the
 * tile server, so a deeper window starves the frame the viewer is actually
 * looking at.
 */
export const RASTER_FRAME_LOOKAHEAD = 1;

/**
 * A time series of raster tile layers shown one frame at a time.
 *
 * Three frames are mounted: the frame shown BEFORE this one, kept painted
 * underneath because tiles take far longer to render than a playback tick
 * and a step to a cold frame would otherwise flash an empty map; the
 * current frame; and one lookahead frame at zero opacity, loading its tiles
 * invisibly. Frames are swapped by opacity with no transition — a fade
 * would smear one timestep into the next.
 *
 * Pure presentation: no fetching, no notion of time. Deciding which frame
 * is current (and when playback may advance) belongs to the caller.
 */
export function RasterFrameStack({
  id,
  frames,
  index,
  bounds,
  minzoom,
  maxzoom,
  opacity = 1,
  beforeId,
}: RasterFrameStackProps) {
  // The frame shown before this one. Tracked in refs rather than state so a
  // step re-renders once, with "previous" already pointing at the frame that
  // was on screen.
  const previousIndex = useRef(index);
  const lastIndex = useRef(index);
  if (lastIndex.current !== index) {
    previousIndex.current = lastIndex.current;
    lastIndex.current = index;
  }

  const count = frames.length;
  if (count === 0) return null;

  // Draw order, bottom to top: previous, current, then the lookahead frames
  // loading invisibly. Deduped — a series shorter than the window would
  // otherwise repeat itself.
  const mounted = [
    ...new Set([
      previousIndex.current % count,
      ...Array.from(
        { length: RASTER_FRAME_LOOKAHEAD + 1 },
        (_, offset) => (index + offset) % count,
      ),
    ]),
  ];

  return (
    <>
      {mounted.map((frameIndex) => (
        <RasterTileLayer
          key={frames[frameIndex].key}
          id={`${id}-frame-${frameIndex}`}
          tiles={frames[frameIndex].tiles}
          bounds={bounds}
          minzoom={minzoom}
          maxzoom={maxzoom}
          opacity={
            frameIndex === index || frameIndex === previousIndex.current % count
              ? opacity
              : 0
          }
          opacityTransitionMs={0}
          beforeId={beforeId}
        />
      ))}
    </>
  );
}
```

Add to `packages/shared/src/index.ts`, after the `RasterTileLayer` lines:

```ts
export { RasterFrameStack, RASTER_FRAME_LOOKAHEAD } from "@shared/components/map/RasterFrameStack";
export type { RasterFrame, RasterFrameStackProps } from "@shared/components/map/RasterFrameStack";
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/raster-frame-stack.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 5: Add the story**

`packages/shared/src/components/map/RasterFrameStack.stories.tsx`:

```tsx
import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { StacMap } from "./StacMap";
import { RasterFrameStack } from "./RasterFrameStack";
import { TimeSlider } from "./TimeSlider";

/**
 * A raster time series shown one frame at a time. Storybook has no tile
 * server, so the "frames" are three public basemaps standing in for three
 * timesteps — enough to see the opacity swap and the invisible lookahead.
 */
const meta: Meta<typeof RasterFrameStack> = {
  component: RasterFrameStack,
  title: "Map/RasterFrameStack",
  parameters: { layout: "fullscreen" },
};

export default meta;
type Story = StoryObj<typeof RasterFrameStack>;

const FRAMES = [
  { key: "osm", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"] },
  { key: "positron", tiles: ["https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"] },
  { key: "dark", tiles: ["https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"] },
];

function Player({ opacity }: { opacity?: number }) {
  const [index, setIndex] = useState(0);
  return (
    <div className="flex flex-col gap-2 p-2">
      <div className="h-[400px] w-full">
        <StacMap>
          <RasterFrameStack id="story" frames={FRAMES} index={index} opacity={opacity} />
        </StacMap>
      </div>
      <TimeSlider labels={FRAMES.map((f) => f.key)} index={index} onIndexChange={setIndex} fps={1} />
    </div>
  );
}

export const Playing: Story = { render: () => <Player /> };
export const Blended: Story = { render: () => <Player opacity={0.5} /> };
```

- [ ] **Step 6: Commit**

```bash
git add packages/shared/src/components/map/RasterFrameStack.tsx packages/shared/src/components/map/RasterFrameStack.stories.tsx packages/shared/src/index.ts app/src/__tests__/raster-frame-stack.test.tsx
git commit -m "feat(map): RasterFrameStack — previous + current + one lookahead, extracted from the preview (V-1)"
```

---

### Task 4: The preview tab consumes `RasterFrameStack`

**Files:**
- Modify: `app/src/components/collections/CollectionPreviewTab.tsx`
- Test: the four preview test files, UNCHANGED.

**Interfaces:**
- Consumes: `RasterFrameStack` (Task 3).
- Produces: nothing new. The tab's frame source ids stay `preview-frame-{n}`.

- [ ] **Step 1: Confirm the preview tests pass before touching the tab**

Run: `cd app && npx vitest run src/__tests__/collection-preview-tab.test.tsx src/__tests__/collection-preview-frames.test.ts src/__tests__/raster-tile-layer.test.tsx src/__tests__/time-slider.test.tsx`
Expected: PASS. Note the count.

- [ ] **Step 2: Replace the mounted-frames block**

In `app/src/components/collections/CollectionPreviewTab.tsx`:

1. Change the shared import to bring in `RasterFrameStack` instead of `RasterTileLayer`:
   ```ts
   import {
     bboxToLngLatBounds,
     EmptyState,
     RasterFrameStack,
     Select,
     SelectContent,
     SelectItem,
     SelectTrigger,
     SelectValue,
     Skeleton,
     StacMap,
     TimeSlider,
   } from "@stac-higher/shared";
   ```
2. Delete the `LOOKAHEAD` constant and its comment, and the unused `frameSourceId` helper.
3. Delete the `previousIndex` / `lastIndex` refs and their comment (the block beginning "The frame shown before this one").
4. Delete the `mounted` array and its comment (the block beginning "Draw order, bottom to top").
5. Add, next to `frames`:
   ```ts
   // What RasterFrameStack mounts: one tile template per timestep, keyed by
   // the datetime the tiler filters on.
   const rasterFrames = useMemo(
     () =>
       asset
         ? frames.map((f) => ({
             key: f.datetime,
             tiles: [collectionTileUrlTemplate(collectionId, asset, f.datetime)],
           }))
         : [],
     [frames, asset, collectionId],
   );
   ```
   Place it AFTER `asset` is computed (it depends on it) and BEFORE the early returns, so the hook order is stable.
6. Replace the `{hintSettled && mounted.map(...)}` JSX inside `<StacMap>` with:
   ```tsx
   {hintSettled && (
     <RasterFrameStack
       id="preview"
       frames={rasterFrames}
       index={index}
       bounds={hint?.bounds}
       minzoom={hint?.minzoom}
       maxzoom={hint?.maxzoom}
     />
   )}
   ```
7. Update the component's doc comment: the paragraph starting "The frames are tile URL templates" stays; append one sentence: "The frame window (previous + current + one lookahead) is `RasterFrameStack`, shared with the /map page."
8. Remove `useRef` from the React import if nothing else in the file uses it (the `mapRef` still does — keep it).

- [ ] **Step 3: Run the preview tests, unchanged**

Run: `cd app && npx vitest run src/__tests__/collection-preview-tab.test.tsx src/__tests__/collection-preview-frames.test.ts src/__tests__/raster-tile-layer.test.tsx src/__tests__/time-slider.test.tsx`
Expected: PASS with the same count as Step 1. `git status` must show none of the four test files modified.

If "warms the next frame at zero opacity" or "keeps the previous frame painted" fails, the stack's dedupe or previous-tracking diverged from the tab's original; fix the stack (Task 3), not the test.

- [ ] **Step 4: Run the whole suite**

Run: `cd app && npx vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/collections/CollectionPreviewTab.tsx
git commit -m "refactor(serving): collection preview mounts RasterFrameStack (V-1)"
```

---

### Task 5: `VectorTileLayer`

**Files:**
- Modify: `packages/shared/src/lib/map/styles.ts` (add `vectorTileLayers`)
- Create: `packages/shared/src/components/map/VectorTileLayer.tsx`
- Create: `packages/shared/src/components/map/VectorTileLayer.stories.tsx`
- Modify: `packages/shared/src/index.ts`
- Test: `app/src/__tests__/vector-tile-layer.test.tsx` (new)

**Interfaces:**
- Produces:
  ```ts
  // styles.ts
  export function vectorTileLayers(
    sourceId: string,
    sourceLayer: string,
    opacity?: number,
  ): { fill: LayerSpecification; line: LayerSpecification; circle: LayerSpecification };
  // ids: `${sourceId}-fill` / `-line` / `-circle`

  // VectorTileLayer.tsx
  export interface VectorTileLayerProps {
    /** Namespace for the source; layers are `${id}-fill|line|circle`. */
    id: string;
    /** A TileJSON document URL — maplibre takes tiles, bounds and zoom from it. */
    url: string;
    /** The MVT layer name inside the tiles. tipg publishes `default`. */
    sourceLayer?: string;   // default "default"
    opacity?: number;       // default 1
    beforeId?: string;
  }
  export function VectorTileLayer(props: VectorTileLayerProps): JSX.Element;
  ```

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/vector-tile-layer.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("react-map-gl/maplibre", () => ({
  Source: ({ children, ...props }: Record<string, unknown> & { children?: React.ReactNode }) => (
    <div data-testid="source" data-props={JSON.stringify(props)}>
      {children}
    </div>
  ),
  Layer: (props: Record<string, unknown>) => (
    <div data-testid="layer" data-props={JSON.stringify(props)} />
  ),
}));

import { VectorTileLayer, vectorTileLayers } from "@stac-higher/shared";

function source(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("source").dataset.props as string);
}
function layers(): Record<string, unknown>[] {
  return screen.getAllByTestId("layer").map((el) => JSON.parse(el.dataset.props as string));
}

describe("VectorTileLayer", () => {
  it("mounts one vector source from the TileJSON url with fill, line and circle layers", () => {
    render(<VectorTileLayer id="v" url="http://tipg/collections/public.roads/tiles/WebMercatorQuad/tilejson.json" />);

    expect(source()).toEqual({
      id: "v",
      type: "vector",
      url: "http://tipg/collections/public.roads/tiles/WebMercatorQuad/tilejson.json",
    });
    expect(layers().map((l) => [l.id, l.type, l["source-layer"]])).toEqual([
      ["v-fill", "fill", "default"],
      ["v-line", "line", "default"],
      ["v-circle", "circle", "default"],
    ]);
  });

  it("draws each geometry type once, via $type filters", () => {
    render(<VectorTileLayer id="v" url="http://t/tilejson.json" />);

    const [fill, line, circle] = layers();
    expect(fill.filter).toEqual(["==", ["geometry-type"], "Polygon"]);
    expect(line.filter).toBeUndefined(); // outlines polygons and draws lines
    expect(circle.filter).toEqual(["==", ["geometry-type"], "Point"]);
  });

  it("takes a custom source layer, opacity and beforeId", () => {
    render(<VectorTileLayer id="v" url="http://t/tilejson.json" sourceLayer="countries" opacity={0.3} beforeId="top" />);

    for (const l of layers()) {
      expect(l).toMatchObject({ "source-layer": "countries", beforeId: "top" });
    }
    const [fill, line, circle] = layers();
    expect(fill.paint).toMatchObject({ "fill-opacity": 0.3 * 0.2 });
    expect(line.paint).toMatchObject({ "line-opacity": 0.3 });
    expect(circle.paint).toMatchObject({ "circle-opacity": 0.3 });
  });

  it("exposes the specs as a function for callers that need the ids", () => {
    const specs = vectorTileLayers("v", "default");
    expect(Object.values(specs).map((s) => s.id)).toEqual(["v-fill", "v-line", "v-circle"]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/vector-tile-layer.test.tsx`
Expected: FAIL — `VectorTileLayer` / `vectorTileLayers` not exported.

- [ ] **Step 3: Add the specs and the component**

Append to `packages/shared/src/lib/map/styles.ts`:

```ts
/** Vector-tile layers (tipg / any MVT): one colour family, three geometry types. */
const VECTOR_COLOR = "#8b5cf6";

/**
 * Fill + line + circle specs over one MVT source layer, each geometry type
 * drawn once: polygons are filled and outlined, lines drawn, points as
 * circles. `opacity` (0..1) scales all three.
 */
export function vectorTileLayers(
  sourceId: string,
  sourceLayer: string,
  opacity = 1,
): { fill: LayerSpecification; line: LayerSpecification; circle: LayerSpecification } {
  return {
    fill: {
      id: `${sourceId}-fill`,
      type: "fill",
      source: sourceId,
      "source-layer": sourceLayer,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": VECTOR_COLOR, "fill-opacity": 0.2 * opacity },
    },
    line: {
      id: `${sourceId}-line`,
      type: "line",
      source: sourceId,
      "source-layer": sourceLayer,
      paint: { "line-color": VECTOR_COLOR, "line-width": 1.5, "line-opacity": opacity },
    },
    circle: {
      id: `${sourceId}-circle`,
      type: "circle",
      source: sourceId,
      "source-layer": sourceLayer,
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-color": VECTOR_COLOR,
        "circle-radius": 4,
        "circle-opacity": opacity,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1,
        "circle-stroke-opacity": opacity,
      },
    },
  };
}
```

`packages/shared/src/components/map/VectorTileLayer.tsx`:

```tsx
import { Source, Layer } from "react-map-gl/maplibre";
import { vectorTileLayers } from "@shared/lib/map/styles";

export interface VectorTileLayerProps {
  /** Namespace for this overlay's maplibre source; layers are `${id}-fill|line|circle`. */
  id: string;
  /**
   * A TileJSON document URL. maplibre fetches it and takes the tile
   * templates, bounds and zoom range from it — which is what tipg publishes
   * at `/collections/{id}/tiles/WebMercatorQuad/tilejson.json`.
   */
  url: string;
  /** The MVT layer name inside the tiles. tipg writes `default`. */
  sourceLayer?: string;
  /** 0..1, default 1. */
  opacity?: number;
  /** Draw beneath this layer id. */
  beforeId?: string;
}

/**
 * An OGC API Tiles vector overlay for `StacMap`: one vector source, three
 * layers so polygons, lines and points each draw once. Pure presentation —
 * whether the tile server exists or answers is the caller's concern, and a
 * TileJSON that fails to load leaves the map as it was.
 */
export function VectorTileLayer({
  id,
  url,
  sourceLayer = "default",
  opacity = 1,
  beforeId,
}: VectorTileLayerProps) {
  const { fill, line, circle } = vectorTileLayers(id, sourceLayer, opacity);
  return (
    <Source id={id} type="vector" url={url}>
      <Layer {...fill} beforeId={beforeId} />
      <Layer {...line} beforeId={beforeId} />
      <Layer {...circle} beforeId={beforeId} />
    </Source>
  );
}
```

Exports in `packages/shared/src/index.ts`: add `vectorTileLayers` to the styles block, and after the `RasterFrameStack` lines:

```ts
export { VectorTileLayer } from "@shared/components/map/VectorTileLayer";
export type { VectorTileLayerProps } from "@shared/components/map/VectorTileLayer";
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app && npx vitest run src/__tests__/vector-tile-layer.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Add the story**

`packages/shared/src/components/map/VectorTileLayer.stories.tsx`:

```tsx
import type { Meta, StoryObj } from "@storybook/react-vite";
import { StacMap } from "./StacMap";
import { VectorTileLayer } from "./VectorTileLayer";

/**
 * The overlay the /map page draws for a tipg vector collection. Storybook
 * has no tipg, so these stories use MapLibre's public demo tiles, whose
 * TileJSON carries `countries` (polygons), `geolines` (lines) and
 * `centroids` (points) — one of each geometry type the layer handles.
 */
const meta: Meta<typeof VectorTileLayer> = {
  component: VectorTileLayer,
  title: "Map/VectorTileLayer",
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div className="h-[400px] w-full">
        <StacMap>
          <Story />
        </StacMap>
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof VectorTileLayer>;

const DEMO_TILEJSON = "https://demotiles.maplibre.org/tiles/tiles.json";

export const Polygons: Story = {
  args: { id: "demo", url: DEMO_TILEJSON, sourceLayer: "countries" },
};

export const Points: Story = {
  args: { id: "demo", url: DEMO_TILEJSON, sourceLayer: "centroids" },
};

export const Blended: Story = {
  args: { id: "demo", url: DEMO_TILEJSON, sourceLayer: "countries", opacity: 0.4 },
};
```

- [ ] **Step 6: Commit**

```bash
git add packages/shared/src/lib/map/styles.ts packages/shared/src/components/map/VectorTileLayer.tsx packages/shared/src/components/map/VectorTileLayer.stories.tsx packages/shared/src/index.ts app/src/__tests__/vector-tile-layer.test.tsx
git commit -m "feat(map): VectorTileLayer for OGC API Tiles vector sources (V-1)"
```

---

### Task 6: tipg URL builders, and the two inline env reads replaced

**Files:**
- Modify: `app/src/lib/serving/urls.ts` (append after `collectionViewerUrl`)
- Modify: `app/src/components/collections/SettingsTab.tsx:56-58, 399`
- Modify: `app/src/components/collections/ProductOverview.tsx:54-56, 250`
- Test: `app/src/__tests__/serving-urls.test.ts` (extend)

**Interfaces:**
- Produces:
  ```ts
  export function tipgBaseUrl(env?: Record<string, string | undefined>): string;   // PUBLIC_TIPG_URL, default http://localhost:8085, no trailing slash
  export function tipgLandingUrl(base?: string): string;                          // `${base}/`
  export function tipgCollectionsUrl(base?: string): string;                      // `${base}/collections`
  export function tipgTileJsonUrl(collectionId: string, base?: string): string;   // `${base}/collections/${enc(id)}/tiles/WebMercatorQuad/tilejson.json`
  ```

- [ ] **Step 1: Write the failing tests**

Append to the `describe("serving urls", …)` block in `app/src/__tests__/serving-urls.test.ts`, and extend its import:

```ts
import {
  titilerBaseUrl,
  itemTileJsonUrl,
  itemViewerUrl,
  collectionInfoUrl,
  tipgBaseUrl,
  tipgLandingUrl,
  tipgCollectionsUrl,
  tipgTileJsonUrl,
} from "@/lib/serving/urls";
```

```ts
  it("defaults the tipg base and strips a trailing slash", () => {
    expect(tipgBaseUrl({})).toBe("http://localhost:8085");
    expect(tipgBaseUrl({ PUBLIC_TIPG_URL: "https://features.example//" })).toBe(
      "https://features.example",
    );
  });

  it("builds the tipg landing, collections and tilejson urls", () => {
    expect(tipgLandingUrl("http://f")).toBe("http://f/");
    expect(tipgCollectionsUrl("http://f")).toBe("http://f/collections");
    expect(tipgTileJsonUrl("public.roads", "http://f")).toBe(
      "http://f/collections/public.roads/tiles/WebMercatorQuad/tilejson.json",
    );
    expect(tipgTileJsonUrl("a b", "http://f")).toContain("/collections/a%20b/");
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/serving-urls.test.ts`
Expected: FAIL — the tipg functions are not exported.

- [ ] **Step 3: Add the builders**

Append to `app/src/lib/serving/urls.ts`:

```ts
// ---------------------------------------------------------------------------
// tipg (OGC API Features + Tiles for vector tables) — same rules as titiler:
// one browser-facing base, link-level only (docs/serving.md).

const DEFAULT_TIPG_URL = "http://localhost:8085";

/** The browser-facing tipg base, without a trailing slash. */
export function tipgBaseUrl(
  env: Record<string, string | undefined> = import.meta.env as Record<
    string,
    string | undefined
  >,
): string {
  return (env.PUBLIC_TIPG_URL ?? DEFAULT_TIPG_URL).replace(/\/+$/, "");
}

/** tipg's landing page — the stack-wide vector surface the product pages link. */
export function tipgLandingUrl(base: string = tipgBaseUrl()): string {
  return `${base}/`;
}

/** The collections list (tables + functions tipg serves). */
export function tipgCollectionsUrl(base: string = tipgBaseUrl()): string {
  return `${base}/collections`;
}

/** TileJSON for one tipg collection's vector tiles (WebMercatorQuad). */
export function tipgTileJsonUrl(collectionId: string, base: string = tipgBaseUrl()): string {
  return `${base}/collections/${enc(collectionId)}/tiles/WebMercatorQuad/tilejson.json`;
}
```

Also update the file's header comment's first line to: `Tile-server (titiler-pgstac) and tipg URL builders — the ONE place the browser-facing bases live, …` (keep the rest).

- [ ] **Step 4: Replace the inline reads**

In `app/src/components/collections/SettingsTab.tsx`: delete lines 56–58 (the comment and `const TIPG_URL = …`), add `tipgLandingUrl` to the existing `@/lib/serving/urls` import, and at line 399 change `href={`${TIPG_URL}/`}` to `href={tipgLandingUrl()}`.

In `app/src/components/collections/ProductOverview.tsx`: delete lines 54–56 likewise, add `tipgLandingUrl` to the `@/lib/serving/urls` import, and at line 250 change `href={`${TIPG_URL}/`}` to `href={tipgLandingUrl()}`.

Confirm no other reference remains: `grep -rn "PUBLIC_TIPG_URL\|TIPG_URL" app/src` must list only `urls.ts`.

- [ ] **Step 5: Run the tests**

Run: `cd app && npx vitest run src/__tests__/serving-urls.test.ts && npx vitest run`
Expected: PASS. (Any existing component test that rendered those two components still passes: the rendered href is byte-identical.)

- [ ] **Step 6: Commit**

```bash
git add app/src/lib/serving/urls.ts app/src/components/collections/SettingsTab.tsx app/src/components/collections/ProductOverview.tsx app/src/__tests__/serving-urls.test.ts
git commit -m "feat(serving): tipg url builders; product pages stop reading PUBLIC_TIPG_URL inline (V-1)"
```

---

### Task 7: Verify, record, merge

**Files:**
- Modify: `TODO.md` (V-1 checkbox + queue-table state)
- Modify: `docs/FEATURES.md` (one line under the serving / preview entry)

- [ ] **Step 1: Full gate**

Run from the worktree root: `npm run verify`
Expected: typecheck, build and unit tests all green. Fix anything reported on this branch before continuing.

- [ ] **Step 2: Confirm the preview tests are untouched**

Run: `git diff ai/main --stat -- app/src/__tests__/collection-preview-tab.test.tsx app/src/__tests__/collection-preview-frames.test.ts app/src/__tests__/raster-tile-layer.test.tsx app/src/__tests__/time-slider.test.tsx`
Expected: no output.

- [ ] **Step 3: Storybook smoke (optional, needs a browser)**

`cd packages/shared && npm run storybook` and open `Map/RasterFrameStack` and `Map/VectorTileLayer`. Not a gate — the stories fetch public tiles; if the network is unavailable, skip and say so in the report.

- [ ] **Step 4: Record**

`TODO.md`: tick V-1 (`- [x]`) and change the V row in the queue table to `V-1 merged 2026-09-04; V-2 next`.

`docs/FEATURES.md`: find the collection preview entry (added 2026-09-04, mentions `CollectionPreviewTab`) and append one sentence: "Its frame window is the shared `RasterFrameStack` (V-1, 2026-09-04), alongside the id-namespaced `FootprintLayer` and the new `VectorTileLayer` the `/map` page builds on."

```bash
git add TODO.md docs/FEATURES.md
git commit -m "docs: V-1 done — shared map pieces + preview refactor"
```

- [ ] **Step 5: Merge**

```bash
cd <repo root>
git checkout ai/main
git merge ai/v1-shared-map --no-ff -m "Merge ai/v1-shared-map: V-1 shared map pieces + preview refactor"
npm run verify
git worktree remove .claude/worktrees/v1-shared-map
git branch -d ai/v1-shared-map
```

Do not push `ai/main` (it stays local by the lead's standing instruction).

---

## Self-review

**Spec coverage (§3, §6 V-1 rows, §10 V-1):**
- `RasterFrameStack` with the stated props, lookahead constant and reason → Task 3. ✓
- `FootprintLayer` `id` / `opacity` / `beforeId`; styles as functions of the source id → Tasks 1–2. ✓
- `VectorTileLayer` (`url`, `sourceLayer` default `default`, fill/line/circle with geometry filters, theme colours) → Task 5. The spec says "theme-token colours from `styles.ts`"; the existing footprint/extent specs use hex literals, not CSS tokens (maplibre paint cannot read CSS variables), so the vector layer follows the file's convention with a hex literal. ✓
- `tipgBaseUrl`, `tipgCollectionsUrl`, `tipgTileJsonUrl`, the two inline reads replaced → Task 6 (plus `tipgLandingUrl`, because that is the URL the two components actually build). ✓
- Preview tab consumes `RasterFrameStack`; four test files unchanged → Task 4, checked again in Task 7. ✓
- Stories for both new components → Tasks 3 and 5. ✓
- Source-layer name verified live is V-4's job, not V-1's; the default `default` is what tipg documents. ✓

**Placeholders:** none. **Type consistency:** `RasterFrame { key, tiles }` in Task 3 matches Task 4's `rasterFrames`; `footprintLayers(sourceId, opacity)` in Task 1 matches Task 2's call; `vectorTileLayers(sourceId, sourceLayer, opacity)` in Task 5 matches its component; frame source ids `${id}-frame-${n}` with `id="preview"` reproduce the `preview-frame-{n}` the preview tests assert.
