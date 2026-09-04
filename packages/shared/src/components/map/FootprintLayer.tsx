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
