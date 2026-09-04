import type { StacItem } from "@/lib/stac-api/types";

/**
 * The asset key the item page previews, or null when the item carries nothing
 * a raster tiler can open.
 *
 * `visual` (the STAC role for a display-ready RGB rendering) wins outright:
 * it is what a GeoColor-style COG is published as, and it needs no render
 * parameters. Otherwise the first GeoTIFF/COG-typed asset is a reasonable
 * guess — the tiler applies its own defaults, which may look odd for a
 * multi-band science product, but a wrong-looking preview is recoverable and
 * silently showing nothing is not.
 */
export function pickPreviewAsset(item: StacItem): string | null {
  const entries = Object.entries(item.assets ?? {});

  const visual = entries.find(([, asset]) => (asset.roles ?? []).includes("visual"));
  if (visual) return visual[0];

  const raster = entries.find(([, asset]) => {
    const type = (asset.type ?? "").toLowerCase();
    return type.startsWith("image/tiff") || type.includes("cloud-optimized");
  });
  return raster ? raster[0] : null;
}

/**
 * Every asset key across `items` that a raster tiler can open, best first
 * (`visual`-role keys ahead of plain GeoTIFF/COGs), deduped.
 *
 * The collection preview needs the whole set, not one item's pick: it renders
 * a mosaic over many items and lets the operator choose between keys when a
 * product publishes more than one rendering. The head of this list is the
 * collection's default preview asset.
 */
export function previewAssetCandidates(items: StacItem[]): string[] {
  const visual = new Set<string>();
  const raster = new Set<string>();

  for (const item of items) {
    for (const [key, asset] of Object.entries(item.assets ?? {})) {
      if ((asset.roles ?? []).includes("visual")) {
        visual.add(key);
        continue;
      }
      const type = (asset.type ?? "").toLowerCase();
      if (type.startsWith("image/tiff") || type.includes("cloud-optimized")) {
        raster.add(key);
      }
    }
  }

  // A key that is visual on any item is visual for the collection.
  for (const key of visual) raster.delete(key);
  return [...visual, ...raster];
}
