/**
 * The /map layer list (spec §4.5), TOPMOST layer first — the order a reader
 * sees on the map, which is the reverse of the draw order the state keeps.
 */
import { Layers } from "lucide-react";
import { EmptyState } from "@stac-higher/shared";
import type { StacCollection } from "@/lib/stac-api/types";
import type { FrameSpan, MapLayer } from "@/lib/map/state";

export interface LayerPanelProps {
  /** Draw order, bottom first. */
  layers: MapLayer[];
  collections: StacCollection[];
  catalogUrl: string;
  frameSpan: FrameSpan;
}

export function LayerPanel({ layers }: LayerPanelProps) {
  const topFirst = [...layers].reverse();

  return (
    <aside
      data-testid="map-layer-panel"
      className="flex w-80 shrink-0 flex-col overflow-y-auto border-r border-border bg-card"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h1 className="text-sm font-bold tracking-tight">Layers</h1>
      </div>

      <div className="flex-1 p-3">
        {topFirst.length === 0 ? (
          <EmptyState
            icon={Layers}
            title="No layers yet"
            description="Add a product from the built-in catalog to draw its item footprints on the map."
          />
        ) : null}
      </div>
    </aside>
  );
}
