/**
 * The asset picker on an imagery layer's row (spec §4.5) — the same control
 * the collection Preview tab offers, over the same candidate list.
 *
 * It reads the layer's data through `useLayerData`, which is the very query
 * the layer itself runs: same TanStack key, so the row costs no extra request.
 */
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@stac-higher/shared";
import type { MapLayer } from "@/lib/map/state";
import { useLayerData } from "./useLayerData";

export interface LayerAssetSelectProps {
  layer: MapLayer;
  catalogUrl: string;
  frameSpan: number;
  onAssetChange: (asset: string) => void;
}

export function LayerAssetSelect({
  layer,
  catalogUrl,
  frameSpan,
  onAssetChange,
}: LayerAssetSelectProps) {
  const { candidates, asset } = useLayerData(layer, catalogUrl, frameSpan);
  if (candidates.length < 2 || !asset) return null;

  return (
    <Select value={asset} onValueChange={onAssetChange}>
      <SelectTrigger className="h-7 w-[8.5rem]" aria-label="Layer asset">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {candidates.map((key) => (
          <SelectItem key={key} value={key}>
            {key}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
