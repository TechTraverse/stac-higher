import { useState } from "react";
import { useStore } from "@nanostores/react";
import { $builtInCatalog } from "@/stores/catalogStore";
import { useCollection, useDeleteCollection } from "@/lib/query/collections";
import { useItems } from "@/lib/query/items";
import { AppShell } from "@/components/layout/AppShell";
import { JsonViewer } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";
import { Skeleton } from "@stac-higher/shared";
import { ItemCard } from "@stac-higher/shared";
import { AssetManager } from "@/components/assets/AssetManager";
import { DataFlowTab } from "./DataFlowTab";
import { CollectionMetadata } from "./CollectionMetadata";
import { ProductOverview } from "./ProductOverview";
import { SettingsTab } from "./SettingsTab";
import { Button } from "@stac-higher/shared";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@stac-higher/shared";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Pencil, Trash2, Plus, ArrowLeft } from "lucide-react";
import { toast } from "sonner";

interface CollectionDetailInnerProps {
  collectionId: string;
}

function CollectionDetailInner({ collectionId }: CollectionDetailInnerProps) {
  const catalog = useStore($builtInCatalog);
  const endpointUrl = catalog?.url ?? "";
  const { data: collection, isLoading, error, refetch } = useCollection(endpointUrl, collectionId);
  const { data: itemsData } = useItems(endpointUrl, collectionId, { limit: 10 });
  const deleteMutation = useDeleteCollection(endpointUrl);
  const [deleteOpen, setDeleteOpen] = useState(false);
  // Controlled so the product Overview panel can hand off to Data flow.
  const [tab, setTab] = useState("overview");

  const handleDelete = () => {
    deleteMutation.mutate(collectionId, {
      onSuccess: () => {
        toast.success("Product deleted");
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
        <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
          <ErrorState
            message={error instanceof Error ? error.message : "Product not found"}
            onRetry={() => refetch()}
          />
        </main>
      </>
    );
  }

  const items = itemsData?.features ?? [];

  return (
    <>
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        <div className="flex items-center gap-2 mb-4 text-sm text-muted-foreground">
          <a href="/collections" className="hover:text-foreground transition-colors inline-flex items-center gap-1">
            <ArrowLeft className="h-3.5 w-3.5" />
            Products
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

        <Tabs value={tab} onValueChange={setTab} className="space-y-4">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="items">
              Items {itemsData?.context?.matched !== undefined && `(${itemsData.context.matched})`}
            </TabsTrigger>
            <TabsTrigger value="assets">
              Assets {collection.assets ? `(${Object.keys(collection.assets).length})` : ""}
            </TabsTrigger>
            <TabsTrigger value="dataflow">Data flow</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
            <TabsTrigger value="json">Raw JSON</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="space-y-4">
            {/* Platform view first, then the raw STAC metadata below it. This
                page is built-in-only since UI-10 — external catalogs are
                browsed read-only at /catalogs/[catalogId]/collections. */}
            <ProductOverview
              collection={collection}
              collectionId={collectionId}
              endpointUrl={endpointUrl}
              items={items}
              onOpenDataFlow={() => setTab("dataflow")}
            />

            <CollectionMetadata collection={collection} />

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

          <TabsContent value="dataflow">
            <DataFlowTab collectionId={collectionId} />
          </TabsContent>

          <TabsContent value="settings">
            <SettingsTab collectionId={collectionId} />
          </TabsContent>

          <TabsContent value="json">
            <JsonViewer data={collection} defaultOpen />
          </TabsContent>
        </Tabs>

        <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete product</DialogTitle>
              <DialogDescription>
                Are you sure you want to delete "{collection.title || collection.id}"?
                This will also remove all items in this product. This action cannot be
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
                {deleteMutation.isPending ? "Deleting..." : "Delete product"}
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
    <AppShell>
      <CollectionDetailInner collectionId={collectionId} />
    </AppShell>
  );
}
