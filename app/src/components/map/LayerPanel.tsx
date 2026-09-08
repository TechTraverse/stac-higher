/**
 * The /map layer list (spec §4.5), TOPMOST layer first — the order a reader
 * sees on the map, which is the reverse of the draw order the state keeps.
 */
import { Layers } from "lucide-react";
import { EmptyState } from "@stac-higher/shared";
import { AddLayerPopover } from "@/components/map/AddLayerPopover";
import { LayerRow } from "@/components/map/LayerRow";
import type { StacCollection } from "@/lib/stac-api/types";
import type { FrameSpan, LayerKind, MapLayer } from "@/lib/map/state";

export interface LayerPanelProps {
  /** Draw order, bottom first. */
  layers: MapLayer[];
  collections: StacCollection[];
  catalogUrl: string;
  frameSpan: FrameSpan;
  onAdd: (kind: LayerKind, collection: StacCollection) => void;
  onVisibleChange: (id: string, visible: boolean) => void;
  onOpacityChange: (id: string, opacity: number) => void;
  onMove: (id: string, direction: "up" | "down") => void;
  onRemove: (id: string) => void;
}

export function LayerPanel({
  layers,
  collections,
  catalogUrl,
  frameSpan,
  onAdd,
  onVisibleChange,
  onOpacityChange,
  onMove,
  onRemove,
}: LayerPanelProps) {
  const topFirst = [...layers].reverse();
  const isAdded = (kind: LayerKind, sourceId: string) =>
    layers.some((l) => l.kind === kind && l.sourceId === sourceId);

  return (
    <aside
      data-testid="map-layer-panel"
      className="flex w-80 shrink-0 flex-col overflow-y-auto border-r border-border bg-card"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h1 className="text-sm font-bold tracking-tight">Layers</h1>
        <AddLayerPopover
          collections={collections}
          isAdded={isAdded}
          onAdd={onAdd}
        />
      </div>

      <div className="flex-1 space-y-2 p-3">
        {topFirst.length === 0 ? (
          <EmptyState
            icon={Layers}
            title="No layers yet"
            description="Add a product from the built-in catalog to draw its item footprints on the map."
          />
        ) : (
          topFirst.map((layer, index) => (
            <LayerRow
              key={layer.id}
              layer={layer}
              catalogUrl={catalogUrl}
              frameSpan={frameSpan}
              // The list runs topmost first, so index 0 cannot go up and the
              // last row cannot go down.
              canMoveUp={index > 0}
              canMoveDown={index < topFirst.length - 1}
              onVisibleChange={(visible) => onVisibleChange(layer.id, visible)}
              onOpacityChange={(opacity) => onOpacityChange(layer.id, opacity)}
              onMove={(direction) => onMove(layer.id, direction)}
              onRemove={() => onRemove(layer.id)}
            />
          ))
        )}
      </div>
    </aside>
  );
}
