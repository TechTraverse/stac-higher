/**
 * The /map "Add layer" picker (spec §4.5).
 *
 * The Products section always offers Footprints; it offers Imagery too, but
 * only once `AddLayerCollectionRow` confirms the product advertises serving
 * AND a probe of its newest items yields a tileable asset — an imagery layer
 * with nothing to draw would violate spec §4.7. The Vector tiles section
 * (V-4) is marked below rather than stubbed, because a disabled control with
 * nothing behind it reads as a broken feature.
 */
import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@stac-higher/shared";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { AddLayerCollectionRow } from "@/components/map/AddLayerCollectionRow";
import type { StacCollection } from "@/lib/stac-api/types";
import type { LayerKind } from "@/lib/map/state";

interface AddLayerPopoverProps {
  collections: StacCollection[];
  catalogUrl: string;
  isAdded: (kind: LayerKind, sourceId: string) => boolean;
  onAdd: (kind: LayerKind, collection: StacCollection) => void;
}

export function AddLayerPopover({
  collections,
  catalogUrl,
  isAdded,
  onAdd,
}: AddLayerPopoverProps) {
  const [open, setOpen] = useState(false);

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
        {/* V-4 adds the Vector tiles section here, from tipg's /collections. */}
      </PopoverContent>
    </Popover>
  );
}
