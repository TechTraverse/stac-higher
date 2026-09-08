/**
 * One row of the /map layer list (spec §4.5). The controls land in the next
 * task; this is the identity half plus the quiet "nothing to draw" line.
 */
import { Layers } from "lucide-react";
import { useItems } from "@/lib/query/items";
import { buildPreviewFrames } from "@/lib/serving/frames";
import type { MapLayer } from "@/lib/map/state";

/** Kind icons: `Image` (imagery, V-3) and `Hexagon` (vector, V-4) follow. */
const KIND_ICON = { footprints: Layers } as const;

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
}

export function LayerRow({ layer, catalogUrl, frameSpan }: LayerRowProps) {
  const Icon = KIND_ICON[layer.kind as keyof typeof KIND_ICON] ?? Layers;

  return (
    <div
      data-testid="map-layer-row"
      data-layer-id={layer.id}
      className="rounded-md border border-border bg-background p-2.5"
    >
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {layer.title}
        </span>
      </div>
      {layer.kind === "footprints" && (
        <FootprintStatusLine
          layer={layer}
          catalogUrl={catalogUrl}
          frameSpan={frameSpan}
        />
      )}
    </div>
  );
}
