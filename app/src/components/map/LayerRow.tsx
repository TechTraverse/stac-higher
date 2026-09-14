/**
 * One row of the /map layer list (spec §4.5): identity, the quiet "nothing
 * to draw" line, the imagery row's asset select, and the
 * visibility/opacity/order/remove controls.
 */
import {
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  Hexagon,
  Image,
  Layers,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button, Slider } from "@stac-higher/shared";
import { useItems } from "@/lib/query/items";
import { buildPreviewFrames } from "@/lib/serving/frames";
import { opacityFromSlider, type LayerKind, type MapLayer } from "@/lib/map/state";
import { LayerAssetSelect } from "@/components/map/LayerAssetSelect";

const KIND_ICON: Record<LayerKind, LucideIcon> = {
  footprints: Layers,
  imagery: Image,
  vector: Hexagon,
};

/**
 * A layer whose items carry no parseable time draws nothing — say so under
 * the title rather than leaving an empty map unexplained. Never an error
 * (spec §4.7). Its own component so the hook stays unconditional while the
 * row itself renders for every kind.
 */
function FootprintStatusLine({
  layer,
  catalogUrl,
  frameSpan,
}: {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
}) {
  const { data, isLoading } = useItems(catalogUrl, layer.sourceId, {
    limit: frameSpan,
    sortby: "-datetime",
  });
  const items = data?.features ?? [];
  if (isLoading || buildPreviewFrames(items).length > 0) return null;

  return (
    <p className="text-xs text-muted-foreground">no items with a timestamp</p>
  );
}

export interface LayerRowProps {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
  canMoveUp: boolean;
  canMoveDown: boolean;
  onVisibleChange: (visible: boolean) => void;
  onOpacityChange: (opacity: number) => void;
  onAssetChange: (asset: string) => void;
  onMove: (direction: "up" | "down") => void;
  onRemove: () => void;
}

export function LayerRow({
  layer,
  catalogUrl,
  frameSpan,
  canMoveUp,
  canMoveDown,
  onVisibleChange,
  onOpacityChange,
  onAssetChange,
  onMove,
  onRemove,
}: LayerRowProps) {
  const Icon = KIND_ICON[layer.kind];

  return (
    <div
      data-testid="map-layer-row"
      data-layer-id={layer.id}
      className="rounded-md border border-border bg-background p-2.5"
    >
      <div className="flex items-center gap-2">
        <Icon
          className="h-4 w-4 shrink-0 text-muted-foreground"
          data-testid={`map-layer-icon-${layer.kind}`}
        />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {layer.title}
        </span>
        <Button
          size="icon-xs"
          variant="ghost"
          data-testid="map-layer-visible"
          aria-label={layer.visible ? "Hide layer" : "Show layer"}
          onClick={() => onVisibleChange(!layer.visible)}
        >
          {layer.visible ? <Eye /> : <EyeOff />}
        </Button>
        <Button
          size="icon-xs"
          variant="ghost"
          data-testid="map-layer-up"
          aria-label="Move layer up"
          disabled={!canMoveUp}
          onClick={() => onMove("up")}
        >
          <ChevronUp />
        </Button>
        <Button
          size="icon-xs"
          variant="ghost"
          data-testid="map-layer-down"
          aria-label="Move layer down"
          disabled={!canMoveDown}
          onClick={() => onMove("down")}
        >
          <ChevronDown />
        </Button>
        <Button
          size="icon-xs"
          variant="ghost"
          data-testid="map-layer-remove"
          aria-label="Remove layer"
          onClick={onRemove}
        >
          <X />
        </Button>
      </div>

      {layer.kind === "footprints" && (
        <FootprintStatusLine
          layer={layer}
          catalogUrl={catalogUrl}
          frameSpan={frameSpan}
        />
      )}

      {layer.kind === "imagery" && (
        <div className="mt-2">
          <LayerAssetSelect
            layer={layer}
            catalogUrl={catalogUrl}
            frameSpan={frameSpan}
            onAssetChange={onAssetChange}
          />
        </div>
      )}

      <Slider
        data-testid="map-layer-opacity"
        aria-label="Layer opacity"
        className="mt-2"
        min={0}
        max={100}
        step={1}
        value={[Math.round(layer.opacity * 100)]}
        // The ONE clamp (V-1 review): the layer components take the number
        // unchanged and maplibre rejects anything outside 0..1.
        onValueChange={(values) => onOpacityChange(opacityFromSlider(values))}
      />
    </div>
  );
}
