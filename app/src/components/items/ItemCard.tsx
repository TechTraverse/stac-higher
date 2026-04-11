import type { StacItem } from "@/lib/stac-api/types";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Calendar, MapPin, File } from "lucide-react";

interface ItemCardProps {
  item: StacItem;
  collectionId: string;
}

export function ItemCard({ item, collectionId }: ItemCardProps) {
  const datetime = item.properties.datetime
    ? new Date(item.properties.datetime).toLocaleString()
    : item.properties.start_datetime
      ? `${new Date(item.properties.start_datetime).toLocaleString()} - ${
          item.properties.end_datetime
            ? new Date(item.properties.end_datetime).toLocaleString()
            : "ongoing"
        }`
      : "N/A";

  const assetCount = Object.keys(item.assets).length;
  const geometryType = item.geometry?.type ?? "None";

  return (
    <a
      href={`/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(item.id)}`}
      className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ring-offset-background"
      onKeyDown={(e) => {
        if (e.key === " ") {
          e.preventDefault();
          e.currentTarget.click();
        }
      }}
    >
      <Card className="hover:bg-accent/50 transition-colors cursor-pointer">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-medium truncate">
              {item.id}
            </CardTitle>
            <Badge variant="outline" className="text-xs shrink-0">
              {geometryType}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <Calendar className="h-3 w-3" />
              {datetime}
            </span>
            {item.bbox && (
              <span className="inline-flex items-center gap-1">
                <MapPin className="h-3 w-3" />
                {item.bbox.slice(0, 2).map((n) => n.toFixed(2)).join(", ")}
              </span>
            )}
            <span className="inline-flex items-center gap-1">
              <File className="h-3 w-3" />
              {assetCount} asset{assetCount !== 1 ? "s" : ""}
            </span>
          </div>
        </CardContent>
      </Card>
    </a>
  );
}
