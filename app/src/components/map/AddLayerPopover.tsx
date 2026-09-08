/**
 * The /map "Add layer" picker (spec §4.5).
 *
 * V-2 offers the Products section with Footprints only. The Imagery option
 * (gated on serving + a tileable-asset probe) is V-3 and the Vector tiles
 * section is V-4; both are marked below rather than stubbed, because a
 * disabled control with nothing behind it reads as a broken feature.
 */
import { useState } from "react";
import { Layers, Plus } from "lucide-react";
import { Button } from "@stac-higher/shared";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { StacCollection } from "@/lib/stac-api/types";
import type { LayerKind } from "@/lib/map/state";

interface AddLayerPopoverProps {
  collections: StacCollection[];
  isAdded: (kind: LayerKind, sourceId: string) => boolean;
  onAdd: (kind: LayerKind, collection: StacCollection) => void;
}

export function AddLayerPopover({
  collections,
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
            {collections.map((collection) => {
              const added = isAdded("footprints", collection.id);
              return (
                <li
                  key={collection.id}
                  className="flex items-center gap-2 px-3 py-1.5"
                >
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">
                    {collection.title ?? collection.id}
                  </span>
                  <Button
                    size="xs"
                    variant={added ? "ghost" : "outline"}
                    disabled={added}
                    data-testid={`map-add-footprints-${collection.id}`}
                    onClick={() => {
                      onAdd("footprints", collection);
                      setOpen(false);
                    }}
                  >
                    <Layers />
                    {added ? "Added" : "Footprints"}
                  </Button>
                  {/* V-3 adds an Imagery button beside this one, shown only
                      when the product advertises serving AND a probe of its
                      newest items yields a tileable asset. */}
                </li>
              );
            })}
          </ul>
        )}
        {/* V-4 adds the Vector tiles section here, from tipg's /collections. */}
      </PopoverContent>
    </Popover>
  );
}
