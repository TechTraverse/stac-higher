import { useItem } from "@/lib/query/items";
import { AppShell } from "@/components/layout/AppShell";
import { ItemDetailView } from "@/components/items/ItemDetailView";
import { ErrorState } from "@stac-higher/shared";
import { Skeleton } from "@stac-higher/shared";
import { Badge } from "@stac-higher/shared";
import {
  browseCollectionPath,
  browseCollectionsPath,
  browseItemsPath,
} from "@/lib/browse/paths";
import { BrowseFrame } from "./BrowseFrame";
import { useBrowseCatalog } from "./useBrowseCatalog";

function BrowseItemInner({
  catalogId,
  collectionId,
  itemId,
}: {
  catalogId: string;
  collectionId: string;
  itemId: string;
}) {
  const catalog = useBrowseCatalog(catalogId);
  const endpointUrl = catalog?.url ?? "";
  const { data: item, isLoading, error, refetch } = useItem(
    endpointUrl,
    collectionId,
    itemId,
  );

  const breadcrumb = (
    <>
      <a href="/catalogs" className="hover:text-foreground transition-colors">
        Catalogs
      </a>
      <span>/</span>
      <a
        href={browseCollectionsPath(catalogId)}
        className="hover:text-foreground transition-colors"
      >
        {catalog?.name}
      </a>
      <span>/</span>
      <a
        href={browseCollectionPath(catalogId, collectionId)}
        className="hover:text-foreground transition-colors"
      >
        {collectionId}
      </a>
      <span>/</span>
      <a
        href={browseItemsPath(catalogId, collectionId)}
        className="hover:text-foreground transition-colors"
      >
        Items
      </a>
      <span>/</span>
      <span className="text-foreground">{itemId}</span>
    </>
  );

  return (
    <BrowseFrame catalog={catalog} breadcrumb={breadcrumb}>
      {isLoading ? (
        <div className="space-y-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-10 w-80" />
          <Skeleton className="h-32 w-full rounded-lg" />
        </div>
      ) : error || !item ? (
        <ErrorState
          message={error instanceof Error ? error.message : "Item not found"}
          onRetry={() => refetch()}
        />
      ) : (
        <>
          <div className="mb-6">
            <h1 className="text-2xl font-bold">{item.id}</h1>
            <div className="flex items-center gap-2 mt-1">
              <Badge variant="outline">
                {item.geometry?.type ?? "No geometry"}
              </Badge>
              <Badge variant="secondary">STAC {item.stac_version}</Badge>
            </div>
          </div>
          <ItemDetailView item={item} />
        </>
      )}
    </BrowseFrame>
  );
}

export function BrowseItemPage(props: {
  catalogId: string;
  collectionId: string;
  itemId: string;
}) {
  return (
    <AppShell title="Browse catalog">
      <BrowseItemInner {...props} />
    </AppShell>
  );
}
