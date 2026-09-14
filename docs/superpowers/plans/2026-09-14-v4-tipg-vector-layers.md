# V-4 · tipg vector layers + docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The `/map` page's Add-layer popover gains a "Vector tiles" section fed by tipg's `/collections`, each entry adding a `vector` layer drawn by the shared `VectorTileLayer` from tipg's WebMercatorQuad TileJSON; the V-1 carried-forward cleanups land; the docs and the spec's §8 deferrals are recorded.

**Architecture:** One quiet TanStack query, `useTipgCollections` (`serving/queries.ts`, beside `useItemTileJson`: `credentials: "omit"`, `retry: false`, `enabled` only while the popover is open), lists tipg's collections. `AddLayerPopover` renders the section from it — a one-line "no vector tiles published" note when the list is empty or the query failed. `MapPage` gains `addVectorLayer` (kind `vector`, `sourceId` = the tipg collection id, no camera fit). `MapLayerView` becomes a dispatcher: `vector` layers render a new `VectorLayerView` (the shared `VectorTileLayer` with `tipgTileJsonUrl(sourceId)` and source layer `default`) and never call `useLayerData`; STAC kinds keep today's body as `StacLayerView`. The shared package gains `vectorTileLayerIds` (the ids-only twin of `vectorTileLayers`) which `layerAnchorId` uses, and loses the consumerless `footprintFillLayer` / `footprintLineLayer` exports and the stale app proxy.

**Tech Stack:** Astro 7 + React 19, TanStack Query, react-map-gl/maplibre, vitest; tipg 1.0.1 (running on :8085).

**Spec:** `docs/superpowers/specs/2026-09-04-map-page-design.md` §4.5 (Vector tiles section), §4.7 (degradation), §7 (docs), §8 (deferred), §11; `TODO.md` V-4 slice + "Carried forward from the V-1 final review — V-4".

## Global Constraints

