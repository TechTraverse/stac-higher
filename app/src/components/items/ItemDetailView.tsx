import type { StacItem, StacLink } from "@/lib/stac-api/types";
import { resolveItemLink, type LinkTarget } from "@/lib/browse/paths";
import { JsonViewer } from "@stac-higher/shared";
import { StacMap, RasterTileLayer } from "@stac-higher/shared";
import { Source, Layer } from "react-map-gl/maplibre";
import { bboxToLngLatBounds } from "@/lib/map/bbox";
import { Button } from "@stac-higher/shared";
import { Badge } from "@stac-higher/shared";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@stac-higher/shared";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ExternalLink, Download } from "lucide-react";

/**
 * Everything a STAC item shows regardless of which catalog it came from:
 * properties, assets, geometry, raw JSON. Catalog-agnostic on purpose — the
 * product item page (which adds edit/delete around it) and the read-only
 * catalog browser render the SAME view, so the two cannot drift.
 */
/** Tiles for this item, when the tile server rendered it (G-5). */
export interface ItemRasterPreview {
  tiles: string[];
  bounds?: [number, number, number, number];
  /** titiler's own viewer for the same item + asset. */
  viewerUrl?: string;
}

const REL_DERIVED_FROM = "derived_from";

/** Without a resolver the view knows no catalogs: root-relative hrefs go to
 *  the product routes, everything else opens as written. */
function defaultResolveLink(link: StacLink): LinkTarget {
  return resolveItemLink(link, null, []);
}

