import { Source, Layer } from "react-map-gl/maplibre";
import {
  RASTER_PREVIEW_LAYER,
  RASTER_PREVIEW_SOURCE,
} from "@shared/lib/map/styles";

export interface RasterTileLayerProps {
  /** XYZ tile URL templates, e.g. a TileJSON document's `tiles`. */
  tiles: string[];
  /**
   * Namespace for this overlay's maplibre source and layer. Omit for the
   * single-overlay case (the item preview); pass a per-instance value when a
   * map mounts several at once, since ids must be unique within a style.
   */
  id?: string;
  /** Optional [w, s, e, n] so maplibre asks only for covered tiles. */
  bounds?: [number, number, number, number];
  tileSize?: number;
  opacity?: number;
  /**
   * Opacity transition, in ms. Left unset, maplibre cross-fades over its own
   * default — right for a layer appearing once, wrong for an animation where
   * frames are swapped by opacity and a fade reads as a smear.
   */
  opacityTransitionMs?: number;
  minzoom?: number;
  maxzoom?: number;
  /** Draw beneath this layer id — pass a vector layer to keep it on top. */
  beforeId?: string;
}

/**
 * A raster overlay for `StacMap` — typically a titiler-pgstac TileJSON's
 * tiles for one item (G-5), or one frame of a collection's time series.
 *
 * Pure presentation: it renders whatever tiles it is handed. Deciding whether
 * a preview exists at all (serving enabled, a previewable asset, a tile server
 * that answered) belongs to the caller, so this component has no notion of
 * loading or failure.
 */
export function RasterTileLayer({
  tiles,
  id,
  bounds,
  tileSize = 256,
  opacity = 1,
  opacityTransitionMs,
  minzoom,
  maxzoom,
  beforeId,
}: RasterTileLayerProps) {
  const sourceId = id ?? RASTER_PREVIEW_SOURCE;
  const layerId = id ? `${id}-layer` : RASTER_PREVIEW_LAYER;

  return (
    <Source
      id={sourceId}
      type="raster"
      tiles={tiles}
      tileSize={tileSize}
      bounds={bounds}
      minzoom={minzoom}
      maxzoom={maxzoom}
    >
      <Layer
        id={layerId}
        type="raster"
        source={sourceId}
        beforeId={beforeId}
        paint={{
          "raster-opacity": opacity,
          ...(opacityTransitionMs !== undefined && {
            "raster-opacity-transition": { duration: opacityTransitionMs, delay: 0 },
          }),
        }}
      />
    </Source>
  );
}
