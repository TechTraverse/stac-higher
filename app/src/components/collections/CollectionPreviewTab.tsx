import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MapRef } from "react-map-gl/maplibre";
import { ExternalLink, ImageOff } from "lucide-react";
import {
  bboxToLngLatBounds,
  EmptyState,
  RasterTileLayer,
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
import { buildPreviewFrames } from "@/lib/serving/frames";
import { previewAssetCandidates } from "@/lib/serving/preview";
import { collectionTileUrlTemplate, collectionViewerUrl } from "@/lib/serving/urls";

/** How many recent items the slider spans. 50 is ~4 hours of GOES cadence. */
const FRAME_COUNTS = [25, 50, 100, 200];
const DEFAULT_FRAME_COUNT = 50;

/**
 * Frames kept mounted ahead of the current one, warming while it plays. ONE:
 * every mounted frame competes for the same handful of connections to the tile
 * server, so a deeper window starves the frame the viewer is actually looking
 * at.
 */
const LOOKAHEAD = 1;

/** Ticks to wait for tiles before advancing regardless — 10s at the default rate. */
const MAX_WAIT_TICKS = 40;

/** The maplibre source id for a frame. */
const frameSourceId = (frameIndex: number) => `preview-frame-${frameIndex}`;

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
 * maplibre asks for the tiles.
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

  // Open on the newest frame — the end of the axis — and return there when the
  // span changes, which is the only thing that reshapes the series. Held as
  // "no choice yet" rather than an index set by an effect, so the first render
  // is already the newest frame and never steps through a stale one.
  const [chosenIndex, setChosenIndex] = useState<number | null>(null);
  useEffect(() => setChosenIndex(null), [frameCount]);
  const lastFrame = Math.max(frames.length - 1, 0);
  const index = chosenIndex === null ? lastFrame : Math.min(chosenIndex, lastFrame);

  // The frame shown before this one, kept painted underneath. Tiles take far
  // longer to render than a playback tick, so without it every step to a cold
  // frame flashes an empty map.
  const previousIndex = useRef(index);
  const lastIndex = useRef(index);
  if (lastIndex.current !== index) {
    previousIndex.current = lastIndex.current;
    lastIndex.current = index;
  }

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

  // Draw order, bottom to top: the previous frame, then the current one over
  // it, then the lookahead frames loading invisibly. Deduped — a series
  // shorter than the window would otherwise repeat itself.
  const mounted = [
    ...new Set([
      previousIndex.current % frames.length,
      ...Array.from(
        { length: LOOKAHEAD + 1 },
        (_, offset) => (index + offset) % frames.length,
      ),
    ]),
  ];

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
          {hintSettled &&
            mounted.map((frameIndex) => (
              <RasterTileLayer
                key={frames[frameIndex].datetime}
                id={`preview-frame-${frameIndex}`}
                tiles={[
                  collectionTileUrlTemplate(collectionId, asset, frames[frameIndex].datetime),
                ]}
                bounds={hint?.bounds}
                minzoom={hint?.minzoom}
                maxzoom={hint?.maxzoom}
                // Frames are swapped by opacity against tiles maplibre already
                // holds; a fade would smear one timestep into the next.
                opacity={
                  frameIndex === index || frameIndex === previousIndex.current ? 1 : 0
                }
                opacityTransitionMs={0}
              />
            ))}
        </StacMap>
      </div>

      <TimeSlider
        labels={frames.map((f) => f.label)}
        index={index}
        onIndexChange={setChosenIndex}
        canAdvance={canAdvance}
        maxWaitTicks={MAX_WAIT_TICKS}
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
