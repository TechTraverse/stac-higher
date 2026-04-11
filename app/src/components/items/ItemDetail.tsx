import { useState } from "react";
import { useStore } from "@nanostores/react";
import { $activeEndpoint } from "@/stores/endpointStore";
import { useItem, useDeleteItem } from "@/lib/query/items";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { JsonViewer } from "@/components/shared/JsonViewer";
import { ErrorState } from "@/components/shared/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { StacMap } from "@/components/map/StacMap";
import { Source, Layer } from "react-map-gl/maplibre";
import { bboxToLngLatBounds } from "@/lib/map/bbox";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Pencil,
  Trash2,
  ExternalLink,
  Download,
} from "lucide-react";
import { toast } from "sonner";

interface ItemDetailInnerProps {
  collectionId: string;
  itemId: string;
}

function ItemDetailInner({ collectionId, itemId }: ItemDetailInnerProps) {
  const endpoint = useStore($activeEndpoint);
  const endpointUrl = endpoint?.url ?? "";
  const { data: item, isLoading, error, refetch } = useItem(endpointUrl, collectionId, itemId);
  const deleteMutation = useDeleteItem(endpointUrl, collectionId);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const handleDelete = () => {
    deleteMutation.mutate(itemId, {
      onSuccess: () => {
        toast.success("Item deleted");
        window.location.href = `/collections/${encodeURIComponent(collectionId)}/items`;
      },
      onError: (err) => {
        toast.error(`Delete failed: ${err.message}`);
      },
    });
  };

  if (isLoading) {
    return (
      <>
        <Header />
        <main className="flex-1 p-6 max-w-6xl mx-auto w-full space-y-6">
          <Skeleton className="h-4 w-64" />
          <div className="flex items-start justify-between">
            <div className="space-y-2">
              <Skeleton className="h-8 w-48" />
              <div className="flex gap-2">
                <Skeleton className="h-5 w-20 rounded-full" />
                <Skeleton className="h-5 w-24 rounded-full" />
              </div>
            </div>
            <div className="flex gap-2">
              <Skeleton className="h-9 w-20" />
              <Skeleton className="h-9 w-24" />
            </div>
          </div>
          <Skeleton className="h-10 w-80" />
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex gap-4">
                <Skeleton className="h-5 w-40" />
                <Skeleton className="h-5 flex-1" />
              </div>
            ))}
          </div>
        </main>
      </>
    );
  }

  if (error || !item) {
    return (
      <>
        <Header />
        <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
          <ErrorState
            message={error instanceof Error ? error.message : "Item not found"}
            onRetry={() => refetch()}
          />
        </main>
      </>
    );
  }

  const properties = Object.entries(item.properties).filter(
    ([key]) => !key.startsWith("_"),
  );

  return (
    <>
      <Header />
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        <div className="flex items-center gap-2 mb-4 text-sm text-muted-foreground">
          <a href="/collections" className="hover:text-foreground transition-colors">
            Collections
          </a>
          <span>/</span>
          <a
            href={`/collections/${encodeURIComponent(collectionId)}`}
            className="hover:text-foreground transition-colors"
          >
            {collectionId}
          </a>
          <span>/</span>
          <a
            href={`/collections/${encodeURIComponent(collectionId)}/items`}
            className="hover:text-foreground transition-colors"
          >
            Items
          </a>
          <span>/</span>
          <span className="text-foreground">{item.id}</span>
        </div>

        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">{item.id}</h1>
            <div className="flex items-center gap-2 mt-1">
              <Badge variant="outline">{item.geometry?.type ?? "No geometry"}</Badge>
              <Badge variant="secondary">STAC {item.stac_version}</Badge>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={`/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}/edit`}
            >
              <Button variant="outline" size="sm">
                <Pencil className="h-3.5 w-3.5 mr-1.5" />
                Edit
              </Button>
            </a>
            <Button variant="outline" size="sm" onClick={() => setDeleteOpen(true)}>
              <Trash2 className="h-3.5 w-3.5 mr-1.5 text-destructive" />
              Delete
            </Button>
          </div>
        </div>

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
                </StacMap>
              </div>
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

        <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete Item</DialogTitle>
              <DialogDescription>
                Are you sure you want to delete item "{item.id}"? This action cannot be
                undone.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
              >
                {deleteMutation.isPending ? "Deleting..." : "Delete Item"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </main>
    </>
  );
}

export function ItemDetailPage({
  collectionId,
  itemId,
}: {
  collectionId: string;
  itemId: string;
}) {
  return (
    <QueryProvider>
      <ItemDetailInner collectionId={collectionId} itemId={itemId} />
    </QueryProvider>
  );
}
