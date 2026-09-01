import { useState } from "react";
import { useCollections } from "@/lib/query/collections";
import { AppShell } from "@/components/layout/AppShell";
import { CollectionCard } from "@stac-higher/shared";
import { LoadingState } from "@stac-higher/shared";
import { EmptyState } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";
import { Input } from "@stac-higher/shared";
import { Layers, Search } from "lucide-react";
import { browseCollectionPath } from "@/lib/browse/paths";
import { BrowseFrame } from "./BrowseFrame";
import { useBrowseCatalog } from "./useBrowseCatalog";

function BrowseCollectionsInner({ catalogId }: { catalogId: string }) {
  const catalog = useBrowseCatalog(catalogId);
  const endpointUrl = catalog?.url ?? "";
  const { data, isLoading, error, refetch } = useCollections(endpointUrl);
  const [search, setSearch] = useState("");

  const collections = data?.collections ?? [];
  const q = search.toLowerCase();
  const filtered = search
    ? collections.filter(
        (c) =>
          c.id.toLowerCase().includes(q) ||
          (c.title ?? "").toLowerCase().includes(q) ||
          c.description.toLowerCase().includes(q),
      )
    : collections;

  return (
    <BrowseFrame
      catalog={catalog}
      breadcrumb={
        <>
          <a href="/catalogs" className="hover:text-foreground transition-colors">
            Catalogs
          </a>
          <span>/</span>
          <span className="text-foreground">{catalog?.name}</span>
        </>
      }
    >
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">Collections</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {collections.length} collection{collections.length !== 1 ? "s" : ""} in
          this catalog
        </p>
      </div>

      {collections.length > 0 && (
        <div className="relative mb-6">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search collections..."
            className="pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      )}

      {isLoading ? (
        <LoadingState />
      ) : error ? (
        <ErrorState
          message={
            error instanceof Error ? error.message : "Failed to load collections"
          }
          onRetry={() => refetch()}
        />
      ) : filtered.length === 0 && search ? (
        <EmptyState
          icon={Search}
          title="No results"
          description={`No collections match "${search}"`}
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="No collections"
          description="This catalog does not expose any collections."
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((collection) => (
            <CollectionCard
              key={collection.id}
              collection={collection}
              href={browseCollectionPath(catalogId, collection.id)}
            />
          ))}
        </div>
      )}
    </BrowseFrame>
  );
}

export function BrowseCollectionsPage({ catalogId }: { catalogId: string }) {
  return (
    <AppShell title="Browse catalog">
      <BrowseCollectionsInner catalogId={catalogId} />
    </AppShell>
  );
}
