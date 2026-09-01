import { useState } from "react";
import { useExtensions } from "@/lib/extensions/queries";
import { AppShell } from "@/components/layout/AppShell";
import { LoadingState } from "@stac-higher/shared";
import { EmptyState } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";
import { ImportExtensionDialog } from "./ImportExtensionDialog";
import { Button } from "@stac-higher/shared";
import { Input } from "@stac-higher/shared";
import { Badge } from "@stac-higher/shared";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@stac-higher/shared";
import { Puzzle, Plus, Download, Search, Tag, Hash } from "lucide-react";
import type { StacExtension } from "@/lib/extensions/types";

interface ExtensionCardProps {
  extension: StacExtension;
}

function getPropertyCount(schema: Record<string, unknown>): number {
  const props = schema.properties as Record<string, unknown> | undefined;
  return props ? Object.keys(props).length : 0;
}

function ExtensionCard({ extension }: ExtensionCardProps) {
  const propertyCount = getPropertyCount(extension.schema);

  return (
    <a
      href={`/extensions/${encodeURIComponent(extension.id)}`}
      className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ring-offset-background"
      onKeyDown={(e) => {
        if (e.key === " ") {
          e.preventDefault();
          e.currentTarget.click();
        }
      }}
    >
      <Card className="hover:bg-accent/50 transition-colors cursor-pointer h-full">
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <CardTitle className="text-base truncate">{extension.name}</CardTitle>
              <p className="text-xs text-muted-foreground font-mono mt-0.5">
                {extension.prefix}
              </p>
            </div>
            <div className="flex flex-col items-end gap-1 shrink-0">
              <Badge variant="secondary" className="text-xs">
                v{extension.version}
              </Badge>
              <Badge
                variant={extension.source === "external" ? "outline" : "default"}
                className="text-xs"
              >
                {extension.source}
              </Badge>
            </div>
          </div>
          {extension.description && (
            <CardDescription className="line-clamp-2 text-xs">
              {extension.description}
            </CardDescription>
          )}
        </CardHeader>
        <CardContent className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Hash className="h-3 w-3 shrink-0" />
            <span>
              {propertyCount} {propertyCount === 1 ? "property" : "properties"}
            </span>
          </div>
          {extension.source === "external" && extension.sourceUrl && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Tag className="h-3 w-3 shrink-0" />
              <span className="truncate">{extension.sourceUrl}</span>
            </div>
          )}
        </CardContent>
      </Card>
    </a>
  );
}


function ExtensionListInner() {
  const { data: extensions, isLoading, error, refetch } = useExtensions();
  const [search, setSearch] = useState("");
  const [importOpen, setImportOpen] = useState(false);

  const allExtensions = extensions ?? [];
  const filtered = search
    ? allExtensions.filter(
        (e) =>
          e.name.toLowerCase().includes(search.toLowerCase()) ||
          e.prefix.toLowerCase().includes(search.toLowerCase()) ||
          e.description.toLowerCase().includes(search.toLowerCase()),
      )
    : allExtensions;

  return (
    <>
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">Extensions</h1>
            <p className="text-sm text-muted-foreground mt-1">
              {allExtensions.length} extension{allExtensions.length !== 1 ? "s" : ""} available
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setImportOpen(true)}>
              <Download className="h-4 w-4 mr-1.5" />
              Import
            </Button>
            <a href="/extensions/new">
              <Button>
                <Plus className="h-4 w-4 mr-1.5" />
                Create Extension
              </Button>
            </a>
          </div>
        </div>

        {allExtensions.length > 0 && (
          <div className="relative mb-6">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search extensions..."
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
            message={error instanceof Error ? error.message : "Failed to load extensions"}
            onRetry={() => refetch()}
          />
        ) : filtered.length === 0 && search ? (
          <EmptyState
            icon={Search}
            title="No results"
            description={`No extensions match "${search}"`}
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={Puzzle}
            title="No extensions yet"
            description="Create a custom STAC extension or import one from a remote schema URL."
            action={{ label: "Create Extension", href: "/extensions/new" }}
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filtered.map((extension) => (
              <ExtensionCard key={extension.id} extension={extension} />
            ))}
          </div>
        )}
      </main>

      <ImportExtensionDialog open={importOpen} onOpenChange={setImportOpen} />
    </>
  );
}

export function ExtensionListPage() {
  return (
    <AppShell>
      <ExtensionListInner />
    </AppShell>
  );
}
