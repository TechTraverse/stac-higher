import { useCollection } from "@/lib/query/collections";
import { useItems } from "@/lib/query/items";
import { AppShell } from "@/components/layout/AppShell";
import { CollectionMetadata } from "@/components/collections/CollectionMetadata";
import { ItemCard } from "@stac-higher/shared";
import { JsonViewer } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";
import { Skeleton } from "@stac-higher/shared";
import { Button } from "@stac-higher/shared";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  browseCollectionsPath,
  browseItemPath,
  browseItemsPath,
  browseTarget,
} from "@/lib/browse/paths";
import { BrowseFrame } from "./BrowseFrame";
import type { BrowseRouteProps } from "./types";
import { useBrowseCatalog } from "./useBrowseCatalog";

function BrowseCollectionInner({
  catalogId,
  collectionId,
  src,
}: BrowseRouteProps & { collectionId: string }) {
  const catalog = useBrowseCatalog(catalogId, src);
  // Links are props: they are built before BrowseFrame can early-return.
  const target = browseTarget(catalog, catalogId, src);
  const endpointUrl = catalog?.url ?? "";
  const {
    data: collection,
    isLoading,
    error,
    refetch,
  } = useCollection(endpointUrl, collectionId);
  const { data: itemsData } = useItems(endpointUrl, collectionId, { limit: 10 });
  const items = itemsData?.features ?? [];

  const breadcrumb = (
    <>
      <a href="/catalogs" className="hover:text-foreground transition-colors">
        Catalogs
      </a>
      <span>/</span>
      <a
        href={browseCollectionsPath(target)}
        className="hover:text-foreground transition-colors"
      >
        {catalog?.name}
      </a>
      <span>/</span>
      <span className="text-foreground">{collectionId}</span>
    </>
  );

  return (
    <BrowseFrame catalog={catalog} src={src} breadcrumb={breadcrumb}>
      {isLoading ? (
        <div className="space-y-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-10 w-72" />
          <Skeleton className="h-32 w-full rounded-lg" />
        </div>
      ) : error || !collection ? (
        <ErrorState
          message={
            error instanceof Error ? error.message : "Collection not found"
          }
          onRetry={() => refetch()}
        />
      ) : (
        <>
          <div className="mb-6">
            <h1 className="text-2xl font-bold">
              {collection.title || collection.id}
            </h1>
            {collection.title && (
              <p className="text-sm text-muted-foreground tech mt-0.5">
                {collection.id}
              </p>
            )}
          </div>

          <Tabs defaultValue="overview" className="space-y-4">
            <TabsList>
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="items">
                Items{" "}
                {itemsData?.context?.matched !== undefined &&
                  `(${itemsData.context.matched})`}
              </TabsTrigger>
              <TabsTrigger value="json">Raw JSON</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="space-y-4">
              <CollectionMetadata collection={collection} />
            </TabsContent>

            <TabsContent value="items">
              {items.length > 0 ? (
                <>
                  <div className="grid gap-3">
                    {items.map((item) => (
                      <ItemCard
                        key={item.id}
                        item={item}
                        collectionId={collectionId}
                        href={browseItemPath(target, collectionId, item.id)}
                      />
                    ))}
                  </div>
                  <div className="mt-4 text-center">
                    <a href={browseItemsPath(target, collectionId)}>
                      <Button variant="outline">View all items</Button>
                    </a>
                  </div>
                </>
              ) : (
                <p className="text-sm text-muted-foreground py-8 text-center">
                  No items in this collection.
                </p>
              )}
            </TabsContent>

            <TabsContent value="json">
              <JsonViewer data={collection} defaultOpen />
            </TabsContent>
          </Tabs>
        </>
      )}
    </BrowseFrame>
  );
}

export function BrowseCollectionPage(
  props: BrowseRouteProps & { collectionId: string },
) {
  return (
    <AppShell title="Browse catalog">
      <BrowseCollectionInner {...props} />
    </AppShell>
  );
}