export function ItemDetailView({
  item,
  rasterPreview,
  resolveLink = defaultResolveLink,
}: {
  item: StacItem;
  rasterPreview?: ItemRasterPreview;
  /** Maps a link href onto an in-app page (D-2). The page supplies one built
   *  from its own catalog context so this view stays catalog-agnostic. */
  resolveLink?: (link: StacLink) => LinkTarget;
}) {
  const properties = Object.entries(item.properties).filter(
    ([key]) => !key.startsWith("_"),
  );
  // Upstream lineage only (D-2): the links this item carries. Nothing is
  // rendered when there are none — no empty state.
  const derivedFrom = (item.links ?? []).filter((link) => link.rel === REL_DERIVED_FROM);

  return (
      <Tabs defaultValue="properties" className="space-y-4">
        <TabsList>
          <TabsTrigger value="properties">Properties</TabsTrigger>
          <TabsTrigger value="assets">
            Assets ({Object.keys(item.assets).length})
          </TabsTrigger>
          <TabsTrigger value="geometry">Geometry</TabsTrigger>
          <TabsTrigger value="json">Raw JSON</TabsTrigger>
        </TabsList>

        <TabsContent value="properties">
          {derivedFrom.length > 0 && (
            <div
              className="flex flex-wrap items-center gap-2 mb-3"
              data-testid="derived-from"
            >
              <span className="text-xs text-muted-foreground">Derived from</span>
              {derivedFrom.map((link, index) => {
                const target = resolveLink(link);
                return (
                  <a
                    key={`${link.href}-${index}`}
                    href={target.href}
                    title={link.href}
                    {...(target.external
                      ? { target: "_blank", rel: "noopener noreferrer" }
                      : {})}
                  >
                    <Badge
                      variant="outline"
                      className="text-xs font-mono hover:bg-accent/50 transition-colors"
                    >
                      {target.external && <ExternalLink className="h-3 w-3 mr-1" />}
                      {target.label}
                    </Badge>
                  </a>
                );
              })}
            </div>
          )}
          {item.stac_extensions && item.stac_extensions.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {item.stac_extensions.map((url) => {
                const label = url.split("/").filter(Boolean).slice(-3, -1).join(" ") || url;
                return (
                  <a key={url} href={url} target="_blank" rel="noopener noreferrer" title={url}>
                    <Badge variant="outline" className="text-xs font-mono hover:bg-accent/50 transition-colors">
                      {label}
                    </Badge>
                  </a>
                );
              })}
            </div>
          )}
          <Card>
            <CardContent className="pt-6">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[200px]">Property</TableHead>
                    <TableHead>Value</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {properties.map(([key, value]) => (
                    <TableRow key={key}>
                      <TableCell className="font-mono text-xs">{key}</TableCell>
                      <TableCell className="text-sm">
                        {typeof value === "object"
                          ? JSON.stringify(value)
                          : String(value ?? "null")}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="assets">
          <div className="grid gap-3">
            {Object.entries(item.assets).map(([key, asset]) => (
              <Card key={key}>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm">
                      {asset.title || key}
                    </CardTitle>
                    <div className="flex items-center gap-2">
                      {asset.roles?.map((role) => (
                        <Badge key={role} variant="outline" className="text-xs">
                          {role}
                        </Badge>
                      ))}
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  {asset.description && (
                    <p className="text-xs text-muted-foreground mb-2">
                      {asset.description}
                    </p>
                  )}
                  <div className="flex items-center justify-between">
                    <div className="text-xs text-muted-foreground">
                      {asset.type && (
                        <span className="font-mono">{asset.type}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      <a href={asset.href} target="_blank" rel="noopener noreferrer">
                        <Button variant="ghost" size="sm">
                          <ExternalLink className="h-3.5 w-3.5 mr-1" />
                          Open
                        </Button>
                      </a>
                      <a href={asset.href} download>
                        <Button variant="ghost" size="sm">
                          <Download className="h-3.5 w-3.5 mr-1" />
                          Download
                        </Button>
                      </a>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="geometry" className="space-y-4">
          {item.geometry && (
            <div className="h-[400px] rounded-lg overflow-hidden border border-border">
              <StacMap
                initialBounds={
                  item.bbox ? bboxToLngLatBounds(item.bbox) : undefined
                }
              >
                <Source
                  id="item-geometry"
                  type="geojson"
                  data={{
                    type: "Feature",
                    properties: {},
                    geometry: item.geometry,
                  }}
                >
                  <Layer
                    id="item-geometry-fill"
                    type="fill"
                    paint={{ "fill-color": "#3b82f6", "fill-opacity": 0.15 }}
                  />
                  <Layer
                    id="item-geometry-line"
                    type="line"
                    paint={{ "line-color": "#3b82f6", "line-width": 2 }}
                  />
                  <Layer
                    id="item-geometry-point"
                    type="circle"
                    filter={["==", "$type", "Point"]}
                    paint={{
                      "circle-color": "#3b82f6",
                      "circle-radius": 6,
                      "circle-stroke-color": "#fff",
                      "circle-stroke-width": 2,
                    }}
                  />
                </Source>
                {rasterPreview && (
                  // AFTER the geometry source, so `item-geometry-fill` exists
                  // when maplibre inserts this: `beforeId` naming a layer that
                  // has not been added yet is a hard error, not a no-op. Being
                  // last in source order also keeps it correct when the tiles
                  // arrive asynchronously, long after the footprint is drawn.
                  <RasterTileLayer
                    tiles={rasterPreview.tiles}
                    bounds={rasterPreview.bounds}
                    beforeId="item-geometry-fill"
                  />
                )}
              </StacMap>
            </div>
          )}
          {rasterPreview && (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Preview rendered by the tile server
              {rasterPreview.viewerUrl && (
                <a
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                  href={rasterPreview.viewerUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink className="h-3 w-3" />
                  Open viewer
                </a>
              )}
            </p>
          )}
          {item.bbox && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Bounding Box</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="font-mono text-sm text-muted-foreground">
                  [{item.bbox.map((n) => n.toFixed(6)).join(", ")}]
                </p>
              </CardContent>
            </Card>
          )}
          <JsonViewer data={item.geometry} title="GeoJSON Geometry" />
        </TabsContent>

        <TabsContent value="json">
          <JsonViewer data={item} defaultOpen />
        </TabsContent>
      </Tabs>
  );
}
