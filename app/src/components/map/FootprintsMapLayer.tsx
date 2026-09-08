/**
 * One footprints layer on the /map map (spec §4.3).
 *
 * The items query lives HERE rather than in the page because the layer list
 * is dynamic and a hook cannot be called in a loop. Two layers over the same
 * collection (footprints + imagery, V-3) share one request through TanStack's
 * key.
 *
 * V-2 draws every item in the span. V-3 narrows this to the items of the
 * current axis tick, so footprints and imagery stay in step.
 */
import { useMemo } from "react";
import { FootprintLayer } from "@stac-higher/shared";
import { useItems } from "@/lib/query/items";
import type { MapLayer } from "@/lib/map/state";

interface FootprintsMapLayerProps {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
  /** The anchor of the layer above this one; undefined when topmost. */
  beforeId?: string;
}

export function FootprintsMapLayer({
  layer,
  catalogUrl,
  frameSpan,
  beforeId,
}: FootprintsMapLayerProps) {
  const { data } = useItems(catalogUrl, layer.sourceId, {
    limit: frameSpan,
    sortby: "-datetime",
  });
  const items = useMemo(() => data?.features ?? [], [data]);

  return (
    <FootprintLayer
      id={layer.id}
      items={items}
      opacity={layer.opacity}
      visible={layer.visible}
      beforeId={beforeId}
    />
  );
}
