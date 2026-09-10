/**
 * One collection's entry in the Add-layer popover's Products section
 * (spec §4.5).
 *
 * A component per collection rather than markup in a loop, because each row
 * runs its own two queries: the settings that say whether the product
 * advertises serving, and a small probe of its newest items that says whether
 * anything there is tileable. Both are cheap and both are needed before the
 * Imagery option can honestly be offered — an imagery layer with no candidate
 * asset would add a row that draws nothing, which spec §4.7 forbids.
 */
import { Image, Layers } from "lucide-react";
import { Button } from "@stac-higher/shared";
import type { StacCollection } from "@/lib/stac-api/types";
import type { LayerKind } from "@/lib/map/state";
import { useItems } from "@/lib/query/items";
import { useCollectionSettings } from "@/lib/collections/settings-client";
import { previewAssetCandidates } from "@/lib/serving/preview";

/** Items the imagery probe looks at. Enough to see the product's assets. */
export const ADD_LAYER_PROBE_LIMIT = 5;

export interface AddLayerCollectionRowProps {
  collection: StacCollection;
  catalogUrl: string;
  isAdded: (kind: LayerKind, sourceId: string) => boolean;
  onAdd: (kind: LayerKind, collection: StacCollection) => void;
}

export function AddLayerCollectionRow({
  collection,
  catalogUrl,
  isAdded,
  onAdd,
}: AddLayerCollectionRowProps) {
  const { data: settings } = useCollectionSettings(collection.id);
  const { data } = useItems(catalogUrl, collection.id, {
    limit: ADD_LAYER_PROBE_LIMIT,
    sortby: "-datetime",
  });
  const imageryOffered =
    settings?.servingEnabled === true &&
    previewAssetCandidates(data?.features ?? []).length > 0;

  const footprintsAdded = isAdded("footprints", collection.id);
  const imageryAdded = isAdded("imagery", collection.id);

  return (
    <li className="flex items-center gap-2 px-3 py-1.5">
      <span className="min-w-0 flex-1 truncate text-sm font-medium">
        {collection.title ?? collection.id}
      </span>
      <Button
        size="xs"
        variant={footprintsAdded ? "ghost" : "outline"}
        disabled={footprintsAdded}
        data-testid={`map-add-footprints-${collection.id}`}
        onClick={() => onAdd("footprints", collection)}
      >
        <Layers />
        {footprintsAdded ? "Added" : "Footprints"}
      </Button>
      {imageryOffered && (
        <Button
          size="xs"
          variant={imageryAdded ? "ghost" : "outline"}
          disabled={imageryAdded}
          data-testid={`map-add-imagery-${collection.id}`}
          onClick={() => onAdd("imagery", collection)}
        >
          <Image />
          {imageryAdded ? "Added" : "Imagery"}
        </Button>
      )}
    </li>
  );
}