- **Worktree:** `git worktree add .claude/worktrees/v4-vector -b ai/v4-vector ai/main` (after `ai/v3-anchor-fix` has merged — Task 0 checks), then `npm install` at the worktree root.
- **Gate:** `npm run verify` from the worktree root before each commit. Teammates never run e2e, the dev server or Docker; the lead runs the whole e2e suite and the live check after the merge.
- **Verified against the running tipg (lead, 2026-09-09):** `GET :8085/collections/{id}/tiles/WebMercatorQuad/tilejson.json` returns `tilejson 3.0.0` with `vector_layers: [{ id: "default", ... }]` and `tiles: [".../tiles/WebMercatorQuad/{z}/{x}/{y}"]` — the MVT source-layer name is **`default`**, the `VectorTileLayer` default. `GET :8085/collections` returned six `public.*` PostGIS function collections (`postgis_srs`, `st_hexagongrid`, `st_squaregrid`, …) and no real table — the spec §8 "seeded demo vector table" deferral stands.
- **Spec §4.5 copy and gating:** section title "Vector tiles"; each entry is a `Hexagon`-icon button; a `(vector, id)` pair already on the map shows "Added" and is disabled; an unreachable tipg or an empty list renders exactly one muted line "no vector tiles published" — never an error.
- **Quiet query rule (serving/queries.ts header):** `credentials: "omit"`, `retry: false`, callers ignore `error`. The list is fetched only while the popover is open (`enabled: open`), so a page that never opens the picker never calls tipg.
- **Carried forward (V-1 final review, V-4 bullet):** delete `footprintFillLayer` / `footprintLineLayer` from `packages/shared/src/lib/map/styles.ts` and `packages/shared/src/index.ts`, and delete the stale proxy `app/src/lib/map/styles.ts` (no app consumer — verified by grep); add an ids-only `vectorTileLayerIds(sourceId)` beside `vectorTileLayers` and use it in `layerAnchorId`; assert the default-opacity paint values (fill 0.2, line 1, circle 1) in `vector-tile-layer.test.tsx`; `map-styles.test.ts` switches its legacy-id assertions to `footprintLayers(FOOTPRINT_SOURCE)`.
- Vector layers are NOT time-aware (spec §4.4): they contribute no frames and ignore the axis; they are not hoverable in V-4 (`interactiveLayerIds` unchanged — footprints only).
- **No new dependencies; never edit `components/ui/`;** app imports shared code from `@stac-higher/shared`; `@shared/*` only inside the shared package. The three untouchable test files stay unchanged.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL>
  ```

---

### Task 0: Precondition

- [ ] `git log --oneline ai/main -15 | grep -i 'anchor-fix'` shows the V-3 anchor-fix merge, and `grep -n useMap packages/shared/src/components/map/RasterFrameStack.tsx` prints a line. If not, STOP and report.

---

### Task 1: `useTipgCollections`, `vectorTileLayerIds`, and the V-1 carried-forward cleanups

**Files:**
- Modify: `app/src/lib/query/keys.ts` — `servingKeys` gains `tipgCollections`
- Modify: `app/src/lib/serving/queries.ts` — `TipgCollection`, `fetchTipgCollections`, `useTipgCollections`
- Modify: `packages/shared/src/lib/map/styles.ts` — add `vectorTileLayerIds`; delete `footprintFillLayer` / `footprintLineLayer`
- Modify: `packages/shared/src/index.ts` — export `vectorTileLayerIds`; drop the two deleted exports
- Delete: `app/src/lib/map/styles.ts` (stale proxy, no consumer)
- Modify: `app/src/lib/map/state.ts` — `layerAnchorId` vector case uses `vectorTileLayerIds(layer.id).fill`
- Test: `app/src/__tests__/serving-tipg.test.ts` (new), `app/src/__tests__/map-styles.test.ts` (legacy assertions rewritten), `app/src/__tests__/vector-tile-layer.test.tsx` (append one test), `app/src/__tests__/map-state.test.ts` (unchanged expectation `a-fill` — verify it still passes)

**Interfaces:**
- Produces:
  ```ts
  // serving/queries.ts
  export interface TipgCollection { id: string; title?: string; description?: string }
  export async function fetchTipgCollections(): Promise<TipgCollection[]>   // GET tipgCollectionsUrl(), credentials omit; throws on !ok or a non-array `collections`
  export function useTipgCollections(enabled: boolean)                       // useQuery({ queryKey: servingKeys.tipgCollections(), queryFn: fetchTipgCollections, enabled, retry: false, staleTime: STALE_MS })
  // keys.ts
  servingKeys.tipgCollections: () => [...servingKeys.all(), "tipg-collections"] as const
  // shared styles.ts
  export function vectorTileLayerIds(sourceId: string): { fill: string; line: string; circle: string }  // `${sourceId}-fill|line|circle`
  ```

- [ ] **Step 1: Failing tests.** Create `app/src/__tests__/serving-tipg.test.ts`:

```ts
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fetchTipgCollections } from "@/lib/serving/queries";
import { servingKeys } from "@/lib/query/keys";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("fetchTipgCollections", () => {
  it("lists tipg's collections without credentials", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        collections: [
          { id: "public.st_hexagongrid", title: "public.st_hexagongrid", links: [] },
          { id: "public.roads", title: "Roads", description: "OSM roads", links: [] },
        ],
      }),
    });
    const list = await fetchTipgCollections();
    expect(list).toEqual([
      { id: "public.st_hexagongrid", title: "public.st_hexagongrid", description: undefined },
      { id: "public.roads", title: "Roads", description: "OSM roads" },
    ]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/collections$/);
    expect(init).toMatchObject({ credentials: "omit" });
  });

  it("throws on a non-2xx so the query lands in error, quietly", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({}) });
    await expect(fetchTipgCollections()).rejects.toThrow(/503/);
  });

  it("throws when the body carries no collections array", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ nope: true }) });
    await expect(fetchTipgCollections()).rejects.toThrow(/collections/);
  });
});

