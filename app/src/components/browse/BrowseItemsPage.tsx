import { useState } from "react";
import { useItems } from "@/lib/query/items";
import { AppShell } from "@/components/layout/AppShell";
import { ItemCard } from "@stac-higher/shared";
import { StacMap } from "@stac-higher/shared";
import { FootprintLayer } from "@stac-higher/shared";
import { LoadingState } from "@stac-higher/shared";
import { EmptyState } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";
import { Button } from "@stac-higher/shared";
import { Package, ChevronLeft, ChevronRight } from "lucide-react";
import { extractToken, useTokenPaging } from "@/hooks/use-token-paging";
import {
  browseCollectionPath,
  browseCollectionsPath,
  browseItemPath,
  browseTarget,
} from "@/lib/browse/paths";
import { BrowseFrame } from "./BrowseFrame";
import type { BrowseRouteProps } from "./types";
import { useBrowseCatalog } from "./useBrowseCatalog";

const PAGE_SIZE = 20;

function BrowseItemsInner({
  catalogId,
  collectionId,
  src,
}: BrowseRouteProps & { collectionId: string }) {
  const catalog = useBrowseCatalog(catalogId, src);
  // Links are props: they are built before BrowseFrame can early-return.
  const target = browseTarget(catalog, catalogId, src);
  const endpointUrl = catalog?.url ?? "";
  const paging = useTokenPaging();
  const { data, isLoading, error, refetch } = useItems(
    endpointUrl,
    collectionId,
    { limit: PAGE_SIZE, token: paging.token },
  );
  const [hoveredItemId, setHoveredItemId] = useState<string | undefined>();

  const items = data?.features ?? [];
  const matchCount = data?.context?.matched ?? data?.numberMatched;
  const nextToken = extractToken(data?.links, "next");

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
      <a
        href={browseCollectionPath(target, collectionId)}
        className="hover:text-foreground transition-colors"
      >
        {collectionId}
      </a>
      <span>/</span>
      <span className="text-foreground">Items</span>
    </>
  );

  return (
    <BrowseFrame catalog={catalog} src={src} breadcrumb={breadcrumb}>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Items</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {matchCount !== undefined
            ? `${matchCount} total items`
            : `${items.length} items loaded`}{" "}
          in <span className="tech">{collectionId}</span>
        </p>
      </div>

      {isLoading ? (
        <LoadingState />
      ) : error ? (
        <ErrorState
          message={error instanceof Error ? error.message : "Failed to load items"}
          onRetry={() => refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Package}
          title="No items"
          description="This collection has no items."
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="h-[500px] rounded-lg overflow-hidden border border-border lg:sticky lg:top-20">
            <StacMap>
              <FootprintLayer items={items} selectedId={hoveredItemId} />
            </StacMap>
          </div>
          <div className="space-y-3">
            <div className="grid gap-3">
              {items.map((item) => (
                <div
                  key={item.id}
                  onMouseEnter={() => setHoveredItemId(item.id)}
                  onMouseLeave={() => setHoveredItemId(undefined)}
                >
                  <ItemCard
                    item={item}
                    collectionId={collectionId}
                    href={browseItemPath(target, collectionId, item.id)}
                  />
                </div>
              ))}
            </div>
            {(paging.hasPrev || nextToken) && (
              <div className="flex items-center justify-between pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!paging.hasPrev}
                  onClick={paging.goPrev}
                >
                  <ChevronLeft className="h-4 w-4 mr-1" />
                  Previous
                </Button>
                <span className="text-xs text-muted-foreground">
                  Page {paging.page}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!nextToken}
                  onClick={() => paging.goNext(nextToken)}
                >
                  Next
                  <ChevronRight className="h-4 w-4 ml-1" />
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </BrowseFrame>
  );
}

export function BrowseItemsPage(
  props: BrowseRouteProps & { collectionId: string },
) {
  return (
    <AppShell title="Browse catalog">
      <BrowseItemsInner {...props} />
    </AppShell>
  );
}
