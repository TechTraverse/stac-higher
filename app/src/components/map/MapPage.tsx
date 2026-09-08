/**
 * /map island (V-2, spec §4): the built-in catalog's products as map layers.
 *
 * Layer state is the reducer in `@/lib/map/state` — the page owns it, nothing
 * else reads it, and none of it is persisted in v1 (spec §4.2, §9).
 *
 * Nothing on this page renders an error state (spec §4.7): a product with no
 * timestamped items, an unreachable catalog or an empty collection list all
 * mean "that layer draws nothing" or "that option is absent".
 */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useStore } from "@nanostores/react";
import type { MapMouseEvent, MapRef } from "react-map-gl/maplibre";
import { StacMap, bboxToLngLatBounds, footprintLayerIds } from "@stac-higher/shared";
import { AppShell } from "@/components/layout/AppShell";
import { FootprintsMapLayer } from "@/components/map/FootprintsMapLayer";
import { LayerPanel } from "@/components/map/LayerPanel";
import { MapTooltip } from "@/components/map/MapTooltip";
import { useCollections } from "@/lib/query/collections";
import { INITIAL_MAP_STATE, beforeIdFor, mapReducer } from "@/lib/map/state";
import type { LayerKind } from "@/lib/map/state";
import type { StacCollection } from "@/lib/stac-api/types";
import { $builtInCatalog } from "@/stores/catalogStore";

