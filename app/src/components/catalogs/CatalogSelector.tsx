import { useStore } from "@nanostores/react";
import { $catalogs } from "@/stores/catalogStore";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@stac-higher/shared";
import { Globe } from "lucide-react";

interface CatalogSelectorProps {
  /** Currently selected catalog id. */
  value: string;
  onChange: (catalogId: string) => void;
}

/**
 * Catalog picker for the two surfaces where browsing an arbitrary catalog is
 * the point: `/search` and `/catalogs`. Controlled by design since UI-10 —
 * there is no global "active catalog" any more, so the selection belongs to
 * the surface that owns it, never to persisted state that product pages could
 * accidentally inherit.
 */
export function CatalogSelector({ value, onChange }: CatalogSelectorProps) {
  const catalogs = useStore($catalogs);

  if (catalogs.length === 0) {
    return (
      <a
        href="/catalogs"
        className="text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        Add a catalog
      </a>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <Globe className="h-4 w-4 text-muted-foreground" />
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-8 w-[200px] text-xs">
          <SelectValue placeholder="Select catalog" />
        </SelectTrigger>
        <SelectContent>
          {catalogs.map((cat) => (
            <SelectItem key={cat.id} value={cat.id} className="text-xs">
              {cat.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
