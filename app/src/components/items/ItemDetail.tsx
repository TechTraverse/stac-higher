import { useState } from "react";
import { useStore } from "@nanostores/react";
import { $builtInCatalog } from "@/stores/catalogStore";
import { useItem, useDeleteItem } from "@/lib/query/items";
import { AppShell } from "@/components/layout/AppShell";
import { ErrorState } from "@stac-higher/shared";
import { Skeleton } from "@stac-higher/shared";
import { Button } from "@stac-higher/shared";
import { Badge } from "@stac-higher/shared";
import { ItemDetailView } from "./ItemDetailView";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useCollectionSettings } from "@/lib/collections/settings-client";
import { useItemTileJson } from "@/lib/serving/queries";
import { pickPreviewAsset } from "@/lib/serving/preview";
import { itemViewerUrl } from "@/lib/serving/urls";

interface ItemDetailInnerProps {
  collectionId: string;
  itemId: string;
}

function ItemDetailInner({ collectionId, itemId }: ItemDetailInnerProps) {
  const catalog = useStore($builtInCatalog);
  const endpointUrl = catalog?.url ?? "";
  const { data: item, isLoading, error, refetch } = useItem(endpointUrl, collectionId, itemId);
  const deleteMutation = useDeleteItem(endpointUrl, collectionId);
  const [deleteOpen, setDeleteOpen] = useState(false);

  // Raster preview (G-5). Hooks run unconditionally — above the loading and
  // error returns — and the tile server is asked only when the collection
  // advertises serving AND the item carries something a tiler can open. A
  // failure here is silent by design: no tile server, no layer.
  const { data: settings } = useCollectionSettings(collectionId);
  const previewAsset = item ? pickPreviewAsset(item) : null;
  const { data: tileJson } = useItemTileJson(
    collectionId,
    itemId,
    previewAsset,
    settings?.servingEnabled === true,
  );
  const rasterPreview =
    tileJson && previewAsset
      ? {
          tiles: tileJson.tiles,
          bounds: tileJson.bounds,
          viewerUrl: itemViewerUrl(collectionId, itemId, previewAsset),
        }
      : undefined;

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
        <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
          <ErrorState
            message={error instanceof Error ? error.message : "Item not found"}
            onRetry={() => refetch()}
          />
        </main>
      </>
    );
  }

  return (
    <>
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        <div className="flex items-center gap-2 mb-4 text-sm text-muted-foreground">
          <a href="/collections" className="hover:text-foreground transition-colors">
            Products
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

        <ItemDetailView item={item} rasterPreview={rasterPreview} />

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
    <AppShell>
      <ItemDetailInner collectionId={collectionId} itemId={itemId} />
    </AppShell>
  );
}
