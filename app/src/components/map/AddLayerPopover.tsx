/**
 * The /map "Add layer" picker (spec §4.5).
 *
 * The Products section always offers Footprints; it offers Imagery too, but
 * only once `AddLayerCollectionRow` confirms the product advertises serving
 * AND a probe of its newest items yields a tileable asset — an imagery layer
 * with nothing to draw would violate spec §4.7. The Vector tiles section is
 * fed by `useTipgCollections` while the picker is open (`open` gates the
 * query, so a closed picker never asks tipg); a miss — no tipg, an error, an
 * empty list — is one muted line, never an error (spec §4.7 again).
 */
import { useState } from "react";
import { Hexagon, Plus } from "lucide-react";
import { Button } from "@stac-higher/shared";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { AddLayerCollectionRow } from "@/components/map/AddLayerCollectionRow";
import { useTipgCollections, type TipgCollection } from "@/lib/serving/queries";
import type { StacCollection } from "@/lib/stac-api/types";
import type { LayerKind } from "@/lib/map/state";

interface AddLayerPopoverProps {
  collections: StacCollection[];
  catalogUrl: string;
  isAdded: (kind: LayerKind, sourceId: string) => boolean;
  onAdd: (kind: LayerKind, collection: StacCollection) => void;
  onAddVector: (collection: TipgCollection) => void;
}

export function AddLayerPopover({
  collections,
  catalogUrl,
  isAdded,
  onAdd,
  onAddVector,
}: AddLayerPopoverProps) {
  const [open, setOpen] = useState(false);
  const { data: vectorCollections, isError: vectorError } = useTipgCollections(open);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="sm" data-testid="map-add-layer">
          <Plus />
          Add layer
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="px-3 pb-1 pt-3 text-[10px] font-bold uppercase tracking-[0.08em] text-muted-foreground">
          Products
        </div>
        {collections.length === 0 ? (
          <p className="px-3 pb-3 text-sm text-muted-foreground">
            The built-in catalog has no products yet.
          </p>
        ) : (
          <ul className="max-h-80 overflow-auto pb-2">
            {collections.map((collection) => (
              <AddLayerCollectionRow
                key={collection.id}
                collection={collection}
                catalogUrl={catalogUrl}
                isAdded={isAdded}
                onAdd={(kind, added) => {
                  onAdd(kind, added);
                  setOpen(false);
                }}
              />
            ))}
          </ul>
        )}
        <div className="px-3 pb-1 pt-2 text-[10px] font-bold uppercase tracking-[0.08em] text-muted-foreground">
          Vector tiles
        </div>
        {vectorError || !vectorCollections || vectorCollections.length === 0 ? (
          <p className="px-3 pb-3 text-sm text-muted-foreground" data-testid="map-vector-empty">
            no vector tiles published
          </p>
        ) : (
          <ul className="max-h-60 overflow-auto pb-2">
            {vectorCollections.map((collection) => {
              const added = isAdded("vector", collection.id);
              return (
                <li key={collection.id} className="flex items-center justify-between gap-2 px-3 py-1.5">
                  <span className="min-w-0 truncate text-sm" title={collection.description}>
                    {collection.title ?? collection.id}
                  </span>
                  <Button
                    size="xs"
                    variant={added ? "ghost" : "outline"}
                    disabled={added}
                    data-testid={`map-add-vector-${collection.id}`}
                    onClick={() => {
                      onAddVector(collection);
                      setOpen(false);
                    }}
                  >
                    <Hexagon />
                    {added ? "Added" : "Vector"}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}