function MapPageInner() {
  const catalog = useStore($builtInCatalog);
  const catalogUrl = catalog?.url ?? "";
  const [state, dispatch] = useReducer(mapReducer, INITIAL_MAP_STATE);

  const { data: collectionList } = useCollections(catalogUrl);
  const collections = collectionList?.collections ?? [];

  // Layer ids are a per-session counter rather than a UUID: stable, ordered,
  // and readable in a maplibre style inspector or a failing test.
  const nextLayerId = useRef(0);

  const mapRef = useRef<MapRef | null>(null);
  const onMapRef = useCallback((map: MapRef) => {
    mapRef.current = map;
  }, []);

  // What the tooltip shows. The maplibre feature-state that DRAWS the hover
  // is tracked separately, in a ref: it is set on the map imperatively and
  // must be cleared on the previous feature before the next one is set.
  const [hovered, setHovered] = useState<{
    id: string;
    datetime: string;
    x: number;
    y: number;
  } | null>(null);
  const hoveredFeature = useRef<{ source: string; id: string } | null>(null);

  // maplibre's mouse events are not React synthetic events — they arrive
  // through the underlying GL canvas's own listeners, outside any React
  // batch. flushSync keeps the tooltip's DOM in lockstep with each event
  // instead of trailing behind by a tick.
  const clearHover = useCallback(() => {
    const current = hoveredFeature.current;
    if (current) mapRef.current?.setFeatureState(current, { hover: false });
    hoveredFeature.current = null;
    flushSync(() => setHovered(null));
  }, []);

  const onMouseMove = useCallback(
    (e: MapMouseEvent) => {
      const feature = e.features?.[0];
      if (!feature || feature.id === undefined || feature.id === null) {
        clearHover();
        return;
      }

      const next = { source: feature.source, id: String(feature.id) };
      const current = hoveredFeature.current;
      if (current?.source !== next.source || current?.id !== next.id) {
        if (current) mapRef.current?.setFeatureState(current, { hover: false });
        // The styles have read this feature-state since the first map landed;
        // this page is the first thing that sets it.
        mapRef.current?.setFeatureState(next, { hover: true });
        hoveredFeature.current = next;
      }

      const properties = (feature.properties ?? {}) as Record<string, unknown>;
      flushSync(() =>
        setHovered({
          id: String(properties.id ?? next.id),
          datetime: properties.datetime ? String(properties.datetime) : "",
          x: e.point.x,
          y: e.point.y,
        }),
      );
    },
    [clearHover],
  );

  // A row can be removed from the panel with no mouse movement over the
  // canvas in between (the remove button is in the sidebar, not on the
  // map), so the tooltip cannot rely on a future mouseleave/mousemove to
  // notice its layer is gone. Its own source is already gone from the map
  // by the time this runs, so there is nothing left to clear feature-state
  // on — only the React-side readout needs dropping.
  useEffect(() => {
    const current = hoveredFeature.current;
    if (current && !state.layers.some((l) => l.id === current.source)) {
      hoveredFeature.current = null;
      setHovered(null);
    }
  }, [state.layers]);

  const onClick = useCallback(
    (e: MapMouseEvent) => {
      const feature = e.features?.[0];
      if (!feature) return;
      // The footprint source id IS the layer id, which carries the collection.
      const layer = state.layers.find((l) => l.id === feature.source);
      if (!layer) return;
      const properties = (feature.properties ?? {}) as Record<string, unknown>;
      const itemId = String(properties.id ?? feature.id ?? "");
      if (!itemId) return;
      window.open(
        `/collections/${encodeURIComponent(layer.sourceId)}/items/${encodeURIComponent(itemId)}`,
        "_blank",
        "noopener",
      );
    },
    [state.layers],
  );

  // Only the visible footprint layers answer the mouse; imagery and vector
  // layers have no hover or click behaviour in v1 (spec §4.6).
  const interactiveLayerIds = useMemo(
    () =>
      state.layers
        .filter((l) => l.kind === "footprints" && l.visible)
        .map((l) => footprintLayerIds(l.id).fill),
    [state.layers],
  );

  const addLayer = useCallback(
    (kind: LayerKind, collection: StacCollection) => {
      // The FIRST layer moves the camera to what was just added; after that
      // the camera is the viewer's (spec §4.6).
      const isFirst = state.layers.length === 0;
      dispatch({
        type: "add",
        layer: {
          id: `layer-${nextLayerId.current++}`,
          kind,
          sourceId: collection.id,
          title: collection.title ?? collection.id,
          visible: true,
          opacity: 1,
        },
      });
      const bbox = collection.extent?.spatial?.bbox?.[0];
      if (isFirst && bbox) {
        mapRef.current?.fitBounds(bboxToLngLatBounds(bbox), { padding: 50 });
      }
    },
    [state.layers.length],
  );

  // Draw order is bottom-first, but the layer COMPONENTS are rendered
  // topmost-first: react-map-gl creates layers during render, in tree order,
  // via map.addLayer(spec, beforeId), and maplibre fires an error and drops
  // the call when beforeId names a layer that is not in the style yet.
  const drawn = useMemo(
    () =>
      state.layers
        .map((layer, index) => ({ layer, beforeId: beforeIdFor(state.layers, index) }))
        .reverse(),
    [state.layers],
  );

  // Task 8 review: hover state (above) re-renders MapPageInner on every
  // mouse-move tick. These four are handed straight to LayerPanel, which has
  // no memoization of its own, so wrapping them in useCallback does not stop
  // LayerPanel/LayerRow from re-rendering on hover — but it does keep the
  // panel from seeing a fresh function identity on every one of those
  // renders, which is the part this page controls.
  const onVisibleChange = useCallback(
    (id: string, visible: boolean) => dispatch({ type: "setVisible", id, visible }),
    [],
  );
  const onOpacityChange = useCallback(
    (id: string, opacity: number) => dispatch({ type: "setOpacity", id, opacity }),
    [],
  );
  const onMove = useCallback(
    (id: string, direction: "up" | "down") => dispatch({ type: "move", id, direction }),
    [],
  );
  const onRemove = useCallback((id: string) => dispatch({ type: "remove", id }), []);

  return (
    <main className="flex min-h-0 flex-1 overflow-hidden">
      <LayerPanel
        layers={state.layers}
        collections={collections}
        catalogUrl={catalogUrl}
        frameSpan={state.frameSpan}
        onAdd={addLayer}
        onVisibleChange={onVisibleChange}
        onOpacityChange={onOpacityChange}
        onMove={onMove}
        onRemove={onRemove}
      />
      <div className="relative flex min-w-0 flex-1 flex-col">
        <div className="relative min-h-0 flex-1">
          <StacMap
            className="h-full w-full"
            onMapRef={onMapRef}
            interactiveLayerIds={interactiveLayerIds}
            onMouseMove={onMouseMove}
            onMouseLeave={clearHover}
            onClick={onClick}
            cursor={hovered ? "pointer" : undefined}
          >
            {drawn.map(({ layer, beforeId }) =>
              layer.kind === "footprints" ? (
                <FootprintsMapLayer
                  key={layer.id}
                  layer={layer}
                  catalogUrl={catalogUrl}
                  frameSpan={state.frameSpan}
                  beforeId={beforeId}
                />
              ) : null,
            )}
            {/* V-3 renders imagery layers (RasterFrameStack) and V-4 vector
                layers (VectorTileLayer) from the same list; both chain their
                beforeId through the same helper. */}
          </StacMap>
          {hovered && <MapTooltip {...hovered} />}
        </div>
        {/* V-3 docks the shared time bar here, below the map and above the
            page edge. Nothing is rendered in V-2: an empty bar would take
            height from the map for no reason. */}
      </div>
    </main>
  );
}

export function MapPage() {
  return (
    <AppShell>
      <MapPageInner />
    </AppShell>
  );
}
