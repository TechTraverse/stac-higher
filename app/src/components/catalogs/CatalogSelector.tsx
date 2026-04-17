import { useStore } from "@nanostores/react";
import {
  $catalogs,
  $activeCatalog,
  setActiveCatalog,
} from "@/stores/catalogStore";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@stac-higher/shared";
import { Globe } from "lucide-react";

export function CatalogSelector() {
  const catalogs = useStore($catalogs);
  const active = useStore($activeCatalog);

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
      <Select value={active?.id ?? ""} onValueChange={setActiveCatalog}>
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
