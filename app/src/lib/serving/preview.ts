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
