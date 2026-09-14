/**
 * One layer of the /map page, drawn (spec §4.3, §4.4).
 *
 * `MapLayerView` is a pure dispatcher on `layer.kind` — it calls no hooks
 * itself, so each per-kind component below keeps its own stable hook order
 * regardless of which branch renders. STAC layers (footprints, imagery) draw
 * through `StacLayerView`, which runs its own queries per layer (a component
 * per layer, since React forbids hooks in a loop): it reports its frames UP
 * to the page, which owns the shared axis, and takes the chosen tick back
 * DOWN — resolving the tick to its OWN frame index, since products have
 * their own cadences and the page's axis is their union.
 *
 * Footprints show the items of their current frame rather than the whole span:
 * a frame's items are exactly what the imagery of the same timestep renders,
 * so the two kinds line up (spec §5, §9).
 *
 * Vector layers (tipg) are NOT time-aware: they contribute no frames, ignore
 * the axis tick, and never call `useLayerData` — `VectorLayerView` just draws
 * the tipg collection's TileJSON through the shared `VectorTileLayer`.
 */
import { useEffect, useMemo } from "react";
import {
  FootprintLayer,
  RasterFrameStack,
  VectorTileLayer,
  type RasterFrame,
} from "@stac-higher/shared";
import type { MapLayer } from "@/lib/map/state";
import type { StacItem } from "@/lib/stac-api/types";
import type { PreviewFrame } from "@/lib/serving/frames";
import { resolveLayerFrame } from "@/lib/map/axis";
import { collectionTileUrlTemplate, tipgTileJsonUrl } from "@/lib/serving/urls";
import { useLayerData } from "./useLayerData";

export interface MapLayerViewProps {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
  /** The instant the page's axis is parked on; null when there is no axis. */
  tickInstant: number | null;
  /** Draw beneath this layer id — the bottom layer of the layer above. */
  beforeId?: string;
  /** Reports this layer's frames up so the page can build the axis. */
  onFramesChange: (layerId: string, frames: PreviewFrame[]) => void;
  /** Called on unmount so a removed layer leaves the axis. */
  onFramesRemove: (layerId: string) => void;
}

/** Dispatch on kind: vector layers have no items, no frames and no axis. */
export function MapLayerView(props: MapLayerViewProps) {
  if (props.layer.kind === "vector") {
    return <VectorLayerView layer={props.layer} beforeId={props.beforeId} />;
  }
  return <StacLayerView {...props} />;
}

function VectorLayerView({ layer, beforeId }: { layer: MapLayer; beforeId?: string }) {
  return (
    <VectorTileLayer
      id={layer.id}
      url={tipgTileJsonUrl(layer.sourceId)}
      sourceLayer="default"
      opacity={layer.opacity}
      visible={layer.visible}
      beforeId={beforeId}
    />
  );
}

function StacLayerView({
  layer,
  catalogUrl,
  frameSpan,
  tickInstant,
  beforeId,
  onFramesChange,
  onFramesRemove,
}: MapLayerViewProps) {
  const { items, frames, asset, hint, hintSettled } = useLayerData(
    layer,
    catalogUrl,
    frameSpan,
  );

  // The page's axis is the union of these; `frames` keeps its identity across
  // renders (useLayerData memoises it), so this settles after one pass.
  useEffect(() => {
    onFramesChange(layer.id, frames);
  }, [layer.id, frames, onFramesChange]);

  useEffect(() => () => onFramesRemove(layer.id), [layer.id, onFramesRemove]);

  // Hold-last against this layer's own frames. Before the page has an axis
  // (nothing has loaded yet) the newest frame is what the preview opens on.
  const resolved =
    tickInstant === null
      ? frames.length > 0
        ? frames.length - 1
        : null
      : resolveLayerFrame(tickInstant, frames);

  const rasterFrames: RasterFrame[] = useMemo(
    () =>
      asset
        ? frames.map((f) => ({
            key: f.datetime,
            tiles: [collectionTileUrlTemplate(layer.sourceId, asset, f.datetime)],
          }))
        : [],
    [frames, asset, layer.sourceId],
  );

  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const frameItems = useMemo(() => {
    if (resolved === null) return [];
    return (frames[resolved]?.itemIds ?? [])
      .map((id) => byId.get(id))
      .filter((i): i is StacItem => i !== undefined);
  }, [resolved, frames, byId]);

  if (layer.kind === "footprints") {
    return (
      <FootprintLayer
        id={layer.id}
        items={frameItems}
        opacity={layer.opacity}
        visible={layer.visible}
        beforeId={beforeId}
      />
    );
  }

  if (layer.kind === "imagery") {
    // Frames wait for the zoom hint to SETTLE (a maplibre source's zoom range
    // is fixed at creation) and for a tick this layer actually reaches. The
    // stack stays mounted either way: its anchor is what the layer below
    // chains its `beforeId` to, and a target that comes and goes drops that
    // layer off the map.
    const showFrames = hintSettled && resolved !== null && rasterFrames.length > 0;
    return (
      <RasterFrameStack
        id={layer.id}
        frames={showFrames ? rasterFrames : []}
        index={
          showFrames
            ? Math.min(Math.max(resolved, 0), rasterFrames.length - 1)
            : 0
        }
        bounds={hint?.bounds}
        minzoom={hint?.minzoom}
        maxzoom={hint?.maxzoom}
        opacity={layer.opacity}
        visible={layer.visible}
        beforeId={beforeId}
      />
    );
  }
}
