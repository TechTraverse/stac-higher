/**
 * Tile-server (titiler-pgstac) URL builders — the ONE place the browser-facing
 * base lives, so the Settings tab, the product overview and the item preview
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
