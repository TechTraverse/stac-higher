import type { StacCollection } from "@/lib/stac-api/types";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { MapPin, Calendar, Scale } from "lucide-react";

interface CollectionCardProps {
  collection: StacCollection;
}

function formatBbox(bbox: number[]): string {
  if (bbox.length < 4) return "N/A";
  return `${bbox[0].toFixed(2)}, ${bbox[1].toFixed(2)} to ${bbox[2].toFixed(2)}, ${bbox[3].toFixed(2)}`;
}

function formatTemporalRange(interval: (string | null)[][]): string {
  if (!interval?.[0]) return "N/A";
  const [start, end] = interval[0];
  const s = start ? new Date(start).toLocaleDateString() : "...";
  const e = end ? new Date(end).toLocaleDateString() : "ongoing";
  return `${s} - ${e}`;
}

export function CollectionCard({ collection }: CollectionCardProps) {
  const bbox = collection.extent?.spatial?.bbox?.[0];
  const temporal = collection.extent?.temporal?.interval;

  return (
    <a
      href={`/collections/${encodeURIComponent(collection.id)}`}
      className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ring-offset-background"
      onKeyDown={(e) => {
        if (e.key === " ") {
          e.preventDefault();
          e.currentTarget.click();
        }
      }}
    >
      <Card className="hover:bg-accent/50 transition-colors cursor-pointer h-full">
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <CardTitle className="text-base truncate">
                {collection.title || collection.id}
              </CardTitle>
              {collection.title && (
                <p className="text-xs text-muted-foreground font-mono mt-0.5">
                  {collection.id}
                </p>
              )}
            </div>
            <Badge variant="secondary" className="shrink-0 text-xs">
              {collection.license}
            </Badge>
          </div>
          <CardDescription className="line-clamp-2 text-xs">
            {collection.description}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-1.5">
          {bbox && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <MapPin className="h-3 w-3 shrink-0" />
              <span className="truncate">{formatBbox(bbox)}</span>
            </div>
          )}
          {temporal && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Calendar className="h-3 w-3 shrink-0" />
              <span className="truncate">{formatTemporalRange(temporal)}</span>
            </div>
          )}
          {collection.keywords && collection.keywords.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-1">
              {collection.keywords.slice(0, 3).map((kw) => (
                <Badge key={kw} variant="outline" className="text-[10px] px-1.5 py-0">
                  {kw}
                </Badge>
              ))}
              {collection.keywords.length > 3 && (
                <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                  +{collection.keywords.length - 3}
                </Badge>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </a>
  );
}
