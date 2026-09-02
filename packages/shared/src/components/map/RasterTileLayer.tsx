import { Source, Layer } from "react-map-gl/maplibre";
import {
  RASTER_PREVIEW_LAYER,
  RASTER_PREVIEW_SOURCE,
} from "@shared/lib/map/styles";

export interface RasterTileLayerProps {
  /** XYZ tile URL templates, e.g. a TileJSON document's `tiles`. */
  tiles: string[];
  /** Optional [w, s, e, n] so maplibre asks only for covered tiles. */
  bounds?: [number, number, number, number];
  tileSize?: number;
  opacity?: number;
  minzoom?: number;
  maxzoom?: number;
  /** Draw beneath this layer id — pass a vector layer to keep it on top. */
  beforeId?: string;
}

/**
 * A raster overlay for `StacMap` — typically a titiler-pgstac TileJSON's
 * tiles for one item (G-5).
 *
 * Pure presentation: it renders whatever tiles it is handed. Deciding whether
 * a preview exists at all (serving enabled, a previewable asset, a tile server
 * that answered) belongs to the caller, so this component has no notion of
 * loading or failure.
 */
export function RasterTileLayer({
  tiles,
  bounds,
  tileSize = 256,
  opacity = 1,
  minzoom,
  maxzoom,
  beforeId,
}: RasterTileLayerProps) {
  return (
    <Source
      id={RASTER_PREVIEW_SOURCE}
      type="raster"
      tiles={tiles}
      tileSize={tileSize}
      bounds={bounds}
      minzoom={minzoom}
      maxzoom={maxzoom}
    >
      <Layer
        id={RASTER_PREVIEW_LAYER}
        type="raster"
        source={RASTER_PREVIEW_SOURCE}
        beforeId={beforeId}
        paint={{ "raster-opacity": opacity }}
      />
    </Source>
  );
}
