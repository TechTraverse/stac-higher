import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MapMouseEvent, MapRef } from "react-map-gl/maplibre";
import { Boxes } from "lucide-react";
import {
  bboxToLngLatBounds,
  EmptyState,
  Label,
  RasterFrameStack,
  RasterTileLayer,
  Skeleton,
  StacMap,
  Switch,
  TimeSeriesChart,
  TimeSlider,
} from "@stac-higher/shared";
import type { StacCollection } from "@/lib/stac-api/types";
import {
  buildCubeFrames,
  CUBE_DEFAULT_FRAME_COUNT,
  cubeVariable,
  type CubeAssetRef,
} from "@/lib/serving/cube-frames";
import { FRAME_MAX_WAIT_TICKS } from "@/lib/serving/frames";
import {
  CUBE_TILE_RETRY_MS,
  frameIndexFromSourceId,
  isNotYetTileBody,
  nextRetry,
} from "@/lib/serving/cube-retry";
import {
  CUBE_WARM_CONCURRENCY,
  CUBE_WARM_TIMEOUT_MS,
  frameIndexFromWarmId,
  nextWarmFrames,
  warmSourceId,
} from "@/lib/serving/cube-warm";
import { useCubePosition } from "@/lib/serving/queries";
import { cubeTileUrlTemplate, roundCoord } from "@/lib/serving/urls";

/** ABI C13 is 2 km; maplibre over-zooms the last level past it. */
const CUBE_PREVIEW_MAXZOOM = 7;
const STACK_ID = "cube";

interface CubePreviewProps {
  collection: StacCollection;
  collectionId: string;
  cube: CubeAssetRef;
}

/**
 * The Preview of a virtual cube (Z-8): an animated map of the cube's frames,
 * rendered by cube-server, and a click-for-time-series chart (EDR).
 *
 * Reads only the collection document: the frames are the asset's
 * `stac_higher:time_values`, the tiles and EDR come from cube-server. No
 * items, no TileJSON, no sink API (viewers outside the group get a 404 there).
 */
