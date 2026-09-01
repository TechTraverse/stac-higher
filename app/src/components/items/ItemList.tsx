import { useState, useCallback } from "react";
import { extractToken, useTokenPaging } from "@/hooks/use-token-paging";
import { useStore } from "@nanostores/react";
import { useQueryClient } from "@tanstack/react-query";
import { $builtInCatalog } from "@/stores/catalogStore";
import { useItems } from "@/lib/query/items";
import { stacKeys } from "@/lib/query/keys";
import { createItem } from "@/lib/stac-api/items";
import type { StacItem } from "@/lib/stac-api/types";
import { AppShell } from "@/components/layout/AppShell";
import { ItemCard } from "@stac-higher/shared";
import { StacMap } from "@stac-higher/shared";
import { FootprintLayer } from "@stac-higher/shared";
import { LoadingState } from "@stac-higher/shared";
import { EmptyState } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";
import { Button } from "@stac-higher/shared";
import { Textarea } from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { ArrowLeft, Plus, Package, ChevronLeft, ChevronRight, Upload } from "lucide-react";
import { toast } from "sonner";

interface ItemListInnerProps {
  collectionId: string;
}

const PAGE_SIZE = 20;

function ItemListInner({ collectionId }: ItemListInnerProps) {
  const catalog = useStore($builtInCatalog);
  const endpointUrl = catalog?.url ?? "";
  const qc = useQueryClient();
  const paging = useTokenPaging();
  const { data, isLoading, error, refetch } = useItems(endpointUrl, collectionId, { limit: PAGE_SIZE, token: paging.token });
  const [hoveredItemId, setHoveredItemId] = useState<string | undefined>();

  const [importOpen, setImportOpen] = useState(false);
  const [importJson, setImportJson] = useState("");
  const [importing, setImporting] = useState(false);
  const [importProgress, setImportProgress] = useState({ done: 0, total: 0, errors: 0 });

  const handleBulkImport = useCallback(async () => {
    let parsed: StacItem[];
    try {
      const raw = JSON.parse(importJson);
      parsed = Array.isArray(raw) ? raw : [raw];
    } catch {
      toast.error("Invalid JSON — must be a STAC Item or array of Items");
      return;
    }

    setImporting(true);
    setImportProgress({ done: 0, total: parsed.length, errors: 0 });

    let errors = 0;
    for (let i = 0; i < parsed.length; i++) {
      try {
        await createItem(collectionId, parsed[i], endpointUrl);
      } catch {
        errors++;
      }
      setImportProgress({ done: i + 1, total: parsed.length, errors });
    }

    setImporting(false);
    qc.invalidateQueries({ queryKey: stacKeys.items(endpointUrl, collectionId) });

    if (errors === 0) {
      toast.success(`Imported ${parsed.length} item${parsed.length !== 1 ? "s" : ""}`);
      setImportOpen(false);
      setImportJson("");
    } else {
      toast.error(`Imported ${parsed.length - errors} of ${parsed.length} — ${errors} failed`);
    }
  }, [importJson, collectionId, endpointUrl, qc]);

  const items = data?.features ?? [];
  const matchCount = data?.context?.matched ?? data?.numberMatched;
  const nextToken = extractToken(data?.links, "next");

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
          <span className="text-foreground">Items</span>
        </div>

        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">Items</h1>
            <p className="text-sm text-muted-foreground mt-1">
              {matchCount !== undefined
                ? `${matchCount} total items`
                : `${items.length} items loaded`}
              {" "}in {collectionId}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <a href={`/collections/${encodeURIComponent(collectionId)}`}>
              <Button variant="outline" size="sm">
                <ArrowLeft className="h-3.5 w-3.5 mr-1.5" />
                Back
              </Button>
            </a>
            <Button variant="outline" size="sm" onClick={() => setImportOpen(true)}>
              <Upload className="h-3.5 w-3.5 mr-1.5" />
              Bulk Import
            </Button>
            <a href={`/collections/${encodeURIComponent(collectionId)}/items/new`}>
              <Button size="sm">
                <Plus className="h-4 w-4 mr-1.5" />
                Create Item
              </Button>
            </a>
          </div>
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
            title="No items yet"
            description="Create your first item in this collection."
            action={{
              label: "Create Item",
              href: `/collections/${encodeURIComponent(collectionId)}/items/new`,
            }}
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
                    <ItemCard item={item} collectionId={collectionId} />
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
        <Dialog open={importOpen} onOpenChange={(open) => { if (!importing) setImportOpen(open); }}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>Bulk Import Items</DialogTitle>
              <DialogDescription>
                Paste a JSON array of STAC Items or a single STAC Item. Each item
                will be created in the "{collectionId}" collection.
              </DialogDescription>
            </DialogHeader>
            <Textarea
              value={importJson}
              onChange={(e) => setImportJson(e.target.value)}
              placeholder={'[\n  { "type": "Feature", "id": "item-1", ... },\n  { "type": "Feature", "id": "item-2", ... }\n]'}
              rows={12}
              className="font-mono text-xs"
              disabled={importing}
            />
            {importing && (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>
                    {importProgress.done} / {importProgress.total} items
                    {importProgress.errors > 0 && ` (${importProgress.errors} errors)`}
                  </span>
                  <span>{Math.round((importProgress.done / importProgress.total) * 100)}%</span>
                </div>
                <div className="h-2 rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full bg-primary transition-all duration-300"
                    style={{ width: `${(importProgress.done / importProgress.total) * 100}%` }}
                  />
                </div>
              </div>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setImportOpen(false)} disabled={importing}>
                Cancel
              </Button>
              <Button onClick={handleBulkImport} disabled={importing || !importJson.trim()}>
                {importing ? "Importing..." : "Import"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </main>
    </>
  );
}

export function ItemListPage({ collectionId }: { collectionId: string }) {
  return (
    <AppShell>
      <ItemListInner collectionId={collectionId} />
    </AppShell>
  );
}
