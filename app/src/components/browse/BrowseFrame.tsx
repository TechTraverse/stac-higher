import type { ReactNode } from "react";
import type { StacCatalog } from "@/stores/catalogStore";
import { EmptyState } from "@stac-higher/shared";
import { LoadingState } from "@stac-higher/shared";
import { Badge } from "@stac-higher/shared";
import { Globe } from "lucide-react";

/**
 * Shell for every catalog-browser page: resolves the route's catalog, and
 * renders the "this is someone else's catalog" framing that keeps the browser
 * visually distinct from the product surface. Read-only by construction —
 * nothing here mints a create/edit/delete affordance.
 */
export function BrowseFrame({
  catalog,
  breadcrumb,
  children,
}: {
  catalog: StacCatalog | null | undefined;
  breadcrumb: ReactNode;
  children: ReactNode;
}) {
  if (catalog === undefined) {
    return (
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        <LoadingState />
      </main>
    );
  }

  if (catalog === null) {
    return (
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        <EmptyState
          icon={Globe}
          title="Catalog not found"
          description="This catalog is no longer configured in this browser."
          action={{ label: "Manage catalogs", href: "/catalogs" }}
        />
      </main>
    );
  }

  return (
    <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
      <div className="flex items-center gap-2 mb-4 text-sm text-muted-foreground">
        {breadcrumb}
      </div>
      <div className="flex items-center gap-2 mb-6">
        <Globe className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-medium">{catalog.name}</span>
        <span className="text-xs tech text-muted-foreground">{catalog.url}</span>
        <Badge variant="outline" className="text-xs">
          Read-only
        </Badge>
      </div>
      {children}
    </main>
  );
}
