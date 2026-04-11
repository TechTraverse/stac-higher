import { Source, Layer } from "react-map-gl/maplibre";
import {
  FOOTPRINT_SOURCE,
  footprintFillLayer,
  footprintLineLayer,
} from "@/lib/map/styles";
import type { StacItem } from "@/lib/stac-api/types";

interface FootprintLayerProps {
  items: StacItem[];
  selectedId?: string;
}

export function FootprintLayer({ items, selectedId }: FootprintLayerProps) {
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
    <Source id={FOOTPRINT_SOURCE} type="geojson" data={geojson}>
      <Layer {...footprintFillLayer} />
      <Layer {...footprintLineLayer} />
    </Source>
  );
}