describe("servingKeys.tipgCollections", () => {
  it("is namespaced under serving and catalog-agnostic", () => {
    expect(servingKeys.tipgCollections()).toEqual(["serving", "tipg-collections"]);
  });
});
```

Append to `app/src/__tests__/vector-tile-layer.test.tsx` (inside the describe):
```ts
  it("draws at the documented default opacities (fill 0.2, line 1, circle 1)", () => {
    const { fill, line, circle } = vectorTileLayers("v", "default");
    expect(fill.paint).toMatchObject({ "fill-opacity": 0.2 });
    expect(line.paint).toMatchObject({ "line-opacity": 1 });
    expect(circle.paint).toMatchObject({ "circle-opacity": 1 });
  });

  it("exposes the layer ids without building specs", () => {
    expect(vectorTileLayerIds("v")).toEqual({ fill: "v-fill", line: "v-line", circle: "v-circle" });
  });
```
(import `vectorTileLayerIds` and `vectorTileLayers` from `@stac-higher/shared` at the top if not already imported.)

Rewrite `app/src/__tests__/map-styles.test.ts`'s two uses of the legacy exports: in "keeps the legacy ids for the default source" replace the three `footprintFillLayer`/`footprintLineLayer` assertions with `const legacy = footprintLayers(FOOTPRINT_SOURCE); expect(legacy.fill.id).toBe("stac-footprint-fill"); expect(legacy.line.id).toBe("stac-footprint-line"); expect(legacy.fill.source).toBe(FOOTPRINT_SOURCE);`; in "emits the plain hover expression at full opacity" drop the two `toEqual(footprint…Layer.paint)` lines (the explicit expression assertions below them already pin the shape) and remove the two names from the import.

- [ ] **Step 2:** `cd app && npx vitest run src/__tests__/serving-tipg.test.ts src/__tests__/vector-tile-layer.test.tsx src/__tests__/map-styles.test.ts` → FAIL (missing exports).

- [ ] **Step 3: Implement.**

`keys.ts`: add `tipgCollections: () => [...servingKeys.all(), "tipg-collections"] as const,` to `servingKeys`.

`serving/queries.ts`: import `tipgCollectionsUrl` from `./urls`; add
```ts
/** The subset of a tipg collection the picker shows. */
export interface TipgCollection {
  id: string;
  title?: string;
  description?: string;
}

export async function fetchTipgCollections(): Promise<TipgCollection[]> {
  // No credentials: tipg is a different origin and needs none (docs/serving.md).
  const res = await fetch(tipgCollectionsUrl(), { credentials: "omit" });
  if (!res.ok) {
    throw new Error(`tipg collections request failed: ${res.status}`);
  }
  const doc = (await res.json()) as { collections?: unknown };
  if (!Array.isArray(doc.collections)) {
    throw new Error("tipg collections document carries no collections array");
  }
  return doc.collections
    .filter((c): c is { id: string; title?: string; description?: string } =>
      typeof c === "object" && c !== null && typeof (c as { id?: unknown }).id === "string",
    )
    .map((c) => ({ id: c.id, title: c.title, description: c.description }));
}

/**
 * tipg's collection list for the /map Add-layer picker (spec §4.5). Runs only
 * while the picker is open; a miss (no tipg, an error) is "no vector tiles
 * published", never an error banner — same quiet rule as the tile server.
 */
