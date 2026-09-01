import type { StacCollection } from "@/lib/stac-api/types";
import { StacMap } from "@stac-higher/shared";
import { ExtentLayer } from "@stac-higher/shared";
import { bboxToLngLatBounds } from "@/lib/map/bbox";
import { Badge } from "@stac-higher/shared";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@stac-higher/shared";
import { MapPin, Calendar, ExternalLink } from "lucide-react";

function formatBbox(bbox: number[]): string {
  if (bbox.length < 4) return "N/A";
  return `[${bbox.map((n) => n.toFixed(4)).join(", ")}]`;
}

/**
 * The raw STAC metadata for a collection: description, extents, license,
 * extensions, providers. Deliberately catalog-agnostic — it renders a
 * `StacCollection` and nothing else — so the product page (below its platform
 * Overview panel) and the read-only catalog browser show the same facts
 * instead of drifting apart.
 */
export function CollectionMetadata({
  collection,
}: {
  collection: StacCollection;
}) {
  const bbox = collection.extent?.spatial?.bbox?.[0];
  const temporal = collection.extent?.temporal?.interval?.[0];

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Description</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground whitespace-pre-wrap">
            {collection.description}
          </p>
        </CardContent>
      </Card>

      {bbox && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <MapPin className="h-4 w-4" />
              Spatial Extent
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="h-[300px] rounded-lg overflow-hidden border border-border">
              <StacMap initialBounds={bboxToLngLatBounds(bbox)}>
                <ExtentLayer bbox={bbox} />
              </StacMap>
            </div>
            <p className="text-sm font-mono text-muted-foreground">
              {formatBbox(bbox)}
            </p>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {!bbox && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <MapPin className="h-4 w-4" />
                Spatial Extent
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                No spatial extent defined
              </p>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Calendar className="h-4 w-4" />
              Temporal Extent
            </CardTitle>
          </CardHeader>
          <CardContent>
            {temporal ? (
              <div className="text-sm text-muted-foreground">
                <p>Start: {temporal[0] ?? "Open"}</p>
                <p>End: {temporal[1] ?? "Ongoing"}</p>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No temporal extent defined
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-wrap gap-2">
        <Badge variant="secondary">{collection.license}</Badge>
        <Badge variant="outline">STAC {collection.stac_version}</Badge>
        {collection.stac_extensions?.map((url) => {
          const label =
            url.split("/").filter(Boolean).slice(-3, -1).join(" ") || url;
          return (
            <a
              key={url}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              title={url}
            >
              <Badge
                variant="outline"
                className="text-xs tech hover:bg-accent/50 transition-colors"
              >
                {label}
              </Badge>
            </a>
          );
        })}
      </div>

      {collection.providers && collection.providers.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Providers</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {collection.providers.map((provider, i) => (
              <div key={i} className="flex items-start justify-between">
                <div>
                  <p className="text-sm font-medium">{provider.name}</p>
                  {provider.description && (
                    <p className="text-xs text-muted-foreground">
                      {provider.description}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-1.5">
                  {provider.roles?.map((role) => (
                    <Badge key={role} variant="outline" className="text-xs">
                      {role}
                    </Badge>
                  ))}
                  {provider.url && (
                    <a
                      href={provider.url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
                    </a>
                  )}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </>
  );
}