export function CubePreview({ collection, collectionId, cube }: CubePreviewProps) {
  const variable = cubeVariable(collection);
  const rawBbox = collection.extent?.spatial?.bbox?.[0];
  // Only a plain 2-D, non-antimeridian box can fit the map and clip the tiles.
  const bbox =
    rawBbox &&
    rawBbox.length === 4 &&
    rawBbox.every((n) => Number.isFinite(n)) &&
    rawBbox[0] <= rawBbox[2]
      ? (rawBbox as [number, number, number, number])
      : undefined;

  const [showAll, setShowAll] = useState(false);
  const frames = useMemo(
    () => buildCubeFrames(cube.timeValues, showAll ? undefined : CUBE_DEFAULT_FRAME_COUNT),
    [cube.timeValues, showAll],
  );

  // A 422 retry remounts a frame by changing its React key (the URL cannot
  // change: an unknown query parameter is read as a dimension selector).
  const [attempts, setAttempts] = useState<Record<string, number>>({});
  const attemptsRef = useRef(attempts);
  attemptsRef.current = attempts;
  const rasterFrames = useMemo(
    () =>
      frames.map((f) => ({
        key: `${f.t}#${attempts[f.t] ?? 0}`,
        tiles: [cubeTileUrlTemplate(collectionId, variable, f.t)],
      })),
    [frames, attempts, collectionId, variable],
  );

  // Open on the newest frame (null = no choice yet) and return there when the
  // span changes, as the item Preview does.
  const [chosenIndex, setChosenIndex] = useState<number | null>(null);
  useEffect(() => setChosenIndex(null), [showAll]);
  const lastFrame = Math.max(frames.length - 1, 0);
  const index = chosenIndex === null ? lastFrame : Math.min(chosenIndex, lastFrame);

  // Preload progress: a frame counts once maplibre reports its source loaded
  // (every in-view tile in), from `sourcedata` for any mounted frame (the
  // shown one and the lookahead), plus `idle` / `canAdvance` for the current one.
  const [loaded, setLoaded] = useState<ReadonlySet<string>>(new Set());
  const framesRef = useRef(frames);
  framesRef.current = frames;
  const indexRef = useRef(index);
  indexRef.current = index;
  const retrying = useRef(new Set<string>());
  const markLoaded = useCallback((t: string | undefined) => {
    if (t === undefined || retrying.current.has(t)) return;
    setLoaded((prev) => (prev.has(t) ? prev : new Set(prev).add(t)));
  }, []);
  const markCurrentLoaded = useCallback(
    () => markLoaded(framesRef.current[indexRef.current]?.t),
    [markLoaded],
  );

  // The frames mount only once the map has loaded. maplibre fires `load` only
  // when EVERY source's in-view tiles are in; cube tiles are slow virtual
  // reads, and playback keeps mounting new frame sources, so frames mounted
  // up front held `load` off indefinitely. StacMap fits the bounds and hands
  // over the map ref on `load`, so the view stayed at the world default, no
  // listener was attached, and playback never waited for tiles (live check
  // of #172). Mounting after `load` also lets the basemap finish before cube
  // tiles take maplibre's shared image-request slots.
  const [mapReady, setMapReady] = useState(false);

  // Background warm-up: once the current frame has loaded, hidden sources
  // (opacity 0, so maplibre loads them) fetch the other frames' tiles a couple
  // at a time; they land in the browser HTTP cache (cube tiles are immutable),
  // so the stack's own sources read them from there. `warming` holds the `t`s
  // mounted now; `dropped` those that timed out (not retried this window).
  const [warming, setWarming] = useState<string[]>([]);
  const [dropped, setDropped] = useState<ReadonlySet<string>>(new Set());
  const warmTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  useEffect(() => {
    // A new window: nothing carries over.
    setWarming([]);
    setDropped(new Set());
  }, [showAll]);
  const currentLoaded = loaded.has(frames[index]?.t ?? "");
  useEffect(() => {
    if (!mapReady || !currentLoaded) return;
    // The stack mounts the previous, current and lookahead frames itself.
    const n = frames.length;
    const mounted = new Set(
      [index - 1, index, index + 1].map((i) => frames[((i % n) + n) % n]?.t).filter((t) => t !== undefined),
    );
    const skip = new Set([...loaded, ...dropped]);
    const ts = frames.map((f) => f.t);
    // Functional, so it composes with the window-change reset queued above.
    setWarming((prev) => {
      const start = nextWarmFrames(ts, skip, mounted, prev, CUBE_WARM_CONCURRENCY, index);
      return start.length > 0 ? [...prev, ...start] : prev;
    });
  }, [mapReady, currentLoaded, frames, index, loaded, dropped, warming]);
  useEffect(() => {
    // One timeout per warm source; leaving `warming` clears it.
    const timersMap = warmTimers.current;
    for (const t of warming) {
      if (timersMap.has(t)) continue;
      timersMap.set(
        t,
        setTimeout(() => {
          timersMap.delete(t);
          setWarming((prev) => prev.filter((x) => x !== t));
          setDropped((prev) => new Set(prev).add(t));
        }, CUBE_WARM_TIMEOUT_MS),
      );
    }
    for (const [t, timer] of timersMap) {
      if (!warming.includes(t)) {
        clearTimeout(timer);
        timersMap.delete(t);
      }
    }
  }, [warming]);
  useEffect(() => {
    const timersMap = warmTimers.current;
    return () => {
      timersMap.forEach(clearTimeout);
      timersMap.clear();
    };
  }, []);

  const mapRef = useRef<MapRef | null>(null);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  // A body read can settle after unmount; it must not schedule a timer then.
  const alive = useRef(true);
  const detach = useRef<(() => void) | null>(null);
  const onMapRef = useCallback(
    (ref: MapRef) => {
      mapRef.current = ref;
      detach.current?.();
      const onIdle = () => markCurrentLoaded();
      const onSourceData = (e: { sourceId?: string; isSourceLoaded?: boolean }) => {
        if (e.isSourceLoaded !== true) return;
        const n = frameIndexFromSourceId(STACK_ID, e.sourceId);
        if (n !== null) markLoaded(framesRef.current[n]?.t);
        const w = frameIndexFromWarmId(e.sourceId);
        if (w !== null) {
          const t = framesRef.current[w]?.t;
          markLoaded(t);
          if (t !== undefined) setWarming((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : prev));
        }
      };
      const onError = (e: { sourceId?: string; error?: { status?: number; body?: unknown } }) => {
        const n = frameIndexFromSourceId(STACK_ID, e.sourceId);
        if (n === null || e.error?.status !== 422) return;
        const t = framesRef.current[n]?.t;
        if (t === undefined) return;
        // Only a 422 about the `t` selector means "server behind"; a bad style
        // or range would 422 on every retry. maplibre's AJAXError carries the
        // body as a Blob; without one, retry (capped).
        const body = e.error.body;
        if (body instanceof Blob) {
          void isNotYetTileBody(body).then((notYet) => {
            if (notYet && alive.current) scheduleRetry(t);
          });
          return;
        }
        scheduleRetry(t);
      };
      const scheduleRetry = (t: string) => {
        if (retrying.current.has(t)) return;
        if (nextRetry(attemptsRef.current, t) === null) return;
        retrying.current.add(t);
        setLoaded((prev) => {
          if (!prev.has(t)) return prev;
          const next = new Set(prev);
          next.delete(t);
          return next;
        });
        const timer = setTimeout(() => {
          timers.current.delete(timer);
          retrying.current.delete(t);
          // The ref moves first: a second 422 before React re-renders must
          // not schedule from a stale count.
          const next = nextRetry(attemptsRef.current, t);
          if (next === null) return;
          attemptsRef.current = next;
          setAttempts(next);
        }, CUBE_TILE_RETRY_MS);
        timers.current.add(timer);
      };
      // The map's own events: StacMap exposes only the ref.
      const map = ref as unknown as {
        on: (type: string, fn: (e: never) => void) => void;
        off: (type: string, fn: (e: never) => void) => void;
      };
      map.on("idle", onIdle);
      map.on("sourcedata", onSourceData as (e: never) => void);
      map.on("error", onError as (e: never) => void);
      detach.current = () => {
        map.off("idle", onIdle);
        map.off("sourcedata", onSourceData as (e: never) => void);
        map.off("error", onError as (e: never) => void);
      };
      setMapReady(true);
    },
    [markCurrentLoaded, markLoaded],
  );
  useEffect(() => {
    const pending = timers.current;
    alive.current = true;
    return () => {
      alive.current = false;
      pending.forEach(clearTimeout);
      pending.clear();
      detach.current?.();
    };
  }, []);

  // Playback waits for tiles (RasterFrameStack's one-frame lookahead is
  // deliberate: more starves the visible frame). A frame whose tiles errored
  // still counts as loaded for `areTilesLoaded`, so a failed one never blocks.
  const canAdvance = useCallback(() => {
    const ok = mapRef.current?.areTilesLoaded() ?? true;
    if (ok) markCurrentLoaded();
    return ok;
  }, [markCurrentLoaded]);

  const [point, setPoint] = useState<{ lon: number; lat: number } | null>(null);
  const position = useCubePosition(collectionId, variable, point);
  const onClick = useCallback(
    (e: MapMouseEvent) => {
      // wrap(): a click on a world copy reports lng beyond +-180; round once so
      // the query key and the URL agree.
      const { lng, lat } = e.lngLat.wrap();
      setPoint({ lon: roundCoord(lng), lat: roundCoord(lat) });
    },
    [],
  );

  if (frames.length === 0) {
    return (
      <EmptyState
        icon={Boxes}
        title="The cube has no steps yet"
        description="The first append publishes the time axis; check the Settings tab's Virtual cube card."
      />
    );
  }

  const loadedCount = frames.filter((f) => loaded.has(f.t)).length;

  return (
    <div className="space-y-4">
      <div className="h-[540px] rounded-lg overflow-hidden border border-border">
        <StacMap
          className="h-full w-full"
          onMapRef={onMapRef}
          onClick={onClick}
          cursor="crosshair"
          initialBounds={bbox ? bboxToLngLatBounds(bbox) : undefined}
        >
          {mapReady && (
            <RasterFrameStack
              id={STACK_ID}
              frames={rasterFrames}
              index={index}
              bounds={bbox}
              minzoom={0}
              maxzoom={CUBE_PREVIEW_MAXZOOM}
            />
          )}
          {mapReady &&
            warming.map((t) => {
              const n = frames.findIndex((f) => f.t === t);
              if (n < 0) return null;
              return (
                <RasterTileLayer
                  key={`warm-${t}`}
                  id={warmSourceId(n)}
                  tiles={[cubeTileUrlTemplate(collectionId, variable, t)]}
                  bounds={bbox}
                  minzoom={0}
                  maxzoom={CUBE_PREVIEW_MAXZOOM}
                  opacity={0}
                  opacityTransitionMs={0}
                />
              );
            })}
        </StacMap>
      </div>

      <TimeSlider
        labels={frames.map((f) => f.label)}
        index={index}
        onIndexChange={setChosenIndex}
        canAdvance={canAdvance}
        maxWaitTicks={FRAME_MAX_WAIT_TICKS}
      >
        {cube.timeValues.length > CUBE_DEFAULT_FRAME_COUNT && (
          <div className="flex items-center gap-2">
            <Switch id="cube-show-all" checked={showAll} onCheckedChange={setShowAll} />
            <Label htmlFor="cube-show-all" className="text-xs">
              Show full window ({cube.timeValues.length} steps)
            </Label>
          </div>
        )}
      </TimeSlider>

      <p aria-live="polite" className="text-xs text-muted-foreground min-h-4">
        {loadedCount < frames.length && `Loaded ${loadedCount} of ${frames.length} frames`}
      </p>

      <div className="rounded-lg border border-border p-4 space-y-2">
        {point === null ? (
          <p className="text-sm text-muted-foreground">
            Click the map for a time series at that point.
          </p>
        ) : position.isLoading ? (
          <Skeleton className="h-[160px] w-full" />
        ) : position.isError ? (
          <p className="text-sm text-destructive">
            Time series unavailable: {position.error.message}
          </p>
        ) : position.data ? (
          <>
            <TimeSeriesChart
              points={position.data.points}
              unit={variable === "CMI" ? "K" : undefined}
              ariaLabel={`${variable} at ${point.lat.toFixed(4)}, ${point.lon.toFixed(4)}`}
            />
            {position.data.missingSteps > 0 && (
              <p className="text-xs text-muted-foreground">
                {position.data.missingSteps} of {position.data.points.length} steps unavailable: the
                source file is missing or unreachable.
              </p>
            )}
          </>
        ) : null}
      </div>

      <p className="text-xs text-muted-foreground">
        Frames rendered by the cube server from the virtual <code>{cube.key}</code> asset; no
        source bytes are copied.
      </p>
    </div>
  );
}
