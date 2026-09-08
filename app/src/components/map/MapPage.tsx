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
import { useReducer } from "react";
import { useStore } from "@nanostores/react";
import { StacMap } from "@stac-higher/shared";
import { AppShell } from "@/components/layout/AppShell";
import { LayerPanel } from "@/components/map/LayerPanel";
import { useCollections } from "@/lib/query/collections";
import { INITIAL_MAP_STATE, mapReducer } from "@/lib/map/state";
import { $builtInCatalog } from "@/stores/catalogStore";

function MapPageInner() {
  const catalog = useStore($builtInCatalog);
  const catalogUrl = catalog?.url ?? "";
  const [state] = useReducer(mapReducer, INITIAL_MAP_STATE);

  const { data: collectionList } = useCollections(catalogUrl);
  const collections = collectionList?.collections ?? [];

  return (
    <main className="flex min-h-0 flex-1 overflow-hidden">
      <LayerPanel
        layers={state.layers}
        collections={collections}
        catalogUrl={catalogUrl}
        frameSpan={state.frameSpan}
      />
      <div className="relative flex min-w-0 flex-1 flex-col">
        <div className="relative min-h-0 flex-1">
          <StacMap className="h-full w-full" />
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
