# G-5 · Raster Preview Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a built-in-catalog collection has OGC serving enabled and the tile server can render an item, the product item page's map shows that item's raster tiles under its footprint.

**Architecture:** A shared `RasterTileLayer` map component (react-map-gl `Source type="raster"` + `Layer type="raster"`) exported from `@stac-higher/shared`. In the app, a small serving module centralises the tile-server base URL and builds the item TileJSON URL; a TanStack Query hook fetches the TileJSON (retry off, failures silent); the product item page enables that query only when the collection's `servingEnabled` setting is true and the item has a previewable asset, and passes the TileJSON's `tiles` and `bounds` into `ItemDetailView`, which renders the layer inside its existing geometry-tab map. The read-only catalog browser (external catalogs) is untouched.

**Tech Stack:** React 19, react-map-gl/maplibre, TanStack Query, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` §7.2. Depends on G-4 (merged 2026-09-01; the tile server maps canonical hrefs, verified live 2026-09-02 with a 3-band uint8 COG).

## Global Constraints

- Worktree off `ai/main`: `git worktree add .claude/worktrees/goes-g5 -b ai/goes-g5 ai/main`; `npm install` at the worktree root.
- Gate: `npm run verify` (root). No e2e, no dev server, no Docker for the implementer.
- Read `.agents/skills/project-conventions/SKILL.md` and `.agents/skills/new-component/SKILL.md` first. Shared components import via `@shared/*` inside the package and are exported from `packages/shared/src/index.ts`; app code imports from `@stac-higher/shared`.
- No new dependencies. Never edit `app/src/components/ui/*`.
- Query keys come from `app/src/lib/query/keys.ts`.
- The map in the product item page must look exactly as it does today when the layer is absent (no serving, no asset, fetch failed, external catalog).
- Commit messages end with the session's attribution trailer.

---

### Task 1: Serving URL helpers + preview asset picker

**Files:**
- Create: `app/src/lib/serving/urls.ts`
- Create: `app/src/lib/serving/preview.ts`
- Test: `app/src/__tests__/serving-urls.test.ts`
- Modify: `app/src/components/collections/SettingsTab.tsx:55-57`, `app/src/components/collections/ProductOverview.tsx:54-55` (use the helper instead of their private `TITILER_URL` constants)

**Interfaces:**
- `titilerBaseUrl(env: Record<string, string | undefined> = import.meta.env): string` → `env.PUBLIC_TITILER_URL ?? "http://localhost:8084"`, trailing slash stripped.
- `itemTileJsonUrl(collectionId: string, itemId: string, assetKey: string, base = titilerBaseUrl()): string` → `${base}/collections/${enc(c)}/items/${enc(i)}/WebMercatorQuad/tilejson.json?assets=${enc(asset)}`.
- `collectionInfoUrl(collectionId: string, base = titilerBaseUrl()): string` → `${base}/collections/${enc(c)}/info` (what SettingsTab/ProductOverview link today).
- `pickPreviewAsset(item: StacItem): string | null` — first asset whose `roles` includes `"visual"`; else first asset whose `type` starts with `image/tiff` or contains `cloud-optimized`; else `null`.

- [ ] **Step 1: Failing tests** (`app/src/__tests__/serving-urls.test.ts`)

```ts
import { describe, it, expect } from "vitest";
import { titilerBaseUrl, itemTileJsonUrl, collectionInfoUrl } from "@/lib/serving/urls";
import { pickPreviewAsset } from "@/lib/serving/preview";
import type { StacItem } from "@/lib/stac-api/types";

describe("serving urls", () => {
  it("defaults the base and strips a trailing slash", () => {
    expect(titilerBaseUrl({})).toBe("http://localhost:8084");
    expect(titilerBaseUrl({ PUBLIC_TITILER_URL: "https://tiles.example/" })).toBe("https://tiles.example");
  });
  it("builds the item tilejson url with encoded segments", () => {
    expect(itemTileJsonUrl("my coll", "it/em", "visual", "http://t")).toBe(
      "http://t/collections/my%20coll/items/it%2Fem/WebMercatorQuad/tilejson.json?assets=visual",
    );
  });
  it("builds the collection info url", () => {
    expect(collectionInfoUrl("c", "http://t")).toBe("http://t/collections/c/info");
  });
});

function item(assets: StacItem["assets"]): StacItem {
  return { type: "Feature", stac_version: "1.0.0", id: "i", geometry: null, bbox: undefined, properties: { datetime: null }, links: [], assets } as unknown as StacItem;
}

describe("pickPreviewAsset", () => {
  it("prefers the visual role", () => {
    expect(pickPreviewAsset(item({ data: { href: "a.tif", type: "image/tiff" }, visual: { href: "v.tif", roles: ["visual"] } }))).toBe("visual");
  });
  it("falls back to a GeoTIFF/COG typed asset", () => {
    expect(pickPreviewAsset(item({ thumb: { href: "t.png", type: "image/png" }, data: { href: "a.tif", type: "image/tiff; application=geotiff; profile=cloud-optimized" } }))).toBe("data");
  });
  it("returns null when nothing is previewable", () => {
    expect(pickPreviewAsset(item({ meta: { href: "m.json", type: "application/json" } }))).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd app && npx vitest run src/__tests__/serving-urls.test.ts` → FAIL (modules missing).

- [ ] **Step 3: Implement**

`app/src/lib/serving/urls.ts`:

```ts
/**
 * Tile-server (titiler-pgstac) URL builders — the one place the browser-facing
 * base lives (docs/serving.md). Link-level only: nothing here gates access.
 */
