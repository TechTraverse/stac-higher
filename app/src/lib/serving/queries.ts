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
import { itemTileJsonUrl, tipgCollectionsUrl } from "./urls";

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
    .map((c) => ({
      id: c.id,
      title: typeof c.title === "string" ? c.title : undefined,
      description: typeof c.description === "string" ? c.description : undefined,
    }));
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
