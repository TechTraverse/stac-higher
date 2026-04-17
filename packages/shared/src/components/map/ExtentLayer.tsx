import { Source, Layer } from "react-map-gl/maplibre";
import {
  EXTENT_SOURCE,
  extentFillLayer,
  extentLineLayer,
} from "@shared/lib/map/styles";
import { bboxToPolygon } from "@shared/lib/map/bbox";

interface ExtentLayerProps {
  bbox: number[];
}

export function ExtentLayer({ bbox }: ExtentLayerProps) {
  const geojson: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: [bboxToPolygon(bbox)],
  };

  return (
    <Source id={EXTENT_SOURCE} type="geojson" data={geojson}>
      <Layer {...extentFillLayer} />
      <Layer {...extentLineLayer} />
    </Source>
  );
}
