import { Source, Layer } from "react-map-gl/maplibre";
import { vectorTileLayers } from "@shared/lib/map/styles";

export interface VectorTileLayerProps {
  /** Namespace for this overlay's maplibre source; layers are `${id}-fill|line|circle`. */
  id: string;
  /**
   * A TileJSON document URL. maplibre fetches it and takes the tile
   * templates, bounds and zoom range from it — which is what tipg publishes
   * at `/collections/{id}/tiles/WebMercatorQuad/tilejson.json`.
   */
  url: string;
  /** The MVT layer name inside the tiles. tipg writes `default`. */
  sourceLayer?: string;
  /** 0..1, default 1. */
  opacity?: number;
  /** Draw beneath this layer id. */
  beforeId?: string;
}

/**
 * An OGC API Tiles vector overlay for `StacMap`: one vector source, three
 * layers so polygons, lines and points each draw once. Pure presentation —
 * whether the tile server exists or answers is the caller's concern, and a
 * TileJSON that fails to load leaves the map as it was.
 */
export function VectorTileLayer({
  id,
  url,
  sourceLayer = "default",
  opacity = 1,
  beforeId,
}: VectorTileLayerProps) {
  const { fill, line, circle } = vectorTileLayers(id, sourceLayer, opacity);
  return (
    <Source id={id} type="vector" url={url}>
      <Layer {...fill} beforeId={beforeId} />
      <Layer {...line} beforeId={beforeId} />
      <Layer {...circle} beforeId={beforeId} />
    </Source>
  );
}