const DEFAULT_TITILER_URL = "http://localhost:8084";

export function titilerBaseUrl(
  env: Record<string, string | undefined> = import.meta.env as Record<string, string | undefined>,
): string {
  return (env.PUBLIC_TITILER_URL ?? DEFAULT_TITILER_URL).replace(/\/+$/, "");
}

const enc = encodeURIComponent;

export function collectionInfoUrl(collectionId: string, base = titilerBaseUrl()): string {
  return `${base}/collections/${enc(collectionId)}/info`;
}

/** TileJSON for one item + asset (WebMercatorQuad). */
export function itemTileJsonUrl(
  collectionId: string,
  itemId: string,
  assetKey: string,
  base = titilerBaseUrl(),
): string {
  return `${base}/collections/${enc(collectionId)}/items/${enc(itemId)}/WebMercatorQuad/tilejson.json?assets=${enc(assetKey)}`;
}
```

`app/src/lib/serving/preview.ts`:

```ts
import type { StacItem } from "@/lib/stac-api/types";

/** The asset key the item page previews, or null when nothing is a raster. */
export function pickPreviewAsset(item: StacItem): string | null {
  const entries = Object.entries(item.assets ?? {});
  const visual = entries.find(([, a]) => (a.roles ?? []).includes("visual"));
  if (visual) return visual[0];
  const raster = entries.find(([, a]) => {
    const type = (a.type ?? "").toLowerCase();
    return type.startsWith("image/tiff") || type.includes("cloud-optimized");
  });
  return raster ? raster[0] : null;
}
```

Replace the two components' private constants with `collectionInfoUrl(collectionId)` (and keep their tipg constant as is).

- [ ] **Step 4: Run tests + `npm run check`, commit**

```bash
git add app/src/lib/serving app/src/__tests__/serving-urls.test.ts app/src/components/collections/SettingsTab.tsx app/src/components/collections/ProductOverview.tsx
git commit -m "feat(serving): tile-server url helpers and preview asset picker (G-5)"
```

---

### Task 2: TileJSON query hook

**Files:**
- Modify: `app/src/lib/query/keys.ts` (add `servingKeys`)
- Create: `app/src/lib/serving/queries.ts`
- Test: `app/src/__tests__/serving-queries.test.tsx`

**Interfaces:**
- `servingKeys = { all: () => ["serving"], itemTileJson: (c, i, asset) => ["serving", "item-tilejson", c, i, asset] }`.
- `interface TileJson { tiles: string[]; bounds?: [number, number, number, number]; minzoom?: number; maxzoom?: number }`.
- `useItemTileJson(collectionId: string, itemId: string, assetKey: string | null, enabled: boolean)` → `useQuery({ queryKey, queryFn, enabled: enabled && !!assetKey, retry: false, staleTime: 5 * 60_000 })`; `queryFn` fetches with `credentials: "omit"` (the tile server is a separate origin, no cookies), throws on non-OK, validates `Array.isArray(json.tiles) && json.tiles.length > 0`.

- [ ] **Step 1: Failing test** — render a tiny component using the hook inside a `QueryClientProvider` (copy the provider setup from `app/src/__tests__/extension-fields.test.tsx`), with `globalThis.fetch` stubbed via `vi.fn()`:

```tsx
it("fetches tilejson when enabled and exposes tiles", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ tiles: ["http://t/{z}/{x}/{y}"], bounds: [-100, 30, -90, 40] }), { status: 200 }));
  render(<Probe collectionId="c" itemId="i" assetKey="visual" enabled />, { wrapper });
  await screen.findByText("tiles:1");
  expect(fetchMock.mock.calls[0][0]).toContain("/collections/c/items/i/WebMercatorQuad/tilejson.json?assets=visual");
});

it("does not fetch when disabled or without an asset", () => {
  render(<Probe collectionId="c" itemId="i" assetKey={null} enabled />, { wrapper });
  render(<Probe collectionId="c" itemId="i" assetKey="visual" enabled={false} />, { wrapper });
  expect(fetchMock).not.toHaveBeenCalled();
});