export function useTipgCollections(enabled: boolean) {
  return useQuery({
    queryKey: servingKeys.tipgCollections(),
    queryFn: fetchTipgCollections,
    enabled,
    retry: false,
    staleTime: STALE_MS,
  });
}
```

`packages/shared/src/lib/map/styles.ts`: delete the `footprintFillLayer` and `footprintLineLayer` `export const` blocks (and their comment); add before `vectorTileLayers`:
```ts
/** The three layer ids `vectorTileLayers` mints for a source, without building specs. */
export function vectorTileLayerIds(sourceId: string): { fill: string; line: string; circle: string } {
  return { fill: `${sourceId}-fill`, line: `${sourceId}-line`, circle: `${sourceId}-circle` };
}
```
and make `vectorTileLayers` use it for its three `id`s. `packages/shared/src/index.ts`: remove the two deleted names from the export list; add `vectorTileLayerIds`. Delete `app/src/lib/map/styles.ts`. `app/src/lib/map/state.ts`: import `vectorTileLayerIds` from `@stac-higher/shared`; the `vector` case of `layerAnchorId` returns `vectorTileLayerIds(layer.id).fill` and its V-4 comment is removed.

- [ ] **Step 4:** the three test files + `map-state.test.ts` → pass; `npm run verify` → green (the `astro check` must not report a dangling import of the deleted proxy — grep `@/lib/map/styles` under `app/src` returns nothing). Commit: `feat(map): useTipgCollections + vectorTileLayerIds; drop the consumerless footprint layer exports and the stale styles proxy (V-4)`.

---

### Task 2: The "Vector tiles" section and the vector layer row

**Files:**
- Modify: `app/src/components/map/AddLayerPopover.tsx` — props gain `onAddVector: (collection: TipgCollection) => void`; the section replaces the V-4 marker comment; `useTipgCollections(open)` inside the component
- Modify: `app/src/components/map/LayerPanel.tsx` — thread `onAddVector`
- Modify: `app/src/components/map/MapPage.tsx` — `addVectorLayer`, passed as `onAddVector`
- Modify: `app/src/components/map/LayerRow.tsx` — `KIND_ICON` becomes a typed `Record<LayerKind, LucideIcon>` with `vector: Hexagon`; the `as keyof` cast goes
- Modify: `app/src/__tests__/map-page.test.tsx` — the `@/lib/serving/queries` mock gains `useTipgCollections` (default `{ data: undefined, isError: false }`) — mocks only, plus NEW tests at the end
- Test: `app/src/__tests__/map-add-layer-vector.test.tsx` (new)

**Interfaces:**
- Consumes: `useTipgCollections(enabled)`, `TipgCollection` (Task 1).
- Produces: `AddLayerPopoverProps.onAddVector`, `LayerPanelProps.onAddVector`, test ids `map-add-vector-${id}` and `map-vector-empty`.

- [ ] **Step 1: Failing tests.** Create `app/src/__tests__/map-add-layer-vector.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const tipgMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/serving/queries", () => ({
  useTipgCollections: tipgMock,
  useItemTileJson: () => ({ data: undefined, isFetched: false }),
}));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: () => ({ data: { servingEnabled: false } }),
}));
vi.mock("@/lib/query/items", () => ({ useItems: () => ({ data: undefined }) }));

import { AddLayerPopover } from "@/components/map/AddLayerPopover";

function renderPicker(onAddVector = vi.fn(), isAdded = () => false) {
  render(
    <AddLayerPopover
      collections={[]}
      catalogUrl="http://stac"
      isAdded={isAdded}
      onAdd={vi.fn()}
      onAddVector={onAddVector}
    />,
  );
  fireEvent.click(screen.getByTestId("map-add-layer"));
  return onAddVector;
}

beforeEach(() => {
  tipgMock.mockReset();
});

