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
import { useCallback, useMemo, useReducer, useRef } from "react";
import { useStore } from "@nanostores/react";
import { StacMap } from "@stac-higher/shared";
import { AppShell } from "@/components/layout/AppShell";
import { FootprintsMapLayer } from "@/components/map/FootprintsMapLayer";
import { LayerPanel } from "@/components/map/LayerPanel";
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

  const addLayer = useCallback((kind: LayerKind, collection: StacCollection) => {
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
  }, []);

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

  return (
    <main className="flex min-h-0 flex-1 overflow-hidden">
      <LayerPanel
        layers={state.layers}
        collections={collections}
        catalogUrl={catalogUrl}
        frameSpan={state.frameSpan}
        onAdd={addLayer}
        onVisibleChange={(id, visible) => dispatch({ type: "setVisible", id, visible })}
        onOpacityChange={(id, opacity) => dispatch({ type: "setOpacity", id, opacity })}
        onMove={(id, direction) => dispatch({ type: "move", id, direction })}
        onRemove={(id) => dispatch({ type: "remove", id })}
      />
      <div className="relative flex min-w-0 flex-1 flex-col">
        <div className="relative min-h-0 flex-1">
          <StacMap className="h-full w-full">
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
