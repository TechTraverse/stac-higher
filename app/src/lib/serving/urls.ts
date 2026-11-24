/**
 * Tile-server (titiler-pgstac) and tipg URL builders — the ONE place the browser-facing bases live,
 * so the Settings tab, the product overview and the item preview
 * cannot drift apart (docs/serving.md).
 *
 * Link-level only: nothing here gates access. Until per-collection read
 * visibility (I-1) lands, anything the tile server can see is effectively
 * public, and the `serving_enabled` toggle only controls whether the product
 * ADVERTISES these URLs (I-69).
 *
 * Since G-4 the compose tile server is a derived image that maps canonical
 * `/api/assets/...` hrefs to the platform bucket, so items ingested by the
 * platform render without any change to what the catalog stores.
 */
const DEFAULT_TITILER_URL = "http://localhost:8084";

/** The browser-facing titiler base, without a trailing slash. */
export function titilerBaseUrl(
  env: Record<string, string | undefined> = import.meta.env as Record<
    string,
    string | undefined
  >,
): string {
  return (env.PUBLIC_TITILER_URL ?? DEFAULT_TITILER_URL).replace(/\/+$/, "");
}

const enc = encodeURIComponent;

/** Per-collection raster metadata — the serving panel's entry-point link. */
export function collectionInfoUrl(
  collectionId: string,
  base: string = titilerBaseUrl(),
): string {
  return `${base}/collections/${enc(collectionId)}/info`;
}

/** TileJSON for one item + asset (WebMercatorQuad), the item preview's source. */
export function itemTileJsonUrl(
  collectionId: string,
  itemId: string,
  assetKey: string,
  base: string = titilerBaseUrl(),
): string {
  return (
    `${base}/collections/${enc(collectionId)}/items/${enc(itemId)}` +
    `/WebMercatorQuad/tilejson.json?assets=${enc(assetKey)}`
  );
}

/** titiler's own standalone viewer for one item + asset. */
export function itemViewerUrl(
  collectionId: string,
  itemId: string,
  assetKey: string,
  base: string = titilerBaseUrl(),
): string {
  return (
    `${base}/collections/${enc(collectionId)}/items/${enc(itemId)}` +
    `/WebMercatorQuad/map?assets=${enc(assetKey)}`
  );
}

/**
 * The tile template for ONE frame of the collection preview.
 *
 * titiler-pgstac's collection mosaic takes the same `datetime` filter pgstac
 * does, so pinning it turns the collection endpoint into a per-timestep layer
 * — no search registration, no POST, no CORS. `{z}/{x}/{y}` stay literal:
 * maplibre substitutes them, so they must survive URL building intact, which
 * is why the query is assembled by hand rather than through `URLSearchParams`
 * over a whole URL.
 */
export function collectionTileUrlTemplate(
  collectionId: string,
  assetKey: string,
  datetime: string,
  base: string = titilerBaseUrl(),
): string {
  return (
    `${base}/collections/${enc(collectionId)}/tiles/WebMercatorQuad/{z}/{x}/{y}@1x` +
    `?assets=${enc(assetKey)}&datetime=${enc(datetime)}`
  );
}

/** titiler's own standalone viewer for a collection, pinned to a frame if given. */
export function collectionViewerUrl(
  collectionId: string,
  assetKey: string,
  datetime?: string,
  base: string = titilerBaseUrl(),
): string {
  const url =
    `${base}/collections/${enc(collectionId)}/WebMercatorQuad/map` +
    `?assets=${enc(assetKey)}`;
  return datetime ? `${url}&datetime=${enc(datetime)}` : url;
}

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

// ---------------------------------------------------------------------------
// cube-server (xpublish-tiles + xpublish-edr over virtual Icechunk cubes, Z-7)
// — same rules again: one browser-facing base, link-level only (docs/serving.md).

const DEFAULT_CUBE_SERVER_URL = "http://localhost:8086";

/** The browser-facing cube-server base, without a trailing slash. */
export function cubeServerBaseUrl(
  env: Record<string, string | undefined> = import.meta.env as Record<
    string,
    string | undefined
  >,
): string {
  return (env.PUBLIC_CUBE_SERVER_URL ?? DEFAULT_CUBE_SERVER_URL).replace(/\/+$/, "");
}

export interface CubeStyle {
  /** A matplotlib colormap name; xpublish-tiles resolves it through `mpl.colormaps`. */
  colormap: string;
  /** [min, max] mapped onto the colormap. */
  range: readonly [number, number];
}

/**
 * GOES ABI C13 brightness temperature in K: the spike's 190-310 K range on
 * `Greys` (low = white), so cold cloud tops are white, the usual IR rendering.
 * xpublish-tiles 0.9.2 validates the variant against its own list, which has
 * no matplotlib `_r` reversals (`gray_r` is a 422). A range is ALWAYS sent:
 * real CMI carries no `valid_min`/`valid_max`, so without one the renderer
 * auto-ranges per tile and shading differs across tiles and frames.
 */
export const CUBE_DEFAULT_STYLE: CubeStyle = { colormap: "Greys", range: [190, 310] };

/**
 * The tile template for ONE frame of a cube. Row before column (`{z}/{y}/{x}`),
 * which stay literal for maplibre to substitute. `t` is the asset's
 * `stac_higher:time_values` entry VERBATIM (nanoseconds, trailing `Z`): the
 * server matches it exactly, so it is never re-formatted.
 */
export function cubeTileUrlTemplate(
  collectionId: string,
  variable: string,
  t: string,
  style: CubeStyle = CUBE_DEFAULT_STYLE,
  base: string = cubeServerBaseUrl(),
): string {
  return (
    `${base}/datasets/${enc(collectionId)}/tiles/WebMercatorQuad/{z}/{y}/{x}` +
    `?variables=${enc(variable)}&t=${enc(t)}&style=${enc(`raster/${style.colormap}`)}` +
    `&colorscalerange=${enc(`${style.range[0]},${style.range[1]}`)}&width=256&height=256&f=png`
  );
}

/** A click coordinate at the precision the EDR URL carries (4 decimals, ~11 m). */
export function roundCoord(n: number): number {
  return Number(n.toFixed(4));
}

/**
 * The EDR position query (GET, CSV) for one point. lon/lat are rounded to 4
 * decimals (~11 m) so a re-click on the same spot reuses the query cache.
 */
export function cubeEdrPositionUrl(
  collectionId: string,
  lon: number,
  lat: number,
  variable: string,
  base: string = cubeServerBaseUrl(),
): string {
  const x = roundCoord(lon);
  const y = roundCoord(lat);
  return (
    `${base}/datasets/${enc(collectionId)}/edr/position` +
    `?coords=POINT(${x}%20${y})&parameter-name=${enc(variable)}&f=csv`
  );
}