describe("AddLayerPopover — Vector tiles", () => {
  it("does not ask tipg until the picker opens", () => {
    tipgMock.mockReturnValue({ data: undefined, isError: false });
    render(
      <AddLayerPopover collections={[]} catalogUrl="u" isAdded={() => false} onAdd={vi.fn()} onAddVector={vi.fn()} />,
    );
    expect(tipgMock).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByTestId("map-add-layer"));
    expect(tipgMock).toHaveBeenLastCalledWith(true);
  });

  it("lists tipg's collections and adds one as a vector layer", () => {
    tipgMock.mockReturnValue({
      data: [{ id: "public.roads", title: "Roads" }, { id: "public.st_hexagongrid" }],
      isError: false,
    });
    const onAddVector = renderPicker();
    expect(screen.getByText("Vector tiles")).toBeInTheDocument();
    expect(screen.getByText("Roads")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("map-add-vector-public.roads"));
    expect(onAddVector).toHaveBeenCalledWith({ id: "public.roads", title: "Roads" });
  });

  it("shows an added collection as Added and disabled", () => {
    tipgMock.mockReturnValue({ data: [{ id: "public.roads", title: "Roads" }], isError: false });
    renderPicker(vi.fn(), (kind, id) => kind === "vector" && id === "public.roads");
    const button = screen.getByTestId("map-add-vector-public.roads");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Added");
  });

  it("says 'no vector tiles published' when tipg is unreachable or empty — never an error", () => {
    tipgMock.mockReturnValue({ data: undefined, isError: true });
    renderPicker();
    expect(screen.getByTestId("map-vector-empty")).toHaveTextContent("no vector tiles published");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the same note for an empty list", () => {
    tipgMock.mockReturnValue({ data: [], isError: false });
    renderPicker();
    expect(screen.getByTestId("map-vector-empty")).toBeInTheDocument();
  });
});
```
(`isAdded`'s signature is `(kind: LayerKind, sourceId: string) => boolean` — the popover passes `"vector"` and the tipg id.)

Append to `app/src/__tests__/map-page.test.tsx` (new tests only; add `useTipgCollections: useTipgMock` to the existing `@/lib/serving/queries` mock with a hoisted `useTipgMock` defaulting to `{ data: undefined, isError: false }` in `beforeEach`):
```tsx
  it("adds a tipg collection as a vector layer with the hexagon icon and no camera fit", () => {
    useTipgMock.mockReturnValue({ data: [{ id: "public.roads", title: "Roads" }], isError: false });
    render(<MapPage />);
    fireEvent.click(screen.getByTestId("map-add-layer"));
    fireEvent.click(screen.getByTestId("map-add-vector-public.roads"));
    const row = screen.getByTestId("map-layer-row");
    expect(row).toHaveAttribute("data-layer-id", "layer-1");
    expect(within(row).getByTestId("map-layer-icon-vector")).toBeInTheDocument();
    expect(within(row).getByText("Roads")).toBeInTheDocument();
    expect(mapProps.current /* the mocked Map's props */).toBeDefined();
  });
