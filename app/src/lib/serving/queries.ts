/**
 * TanStack Query access to the tile server (G-5).
 *
 * Deliberately quiet: the tile server is optional infrastructure on a separate
 * origin, so a miss (no service, serving off, an asset it cannot open) must
 * degrade to "no preview", never to an error banner on the item page. Hence
 * `retry: false` and callers that ignore `error`.
 */
import { useQuery } from "@tanstack/react-query";
import { servingKeys } from "@/lib/query/keys";
import { itemTileJsonUrl } from "./urls";

/** The subset of TileJSON the preview layer consumes. */
export interface TileJson {
  tiles: string[];
  bounds?: [number, number, number, number];
  minzoom?: number;
  maxzoom?: number;
}

/** 5 minutes: a published item's rendering does not change under us. */
const STALE_MS = 5 * 60_000;

export async function fetchItemTileJson(
  collectionId: string,
  itemId: string,
  assetKey: string,
): Promise<TileJson> {
  // No credentials: the tile server is a different origin and needs none.
  const res = await fetch(itemTileJsonUrl(collectionId, itemId, assetKey), {
    credentials: "omit",
  });
  if (!res.ok) {
    throw new Error(`tilejson request failed: ${res.status}`);
  }
  const doc = (await res.json()) as Partial<TileJson>;
  if (!Array.isArray(doc.tiles) || doc.tiles.length === 0) {
    throw new Error("tilejson document carries no tiles");
  }
  return doc as TileJson;
}

/**
 * TileJSON for one item's preview asset. Runs only when the collection has
 * serving enabled (`enabled`) and the item has something previewable
 * (`assetKey`).
 */
export function useItemTileJson(
  collectionId: string,
  itemId: string,
  assetKey: string | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: servingKeys.itemTileJson(collectionId, itemId, assetKey ?? ""),
    queryFn: () => fetchItemTileJson(collectionId, itemId, assetKey as string),
    enabled: enabled && Boolean(assetKey),
    retry: false,
    staleTime: STALE_MS,
  });
}
