import { useState } from "react";
import { useStore } from "@nanostores/react";
import { $builtInCatalog } from "@/stores/catalogStore";
import { useCollections } from "@/lib/query/collections";
import { AppShell } from "@/components/layout/AppShell";
import { CollectionCard } from "@stac-higher/shared";
import { LoadingState } from "@stac-higher/shared";
import { EmptyState } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";
import { Button } from "@stac-higher/shared";
import { Input } from "@stac-higher/shared";
import { Layers, Plus, Search } from "lucide-react";

function CollectionListInner() {
  const catalog = useStore($builtInCatalog);
  const endpointUrl = catalog?.url ?? "";
  const { data, isLoading, error, refetch } = useCollections(endpointUrl);
  const [search, setSearch] = useState("");

  if (!catalog) {
    return (
      <>
        <main className="flex-1 p-6">
          <EmptyState
            icon={Layers}
            title="Platform catalog unavailable"
            description="The built-in catalog entry is missing. Reload the page, or check PUBLIC_BUILTIN_CATALOG_URL."
            action={{ label: "Manage catalogs", href: "/catalogs" }}
          />
        </main>
      </>
    );
  }

  const collections = data?.collections ?? [];
  const filtered = search
    ? collections.filter(
        (c) =>
          c.id.toLowerCase().includes(search.toLowerCase()) ||
          (c.title ?? "").toLowerCase().includes(search.toLowerCase()) ||
          c.description.toLowerCase().includes(search.toLowerCase()),
      )
    : collections;

  return (
    <>
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Products</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {collections.length} product{collections.length !== 1 ? "s" : ""} on
              this platform
            </p>
          </div>
          <a href="/collections/new">
            <Button>
              <Plus className="h-4 w-4 mr-1.5" />
              Create product
            </Button>
          </a>
        </div>

        {collections.length > 0 && (
          <div className="relative mb-6">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search products..."
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
            message={error instanceof Error ? error.message : "Failed to load collections"}
            onRetry={() => refetch()}
          />
        ) : filtered.length === 0 && search ? (
          <EmptyState
            icon={Search}
            title="No results"
            description={`No products match "${search}"`}
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={Layers}
            title="No products yet"
            description="Create your first product to start wiring sources, processes and destinations."
            action={{ label: "Create product", href: "/collections/new" }}
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filtered.map((collection) => (
              <CollectionCard key={collection.id} collection={collection} />
            ))}
          </div>
        )}
      </main>
    </>
  );
}

export function CollectionListPage() {
  return (
    <AppShell>
      <CollectionListInner />
    </AppShell>
  );
}
