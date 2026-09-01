import { useState, type ReactNode } from "react";
import { addCatalog, type StacCatalog } from "@/stores/catalogStore";
import { CatalogForm } from "@/components/catalogs/CatalogForm";
import { EmptyState } from "@stac-higher/shared";
import { LoadingState } from "@stac-higher/shared";
import { Badge } from "@stac-higher/shared";
import { Button } from "@stac-higher/shared";
import { Globe, Link2 } from "lucide-react";
import { parseSrc } from "@/lib/browse/paths";
import { toast } from "sonner";

/** Host only — what the user needs to judge whether to trust a shared link. */
function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/**
 * Offered when a shared browse link names a catalog this browser does not
 * have. NOTHING is fetched until the user accepts: `src` came from whoever
 * wrote the link, so adding it — and therefore requesting it — is an explicit
 * choice, made in the same dialog as any other catalog, with the URL shown.
 */
function UnknownCatalogPrompt({ src }: { src: string }) {
  const [formOpen, setFormOpen] = useState(false);

  return (
    <>
      <EmptyState
        icon={Link2}
        title="This link points at a catalog you haven't added"
        description={`${hostOf(src)} isn't in your catalog list. Add it to browse this link, or open the Catalogs page to manage the list.`}
        action={{ label: "Manage catalogs", href: "/catalogs" }}
      />
      <div className="mt-4 flex flex-col items-center gap-2">
        <p className="text-xs tech text-muted-foreground break-all">{src}</p>
        <Button onClick={() => setFormOpen(true)}>
          <Globe className="h-4 w-4 mr-1.5" />
          Add this catalog
        </Button>
      </div>
      {formOpen && (
        <CatalogForm
          open={formOpen}
          onOpenChange={setFormOpen}
          defaults={{ name: hostOf(src), url: src }}
          onSubmit={(data) => {
            addCatalog({ ...data, isDefault: false });
            toast.success(`Added catalog: ${data.name}`);
          }}
        />
      )}
    </>
  );
}

/**
 * Shell for every catalog-browser page: renders the "this is someone else's
 * catalog" framing, and handles the two ways a route can fail to resolve —
 * still hydrating, or a catalog this browser does not have. Read-only by
 * construction; nothing here mints a create/edit/delete affordance.
 */
export function BrowseFrame({
  catalog,
  src,
  breadcrumb,
  children,
}: {
  catalog: StacCatalog | null | undefined;
  /** Raw `?src=` from the route, if the link carried one. */
  src?: string | null;
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
    const shared = parseSrc(src);
    return (
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        {shared ? (
          <UnknownCatalogPrompt src={shared} />
        ) : (
          <EmptyState
            icon={Globe}
            title="Catalog not found"
            description="This catalog is not configured in this browser, and the link carries no catalog URL to offer."
            action={{ label: "Manage catalogs", href: "/catalogs" }}
          />
        )}
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
