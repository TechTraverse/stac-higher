import { useState } from "react";
import { useStore } from "@nanostores/react";
import { $activeEndpoint } from "@/stores/endpointStore";
import { useCollection, useDeleteCollection } from "@/lib/query/collections";
import { useItems } from "@/lib/query/items";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { JsonViewer } from "@/components/shared/JsonViewer";
import { ErrorState } from "@/components/shared/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { ItemCard } from "@/components/items/ItemCard";
import { AssetManager } from "@/components/assets/AssetManager";
import { StacMap } from "@/components/map/StacMap";
import { ExtentLayer } from "@/components/map/ExtentLayer";
import { bboxToLngLatBounds } from "@/lib/map/bbox";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
  Plus,
  MapPin,
  Calendar,
  ArrowLeft,
  ExternalLink,
} from "lucide-react";
import { toast } from "sonner";

function formatBbox(bbox: number[]): string {
  if (bbox.length < 4) return "N/A";
  return `[${bbox.map((n) => n.toFixed(4)).join(", ")}]`;
}

interface CollectionDetailInnerProps {
  collectionId: string;
}

function CollectionDetailInner({ collectionId }: CollectionDetailInnerProps) {
  const endpoint = useStore($activeEndpoint);
  const endpointUrl = endpoint?.url ?? "";
  const { data: collection, isLoading, error, refetch } = useCollection(endpointUrl, collectionId);
  const { data: itemsData } = useItems(endpointUrl, collectionId, { limit: 10 });
  const deleteMutation = useDeleteCollection(endpointUrl);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const handleDelete = () => {
    deleteMutation.mutate(collectionId, {
      onSuccess: () => {
        toast.success("Collection deleted");
        window.location.href = "/collections";
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
          <Skeleton className="h-4 w-48" />
          <div className="flex items-start justify-between">
            <div className="space-y-2">
              <Skeleton className="h-8 w-64" />
              <Skeleton className="h-4 w-32" />
            </div>
            <div className="flex gap-2">
              <Skeleton className="h-9 w-20" />
              <Skeleton className="h-9 w-24" />
            </div>
          </div>
          <Skeleton className="h-10 w-72" />
          <Skeleton className="h-32 w-full rounded-lg" />
          <div className="grid gap-4 md:grid-cols-2">
            <Skeleton className="h-[300px] rounded-lg" />
            <Skeleton className="h-40 rounded-lg" />
          </div>
        </main>
      </>
    );
  }

  if (error || !collection) {
    return (
      <>
        <Header />
        <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
          <ErrorState
            message={error instanceof Error ? error.message : "Collection not found"}
            onRetry={() => refetch()}
          />
        </main>
      </>
    );
  }

  const bbox = collection.extent?.spatial?.bbox?.[0];
  const temporal = collection.extent?.temporal?.interval?.[0];
  const items = itemsData?.features ?? [];

  return (
    <>
      <Header />
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        <div className="flex items-center gap-2 mb-4 text-sm text-muted-foreground">
          <a href="/collections" className="hover:text-foreground transition-colors inline-flex items-center gap-1">
            <ArrowLeft className="h-3.5 w-3.5" />
            Collections
          </a>
          <span>/</span>
          <span className="text-foreground">{collection.title || collection.id}</span>
        </div>

        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">{collection.title || collection.id}</h1>
            {collection.title && (
              <p className="text-sm text-muted-foreground font-mono mt-0.5">
                {collection.id}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <a href={`/collections/${encodeURIComponent(collectionId)}/edit`}>
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

        <Tabs defaultValue="overview" className="space-y-4">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="items">
              Items {itemsData?.context?.matched !== undefined && `(${itemsData.context.matched})`}
            </TabsTrigger>
            <TabsTrigger value="assets">
              Assets {collection.assets ? `(${Object.keys(collection.assets).length})` : ""}
            </TabsTrigger>
            <TabsTrigger value="json">Raw JSON</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="space-y-4">
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
                    <p className="text-sm text-muted-foreground">No spatial extent defined</p>
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
                    <p className="text-sm text-muted-foreground">No temporal extent defined</p>
                  )}
                </CardContent>
              </Card>
            </div>

            <div className="flex flex-wrap gap-2">
              <Badge variant="secondary">{collection.license}</Badge>
              <Badge variant="outline">STAC {collection.stac_version}</Badge>
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
                          <p className="text-xs text-muted-foreground">{provider.description}</p>
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

          </TabsContent>

          <TabsContent value="items">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Items</h2>
              <a href={`/collections/${encodeURIComponent(collectionId)}/items/new`}>
                <Button size="sm">
                  <Plus className="h-4 w-4 mr-1.5" />
                  Create Item
                </Button>
              </a>
            </div>
            {items.length > 0 ? (
              <div className="grid gap-3">
                {items.map((item) => (
                  <ItemCard key={item.id} item={item} collectionId={collectionId} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground py-8 text-center">
                No items in this collection yet.
              </p>
            )}
            {items.length > 0 && (
              <div className="mt-4 text-center">
                <a href={`/collections/${encodeURIComponent(collectionId)}/items`}>
                  <Button variant="outline">View All Items</Button>
                </a>
              </div>
            )}
          </TabsContent>

          <TabsContent value="assets">
            <AssetManager collection={collection} endpointUrl={endpointUrl} />
          </TabsContent>

          <TabsContent value="json">
            <JsonViewer data={collection} defaultOpen />
          </TabsContent>
        </Tabs>

        <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete Collection</DialogTitle>
              <DialogDescription>
                Are you sure you want to delete "{collection.title || collection.id}"?
                This will also remove all items in this collection. This action cannot be
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
                {deleteMutation.isPending ? "Deleting..." : "Delete Collection"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </main>
    </>
  );
}

export function CollectionDetailPage({ collectionId }: { collectionId: string }) {
  return (
    <QueryProvider>
      <CollectionDetailInner collectionId={collectionId} />
    </QueryProvider>
  );
}
