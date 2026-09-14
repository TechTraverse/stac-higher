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
  /**
   * The hint query has SETTLED (succeeded or failed). Frames wait on this.
   * False forever when the tiler was never asked (footprints, serving off,
   * no tileable asset) — check `servingEnabled`/`asset` first; never gate
   * footprints on it.
   */
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