it("surfaces a non-OK response as an error, without retrying", async () => {
  fetchMock.mockResolvedValueOnce(new Response("nope", { status: 404 }));
  render(<Probe collectionId="c" itemId="i" assetKey="visual" enabled />, { wrapper });
  await screen.findByText("error");
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
```

where `Probe` renders `tiles:{data?.tiles.length}` / `error` from the hook.

- [ ] **Step 2: Run to verify failure**, then **Step 3: implement** the key + hook per the Interfaces block, **Step 4: run + check, commit**:

```bash
git add app/src/lib/query/keys.ts app/src/lib/serving/queries.ts app/src/__tests__/serving-queries.test.tsx
git commit -m "feat(serving): useItemTileJson query hook (G-5)"
```

---

### Task 3: Shared `RasterTileLayer`

**Files:**
- Create: `packages/shared/src/components/map/RasterTileLayer.tsx`
- Create: `packages/shared/src/components/map/RasterTileLayer.stories.tsx` (Storybook renders StacMap with a public demo tile URL; optional but matches the directory)
- Modify: `packages/shared/src/index.ts` (export next to `FootprintLayer`)
- Modify: `packages/shared/src/lib/map/styles.ts` (add `RASTER_PREVIEW_SOURCE = "stac-raster-preview"` and `RASTER_PREVIEW_LAYER = "stac-raster-preview-layer"`)
- Test: `app/src/__tests__/raster-tile-layer.test.tsx` (the shared package has no vitest of its own; app tests import it)

**Interfaces:**

```ts
export interface RasterTileLayerProps {
  tiles: string[];
  bounds?: [number, number, number, number];
  tileSize?: number;      // default 256
  opacity?: number;       // default 1
  minzoom?: number;
  maxzoom?: number;
  beforeId?: string;      // insert below this layer id (e.g. the footprint fill)
}
export function RasterTileLayer(props: RasterTileLayerProps): JSX.Element
```

- [ ] **Step 1: Failing test** — mock `react-map-gl/maplibre` so `Source`/`Layer` record their props:

```tsx
vi.mock("react-map-gl/maplibre", () => ({
  Source: ({ children, ...props }: any) => <div data-testid="source" data-props={JSON.stringify(props)}>{children}</div>,
  Layer: (props: any) => <div data-testid="layer" data-props={JSON.stringify(props)} />,
}));
import { RasterTileLayer } from "@stac-higher/shared";

it("renders a raster source and layer with the given tiles and bounds", () => {
  render(<RasterTileLayer tiles={["http://t/{z}/{x}/{y}.png"]} bounds={[-100, 30, -90, 40]} opacity={0.8} beforeId="stac-footprint-fill" />);
  const source = JSON.parse(screen.getByTestId("source").dataset.props!);
  expect(source).toMatchObject({ id: "stac-raster-preview", type: "raster", tiles: ["http://t/{z}/{x}/{y}.png"], tileSize: 256, bounds: [-100, 30, -90, 40] });
  const layer = JSON.parse(screen.getByTestId("layer").dataset.props!);
  expect(layer).toMatchObject({ id: "stac-raster-preview-layer", type: "raster", source: "stac-raster-preview", beforeId: "stac-footprint-fill", paint: { "raster-opacity": 0.8 } });
});
```

If the module mock cannot intercept the shared package's import of `react-map-gl/maplibre` (workspace symlink resolution), import the component from `@shared/components/map/RasterTileLayer` via the app's vitest alias if one exists, or add `resolve.alias` for `@shared` in `app/vitest.config.ts` scoped to tests — say which in the commit.

- [ ] **Step 2: Implement**

```tsx
import { Source, Layer } from "react-map-gl/maplibre";
import { RASTER_PREVIEW_LAYER, RASTER_PREVIEW_SOURCE } from "@shared/lib/map/styles";

export interface RasterTileLayerProps { /* as above */ }

/**
 * An XYZ raster overlay (e.g. a titiler-pgstac TileJSON's `tiles`) drawn
 * under the vector footprints. Pure presentation: the caller decides whether
 * there is anything to show.
 */
export function RasterTileLayer({ tiles, bounds, tileSize = 256, opacity = 1, minzoom, maxzoom, beforeId }: RasterTileLayerProps) {
  return (
    <Source id={RASTER_PREVIEW_SOURCE} type="raster" tiles={tiles} tileSize={tileSize} bounds={bounds} minzoom={minzoom} maxzoom={maxzoom}>
      <Layer id={RASTER_PREVIEW_LAYER} type="raster" source={RASTER_PREVIEW_SOURCE} beforeId={beforeId} paint={{ "raster-opacity": opacity }} />
    </Source>
  );
}
```

Export from `packages/shared/src/index.ts` and add the two ids to `styles.ts` (and export them from the barrel next to the other layer constants).

- [ ] **Step 3: Run the test + `npm run check`, commit**

```bash
git add packages/shared/src/components/map/RasterTileLayer.tsx packages/shared/src/components/map/RasterTileLayer.stories.tsx packages/shared/src/lib/map/styles.ts packages/shared/src/index.ts app/src/__tests__/raster-tile-layer.test.tsx
git commit -m "feat(shared/map): RasterTileLayer (G-5)"
```

---

### Task 4: Wire it into the product item page

**Files:**
- Modify: `app/src/components/items/ItemDetailView.tsx` (props; geometry tab map ~lines 140–178)
- Modify: `app/src/components/items/ItemDetail.tsx` (`ItemDetailInner`, ~lines 27–40 and the `<ItemDetailView item={item} />` at ~line 140)
- Test: `app/src/__tests__/item-detail-preview.test.tsx`

**Interfaces:**
- `ItemDetailView({ item, rasterPreview }: { item: StacItem; rasterPreview?: { tiles: string[]; bounds?: [number, number, number, number]; viewerUrl?: string } })`. When `rasterPreview` is present: render `<RasterTileLayer tiles bounds beforeId="item-geometry-fill" />` as the FIRST child of the geometry tab's `StacMap`, and a caption line under the map: "Preview rendered by the tile server" with an "Open viewer" link to `viewerUrl` when given.
- `ItemDetail.tsx`: 

```ts
const { data: settings } = useCollectionSettings(collectionId);
const previewAsset = item ? pickPreviewAsset(item) : null;
const tileJson = useItemTileJson(collectionId, itemId, previewAsset, settings?.servingEnabled === true);
const rasterPreview = tileJson.data ? { tiles: tileJson.data.tiles, bounds: tileJson.data.bounds, viewerUrl: `${titilerBaseUrl()}/collections/${enc(collectionId)}/items/${enc(itemId)}/WebMercatorQuad/map?assets=${enc(previewAsset!)}` } : undefined;
<ItemDetailView item={item} rasterPreview={rasterPreview} />
```

  `useCollectionSettings` is already exported from `@/lib/collections/settings-client` and returns the camelCase `CollectionSettings` (`servingEnabled`). Hooks must be called unconditionally — place them above the loading/error early returns in `ItemDetailInner`, keying `enabled` on `!!item`.

- [ ] **Step 1: Failing test** — mock `@/lib/collections/settings-client` (as `settings-tab.test.tsx` does), `@/lib/serving/queries` (`useItemTileJson` returning `{ data: { tiles: [...], bounds } }` or `{ data: undefined }`), `@/lib/query/items` (whatever `useItem` module `ItemDetail.tsx` imports — return a fixed item with a `visual` asset), and `@stac-higher/shared`'s map pieces so the DOM shows a marker for the raster layer. Assert: with `servingEnabled: true` + tilejson data → `screen.getByText(/Preview rendered by the tile server/)` present and `useItemTileJson` called with `enabled === true`; with `servingEnabled: false` → not present and `enabled === false`; with an item lacking a raster asset → called with `assetKey === null`.

- [ ] **Step 2: Implement** per the Interfaces block. In `ItemDetailView`, import `RasterTileLayer` from `@stac-higher/shared` and `ExternalLink` from `lucide-react` (already imported).

- [ ] **Step 3: Run `npm test` + `npm run check`, commit**

```bash
git add app/src/components/items/ItemDetailView.tsx app/src/components/items/ItemDetail.tsx app/src/__tests__/item-detail-preview.test.tsx
git commit -m "feat(items): raster preview layer on the product item page (G-5)"
```

---

### Task 5: Docs, verify

**Files:**
- Modify: `docs/serving.md` ("What the collection page links to" → add an "Item preview" bullet; Env table unchanged)
- Modify: `docs/FEATURES.md` (G queue section: G-5 row)
- Modify: `docs/ISSUES.md` I-69 (one sentence: the preview layer obeys the same link-level toggle and is not a gate either)

- [ ] **Step 1:** `docs/serving.md` bullet: "**Item preview (G-5).** When serving is on and the item has a `visual` (or GeoTIFF-typed) asset, the product item page's geometry map overlays the item's tiles from the tile server's item TileJSON, with an *Open viewer* link. The read-only catalog browser never does this. Failures are silent — no tile server, no layer."

- [ ] **Step 2:** `npm run verify` from the root → green.

```bash
git add docs/serving.md docs/FEATURES.md docs/ISSUES.md
git commit -m "docs(serving): item raster preview (G-5)"
```

Lead (Docker + dev server): open `/collections/g4-tiletest/items/t1` on the local stack with serving enabled on that collection and confirm the gradient tile appears under the footprint.
