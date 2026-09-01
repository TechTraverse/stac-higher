import { useState } from "react";
import {
  useExtension,
  useDeleteExtension,
  useImportExtension,
} from "@/lib/extensions/queries";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { JsonViewer } from "@stac-higher/shared";
import { LoadingState } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";
import { Button } from "@stac-higher/shared";
import { Badge } from "@stac-higher/shared";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ArrowLeft,
  Pencil,
  Trash2,
  Copy,
  Check,
  ExternalLink,
  RefreshCw,
} from "lucide-react";
import { toast } from "sonner";
import type { StacExtension } from "@/lib/extensions/types";

interface PropertyRow {
  key: string;
  name: string;
  type: string;
  description: string;
  required: boolean;
}

function extractProperties(extension: StacExtension): PropertyRow[] {
  const props = (extension.schema.properties ?? {}) as Record<
    string,
    Record<string, unknown>
  >;
  const required = (extension.schema.required ?? []) as string[];

  return Object.entries(props).map(([key, schema]) => ({
    key,
    name: key.includes(":") ? key.split(":").slice(1).join(":") : key,
    type: (schema.type as string) ?? "unknown",
    description: (schema.description as string) ?? "",
    required: required.includes(key),
  }));
}

function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
      title={copied ? "Copied!" : `Copy ${label ?? "to clipboard"}`}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-success" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

interface ExtensionDetailInnerProps {
  extensionId: string;
}

function ExtensionDetailInner({ extensionId }: ExtensionDetailInnerProps) {
  const { data: extension, isLoading, error, refetch } = useExtension(extensionId);
  const deleteMutation = useDeleteExtension();
  const refreshMutation = useImportExtension();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const handleDelete = () => {
    deleteMutation.mutate(extensionId, {
      onSuccess: () => {
        toast.success("Extension deleted");
        window.location.href = "/extensions";
      },
      onError: (err) => toast.error(`Delete failed: ${err.message}`),
    });
  };

  const handleRefresh = () => {
    if (!extension?.sourceUrl) return;
    refreshMutation.mutate(extension.sourceUrl, {
      onSuccess: () => {
        toast.success("Extension refreshed from source");
        refetch();
      },
      onError: (err) => toast.error(`Refresh failed: ${err.message}`),
    });
  };

  if (isLoading) {
    return (
      <>
        <Header />
        <main className="flex-1 p-6">
          <LoadingState />
        </main>
      </>
    );
  }

  if (error || !extension) {
    return (
      <>
        <Header />
        <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
          <ErrorState
            message={error instanceof Error ? error.message : "Extension not found"}
            onRetry={() => refetch()}
          />
        </main>
      </>
    );
  }

  const properties = extractProperties(extension);
  const schemaUrl = `${typeof window !== "undefined" ? window.location.origin : ""}/api/extensions/${encodeURIComponent(extension.id)}/schema`;

  return (
    <>
      <Header />
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 mb-4 text-sm text-muted-foreground">
          <a
            href="/extensions"
            className="hover:text-foreground transition-colors inline-flex items-center gap-1"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Extensions
          </a>
          <span>/</span>
          <span className="text-foreground">{extension.name}</span>
        </div>

        {/* Header */}
        <div className="flex items-start justify-between mb-6 gap-4">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-2xl font-bold">{extension.name}</h1>
              <Badge variant="secondary">v{extension.version}</Badge>
              <Badge
                variant={extension.source === "external" ? "outline" : "default"}
              >
                {extension.source}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground font-mono mt-1">
              prefix: {extension.prefix}
            </p>
            {extension.description && (
              <p className="text-sm text-muted-foreground mt-2 max-w-xl">
                {extension.description}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {extension.source === "external" && extension.sourceUrl && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleRefresh}
                disabled={refreshMutation.isPending}
              >
                <RefreshCw
                  className={`h-3.5 w-3.5 mr-1.5 ${refreshMutation.isPending ? "animate-spin" : ""}`}
                />
                Refresh
              </Button>
            )}
            <a href={`/extensions/${encodeURIComponent(extension.id)}/edit`}>
              <Button variant="outline" size="sm">
                <Pencil className="h-3.5 w-3.5 mr-1.5" />
                Edit
              </Button>
            </a>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDeleteOpen(true)}
              className="text-destructive hover:text-destructive"
            >
              <Trash2 className="h-3.5 w-3.5 mr-1.5" />
              Delete
            </Button>
          </div>
        </div>

        <Tabs defaultValue="properties">
          <TabsList className="mb-4">
            <TabsTrigger value="properties">
              Properties ({properties.length})
            </TabsTrigger>
            <TabsTrigger value="schema">JSON Schema</TabsTrigger>
            <TabsTrigger value="info">Info</TabsTrigger>
          </TabsList>

          {/* Properties Tab */}
          <TabsContent value="properties">
            {properties.length === 0 ? (
              <Card>
                <CardContent className="py-8 text-center text-sm text-muted-foreground">
                  No properties defined in this extension.
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Key</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Description</TableHead>
                        <TableHead>Required</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {properties.map((prop) => (
                        <TableRow key={prop.key}>
                          <TableCell className="font-mono text-xs">
                            {prop.key}
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className="text-xs font-mono">
                              {prop.type}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-sm text-muted-foreground max-w-xs">
                            {prop.description || (
                              <span className="italic">No description</span>
                            )}
                          </TableCell>
                          <TableCell>
                            {prop.required && (
                              <Badge variant="destructive" className="text-xs">
                                required
                              </Badge>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* JSON Schema Tab */}
          <TabsContent value="schema" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Schema URL</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center gap-2 p-2 rounded-md bg-muted font-mono text-xs break-all">
                  <span className="flex-1">{schemaUrl}</span>
                  <CopyButton value={schemaUrl} label="schema URL" />
                </div>
              </CardContent>
            </Card>
            <JsonViewer data={extension.schema} title="Raw JSON Schema" defaultOpen />
          </TabsContent>

          {/* Info Tab */}
          <TabsContent value="info">
            <Card>
              <CardContent className="pt-6 space-y-4">
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">ID</p>
                    <p className="font-mono text-xs break-all">{extension.id}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Source</p>
                    <Badge
                      variant={extension.source === "external" ? "outline" : "default"}
                    >
                      {extension.source}
                    </Badge>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Created</p>
                    <p>{new Date(extension.createdAt).toLocaleString()}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Updated</p>
                    <p>{new Date(extension.updatedAt).toLocaleString()}</p>
                  </div>
                </div>

                {extension.source === "external" && extension.sourceUrl && (
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Source URL</p>
                    <div className="flex items-center gap-2">
                      <a
                        href={extension.sourceUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs font-mono text-primary hover:underline break-all flex items-center gap-1"
                      >
                        {extension.sourceUrl}
                        <ExternalLink className="h-3 w-3 shrink-0" />
                      </a>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </main>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Extension</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{extension.name}"? This action cannot
              be undone. Any collections or items using this extension will lose the
              schema reference.
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
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function ExtensionDetailPage({ extensionId }: { extensionId: string }) {
  return (
    <QueryProvider>
      <ExtensionDetailInner extensionId={extensionId} />
    </QueryProvider>
  );
}