```
(Adapt the last assertion to whatever the file's existing "first-add fitBounds" test observes — assert `fitBounds` was NOT called for a vector first-add; read that test and mirror its spy.)

- [ ] **Step 2:** run both files → FAIL.

- [ ] **Step 3: Implement.**

`AddLayerPopover.tsx`: add `onAddVector: (collection: TipgCollection) => void` to the props; `import { Hexagon, Plus } from "lucide-react"`; `import { useTipgCollections, type TipgCollection } from "@/lib/serving/queries"`; inside the component `const { data: vectorCollections, isError: vectorError } = useTipgCollections(open);`. Replace the V-4 marker comment with:

```tsx
        <div className="px-3 pb-1 pt-2 text-[10px] font-bold uppercase tracking-[0.08em] text-muted-foreground">
          Vector tiles
        </div>
        {vectorError || !vectorCollections || vectorCollections.length === 0 ? (
          <p className="px-3 pb-3 text-sm text-muted-foreground" data-testid="map-vector-empty">
            no vector tiles published
          </p>
        ) : (
          <ul className="max-h-60 overflow-auto pb-2">
            {vectorCollections.map((collection) => {
              const added = isAdded("vector", collection.id);
              return (
                <li key={collection.id} className="flex items-center justify-between gap-2 px-3 py-1.5">
                  <span className="min-w-0 truncate text-sm" title={collection.description}>
                    {collection.title ?? collection.id}
                  </span>
                  <Button
                    size="xs"
                    variant={added ? "secondary" : "outline"}
                    disabled={added}
                    data-testid={`map-add-vector-${collection.id}`}
                    onClick={() => {
                      onAddVector(collection);
                      setOpen(false);
                    }}
                  >
                    <Hexagon />
                    {added ? "Added" : "Vector"}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
```
(Use the same `size`/`variant` values `AddLayerCollectionRow` uses for its Footprints/Imagery buttons — read that file and copy them; the copy "Added" must match.) Update the header comment: the Vector tiles section is fed by `useTipgCollections` while the picker is open; a miss is one muted line.

`LayerPanel.tsx`: `onAddVector: (collection: TipgCollection) => void` in `LayerPanelProps`, passed through to `AddLayerPopover`.

`MapPage.tsx`: after `addLayer` add
```tsx
  // tipg collections carry no STAC extent, so the first-add camera fit (spec
  // §4.6) is a STAC-layer courtesy only; a vector layer keeps the camera.
  const addVectorLayer = useCallback((collection: TipgCollection) => {
    dispatch({
      type: "add",
      layer: {
        id: `layer-${nextLayerId.current++}`,
        kind: "vector",
        sourceId: collection.id,
        title: collection.title ?? collection.id,
        visible: true,
        opacity: 1,
      },
    });
  }, []);
```
and pass `onAddVector={addVectorLayer}` to `LayerPanel`.

`LayerRow.tsx`: `import { Hexagon, Image, Layers } from "lucide-react"; import type { LucideIcon } from "lucide-react";` and `const KIND_ICON: Record<LayerKind, LucideIcon> = { footprints: Layers, imagery: Image, vector: Hexagon };` then `const Icon = KIND_ICON[layer.kind];` (drop the cast and the `?? Layers`). Keep the `data-testid={\`map-layer-icon-${layer.kind}\`}`.

- [ ] **Step 4:** the two test files → pass; `npm run verify` → green. Commit: `feat(map): Vector tiles section from tipg's /collections; vector layer rows (V-4)`.

---

### Task 3: Draw the vector layer — `MapLayerView` dispatches to `VectorLayerView`

**Files:**
- Modify: `app/src/components/map/MapLayerView.tsx` — rename today's component body to `StacLayerView` (same props, unexported), add `VectorLayerView`, and make the exported `MapLayerView` a pure dispatcher on `layer.kind`
- Test: `app/src/__tests__/map-layer-view.test.tsx` (append), `app/src/__tests__/map-page-imagery.test.tsx` (append one: a vector layer contributes no ticks)

**Interfaces:**
- `MapLayerViewProps` unchanged (Task 6 of V-3 passes them field for field). `VectorLayerView({ layer, beforeId })` renders `<VectorTileLayer id={layer.id} url={tipgTileJsonUrl(layer.sourceId)} sourceLayer="default" opacity={layer.opacity} visible={layer.visible} beforeId={beforeId} />`.

- [ ] **Step 1: Failing tests.** Append to `map-layer-view.test.tsx` (the file mocks `react-map-gl/maplibre` recording `Source`/`Layer` props and mocks `useLayerData` — read its helpers and reuse them):

```tsx
describe("MapLayerView — vector", () => {
  it("draws a tipg collection through VectorTileLayer with the default source layer and never asks useLayerData", () => {
    useLayerDataMock.mockClear();
    render(
      <MapLayerView
        layer={{ id: "v1", kind: "vector", sourceId: "public.roads", title: "Roads", visible: true, opacity: 0.5 }}
        catalogUrl="u"
        frameSpan={50}
        tickInstant={null}
        beforeId="above"
        onFramesChange={vi.fn()}
        onFramesRemove={vi.fn()}
      />,
    );
    expect(useLayerDataMock).not.toHaveBeenCalled();
    const source = sources().find((s) => s.id === "v1");
    expect(source?.type).toBe("vector");
    expect(source?.url).toMatch(/\/collections\/public\.roads\/tiles\/WebMercatorQuad\/tilejson\.json$/);
    const fill = layers().find((l) => l.id === "v1-fill");
    expect(fill?.["source-layer"]).toBe("default");
    expect(fill?.paint).toMatchObject({ "fill-opacity": 0.2 * 0.5 });
    expect(fill?.beforeId).toBe("above");
  });

  it("reports no frames for a vector layer", () => {
    const onFramesChange = vi.fn();
    render(<MapLayerView layer={{ id: "v1", kind: "vector", sourceId: "public.roads", title: "Roads", visible: true, opacity: 1 }} catalogUrl="u" frameSpan={50} tickInstant={null} beforeId={undefined} onFramesChange={onFramesChange} onFramesRemove={vi.fn()} />);
    expect(onFramesChange).not.toHaveBeenCalled();
  });
});
```
(`sources()` / `layers()` — use the file's existing recording helpers, whatever they are named; `useLayerDataMock` — the file's existing hoisted mock of `@/components/map/useLayerData`.)

Append to `map-page-imagery.test.tsx` one test "a vector layer docks no time bar": mock `useTipgCollections` in that file's `@/lib/serving/queries` mock (default `{ data: [{ id: "public.roads", title: "Roads" }], isError: false }`), add the vector layer through the picker, and assert `screen.queryByTestId("map-time-bar")` is null.

- [ ] **Step 2:** run → FAIL (vector renders nothing / `useLayerData` called).

- [ ] **Step 3: Implement.** In `MapLayerView.tsx`:

```tsx
import { VectorTileLayer } from "@stac-higher/shared";
import { tipgTileJsonUrl } from "@/lib/serving/urls";

/** Dispatch on kind: vector layers have no items, no frames and no axis. */
export function MapLayerView(props: MapLayerViewProps) {
  if (props.layer.kind === "vector") {
    return <VectorLayerView layer={props.layer} beforeId={props.beforeId} />;
  }
  return <StacLayerView {...props} />;
}

function VectorLayerView({ layer, beforeId }: { layer: MapLayer; beforeId?: string }) {
  return (
    <VectorTileLayer
      id={layer.id}
      url={tipgTileJsonUrl(layer.sourceId)}
      sourceLayer="default"
      opacity={layer.opacity}
      visible={layer.visible}
      beforeId={beforeId}
    />
  );
}

function StacLayerView({ layer, catalogUrl, frameSpan, tickInstant, beforeId, onFramesChange, onFramesRemove }: MapLayerViewProps) {
  // ← today's MapLayerView body, unchanged
}
```
The dispatcher calls no hooks, so the per-kind components each keep a stable hook order. Update the module doc to say so. Remove any trailing `return null` for vector in the old body if present.

- [ ] **Step 4:** tests → pass; `npm run verify` → green. Commit: `feat(map): vector layers draw through VectorTileLayer from tipg's TileJSON (V-4)`.

---

### Task 4: Docs and the §8 deferrals

**Files:**
- Modify: `docs/FEATURES.md` — the `| Map page (\`/map\`) |` row: replace "tipg vector layers and the full docs are V-4." with the V-4 sentence below
- Modify: `docs/serving.md` — after the tipg row/paragraph (anchor: the line beginning `  - *Vector features/tiles* → the tipg **landing page**`): a short paragraph naming `/map` as the third consumer, the TileJSON path `/collections/{id}/tiles/WebMercatorQuad/tilejson.json`, and that the MVT source layer is `default` (verified against tipg 1.0.1 on 2026-09-09)
- Modify: `docs/ISSUES.md` — new entry I-122 after I-121 with the spec §8 deferrals

- [ ] **Step 1:** FEATURES sentence: "V-4 (2026-09-14) added the Vector tiles section: `useTipgCollections` (`serving/queries.ts`, quiet, fetched only while the picker is open) lists tipg's `/collections`, each adding a `vector` layer that `VectorTileLayer` draws from `tipgTileJsonUrl(id)` (source layer `default`, verified against tipg 1.0.1); vector layers keep the camera and ignore the time axis. An unreachable tipg reads "no vector tiles published". `docs/serving.md` records the TileJSON path; spec §8's deferrals are I-122."

- [ ] **Step 2:** serving.md paragraph:
```markdown
The `/map` page (V-4) is the third consumer of the tilers: its Add-layer picker
lists `GET {PUBLIC_TIPG_URL}/collections` (only while the picker is open, no
credentials) and draws a chosen collection from
`{PUBLIC_TIPG_URL}/collections/{id}/tiles/WebMercatorQuad/tilejson.json` — a
TileJSON 3.0 document whose `vector_layers[0].id` is `default`, the MVT
source-layer name every tipg tile carries (verified against tipg 1.0.1 on
2026-09-09). The local stack's tipg exposes only PostGIS function collections
(`public.st_hexagongrid`, …) until a demo table is seeded (I-122).
```

- [ ] **Step 3:** ISSUES entry:
```markdown
### I-122 · `/map` deferred scope (map-page spec §8) ⚪
Logged when V-4 closed the V queue; none is a defect.
- Shareable URL state (layers, span, tick, camera).
- The STAC `renders` extension (band / rescale / colormap) instead of the tiler's defaults; a legend.
- A basemap picker.
- Whole-span footprints toggle; click-through on imagery to the item at that tick.
- A seeded demo vector table (e.g. GOES hotspot points) — today's tipg lists only PostGIS function collections, so the Vector tiles section has nothing real to show.
- A pre-warmed mosaic cache for smooth first-pass playback (shared with I-108).
- Vector layers are not hoverable and contribute no ticks (spec §4.4/§4.6 as written).
- Tracked in: `app/src/components/map/`, `docs/superpowers/specs/2026-09-04-map-page-design.md` §8.
```

- [ ] **Step 4:** `npm run verify` (docs only, still the gate) → commit: `docs: V-4 — map page vector tiles, serving.md TileJSON path, I-122 deferrals`.

---

### Task 5: Verify, e2e, merge, live check (lead only)

- [ ] `npm run verify` on the branch; merge `--no-ff` into `ai/main`; verify again; whole e2e suite (`E2E_PORT=4399 npm run test:e2e:ci` if :4321 is held; baseline 46 passed, 1 skipped, plus whatever V-3/V-4 added).
- [ ] Chrome on the standing stack: `/map` → Add layer → Vector tiles lists the six `public.*` function collections; add `public.st_hexagongrid` — expect either hexagons over the view or nothing (a function collection needs parameters — record which); "no vector tiles published" appears with tipg stopped (`docker compose stop tipg`, then `start`).
- [ ] TODO tick + V row ("queue complete") + landed note; remove the worktree and branch.

## Self-review

**Spec coverage.** §4.5 Vector tiles section (query, per-collection add, Added/disabled, the empty/unreachable note) → Task 2; the `vector` layer drawn from `tipgTileJsonUrl` with the verified source layer → Task 3; §4.4 vector layers ignore the axis → Task 3's dispatcher (no frames reported) + test; §7 docs → Task 4; §8 deferrals logged → Task 4 (I-122); the V-1 carried-forward V-4 bullet (delete the two exports + the proxy, `vectorTileLayerIds`, default-opacity assertions) → Task 1. ✓

**Placeholder scan.** Two test snippets tell the implementer to reuse the file's existing recording helpers / fitBounds spy by name-lookup rather than restating them; each says exactly what to assert. No "TBD".

**Type consistency.** `TipgCollection { id, title?, description? }` is produced by `fetchTipgCollections`, consumed by `AddLayerPopover.onAddVector`, `LayerPanel.onAddVector`, `MapPage.addVectorLayer`. `vectorTileLayerIds(id).fill` = `${id}-fill` matches `layerAnchorId`'s prior literal and `map-state.test.ts`'s `a-fill`. `MapLayerViewProps` is unchanged. Test ids: `map-add-vector-${id}`, `map-vector-empty`, `map-layer-icon-vector` (from V-3's `map-layer-icon-${kind}`).
