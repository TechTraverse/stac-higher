import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MapRef } from "react-map-gl/maplibre";
import { ExternalLink, ImageOff } from "lucide-react";
import {
  bboxToLngLatBounds,
  EmptyState,
  RasterFrameStack,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  StacMap,
  TimeSlider,
} from "@stac-higher/shared";
import type { StacCollection } from "@/lib/stac-api/types";
import { useItems } from "@/lib/query/items";
import { useCollectionSettings } from "@/lib/collections/settings-client";
import { useItemTileJson } from "@/lib/serving/queries";
import { buildPreviewFrames, FRAME_MAX_WAIT_TICKS } from "@/lib/serving/frames";
import { previewAssetCandidates } from "@/lib/serving/preview";
import { collectionTileUrlTemplate, collectionViewerUrl } from "@/lib/serving/urls";

/** How many recent items the slider spans. 50 is ~4 hours of GOES cadence. */
const FRAME_COUNTS = [25, 50, 100, 200];
const DEFAULT_FRAME_COUNT = 50;

interface CollectionPreviewTabProps {
  collection: StacCollection;
  collectionId: string;
  endpointUrl: string;
}

/**
 * An animated raster preview of a product: one frame per timestep, played
 * through titiler-pgstac's collection mosaic.
 *
 * The frames are tile URL templates with `datetime` pinned to a timestep the
 * catalog actually has items for — the mosaic then composites whatever falls
 * in that instant, so a product tiled across several items per timestep works
 * with no extra machinery. Nothing is fetched per frame before it renders;
 * maplibre asks for the tiles. The frame window (previous + current + one
 * lookahead) is `RasterFrameStack`, shared with the /map page.
 *
 * Degradation is silent throughout, as it is for the item preview (G-5): no
 * serving, no tileable asset, or a tiler that cannot open one all mean "no
 * preview", never an error on the product page.
 */
export function CollectionPreviewTab({
  collection,
  collectionId,
  endpointUrl,
}: CollectionPreviewTabProps) {
  const { data: settings } = useCollectionSettings(collectionId);
  const servingEnabled = settings?.servingEnabled === true;

  const [frameCount, setFrameCount] = useState(DEFAULT_FRAME_COUNT);
  const { data, isLoading } = useItems(endpointUrl, collectionId, {
    limit: frameCount,
    sortby: "-datetime",
  });
  const items = useMemo(() => data?.features ?? [], [data]);

  const frames = useMemo(() => buildPreviewFrames(items), [items]);
  const candidates = useMemo(() => previewAssetCandidates(items), [items]);
  const [chosenAsset, setChosenAsset] = useState<string | null>(null);
  const asset = chosenAsset && candidates.includes(chosenAsset) ? chosenAsset : candidates[0];

  // What RasterFrameStack mounts: one tile template per timestep, keyed by
  // the datetime the tiler filters on.
  const rasterFrames = useMemo(
    () =>
      asset
        ? frames.map((f) => ({
            key: f.datetime,
            tiles: [collectionTileUrlTemplate(collectionId, asset, f.datetime)],
          }))
        : [],
    [frames, asset, collectionId],
  );

  // Open on the newest frame — the end of the axis — and return there when the
  // span changes, which is the only thing that reshapes the series. Held as
  // "no choice yet" rather than an index set by an effect, so the first render
  // is already the newest frame and never steps through a stale one.
  const [chosenIndex, setChosenIndex] = useState<number | null>(null);
  useEffect(() => setChosenIndex(null), [frameCount]);
  const lastFrame = Math.max(frames.length - 1, 0);
  const index = chosenIndex === null ? lastFrame : Math.min(chosenIndex, lastFrame);

  // Playback waits for tiles rather than dropping frames: the first pass runs
  // at the tile server's pace, replays at full speed off the browser cache.
  //
  // `areTilesLoaded` covers every mounted source — the frame on screen and the
  // one being warmed — so advancing means the incoming frame is already
  // complete. It is asked a full tick AFTER the frame changed, by which point
  // maplibre has requested the new tiles. Asked any sooner it answers for a
  // source that has not started loading and waves a blank frame through, which
  // is what sank both per-source `isSourceLoaded` and the `idle` event.
  const mapRef = useRef<MapRef | null>(null);
  const onMapRef = useCallback((map: MapRef) => {
    mapRef.current = map;
  }, []);
  const canAdvance = useCallback(() => mapRef.current?.areTilesLoaded() ?? true, []);

  // One TileJSON request, for the newest item, purely to learn the asset's
  // zoom range and footprint: the collection mosaic advertises 0–24 over the
  // whole extent, which would have maplibre asking the tiler to oversample a
  // five-level pyramid. A miss here just means no hints — hence waiting for
  // the query to SETTLE rather than to succeed. Frames wait for it because a
  // maplibre source's zoom range is fixed at creation: mounting before the
  // hint lands both warns and leaves the range wrong.
  const { data: hint, isFetched: hintSettled } = useItemTileJson(
    collectionId,
    items[0]?.id ?? "",
    items.length > 0 ? (asset ?? null) : null,
    servingEnabled,
  );

  if (!servingEnabled) return null;

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-[540px] w-full rounded-lg" />
        <Skeleton className="h-9 w-full" />
      </div>
    );
  }

  if (!asset || frames.length === 0) {
    return (
      <EmptyState
        icon={ImageOff}
        title="No previewable imagery"
        description={
          frames.length === 0
            ? "This product has no items with a usable timestamp yet."
            : "None of this product's recent items carry an asset the tile server can render — a visual-role or COG asset is needed."
        }
      />
    );
  }

  const current = frames[index];
  const bbox = collection.extent?.spatial?.bbox?.[0];

  return (
    <div className="space-y-4">
      <div className="h-[540px] rounded-lg overflow-hidden border border-border">
        <StacMap
          className="h-full w-full"
          onMapRef={onMapRef}
          initialBounds={
            hint?.bounds
              ? bboxToLngLatBounds(hint.bounds)
              : bbox
                ? bboxToLngLatBounds(bbox)
                : undefined
          }
        >
          {hintSettled && (
            <RasterFrameStack
              id="preview"
              frames={rasterFrames}
              index={index}
              bounds={hint?.bounds}
              minzoom={hint?.minzoom}
              maxzoom={hint?.maxzoom}
            />
          )}
        </StacMap>
      </div>

      <TimeSlider
        labels={frames.map((f) => f.label)}
        index={index}
        onIndexChange={setChosenIndex}
        canAdvance={canAdvance}
        maxWaitTicks={FRAME_MAX_WAIT_TICKS}
      >
        {candidates.length > 1 && (
          <Select value={asset} onValueChange={setChosenAsset}>
            <SelectTrigger className="w-[9rem]" aria-label="Preview asset">
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
        )}
        <Select
          value={String(frameCount)}
          onValueChange={(value) => setFrameCount(Number(value))}
        >
          <SelectTrigger className="w-[7.5rem]" aria-label="Frame count">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FRAME_COUNTS.map((count) => (
              <SelectItem key={count} value={String(count)}>
                {count} frames
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </TimeSlider>

      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        Frames rendered by the tile server from the <code>{asset}</code> asset.
        <a
          className="inline-flex items-center gap-1 text-primary hover:underline"
          href={collectionViewerUrl(collectionId, asset, current.datetime)}
          target="_blank"
          rel="noreferrer"
        >
          <ExternalLink className="h-3 w-3" />
          Open viewer
        </a>
      </p>
    </div>
  );
}
