/**
 * Collection "Data flow" tab — ingest half (ROADMAP §8, Phase 4).
 *
 * Associates connections to this (built-in-catalog) collection as ingest
 * sources and edits the §5.1 ingest config. Delivery is Phase 5. Ingest state
 * lives in `stac_higher` (not the catalog), so this reads/writes the same-origin
 * `/api/collections/[id]/connections` surface, never `stacFetch`.
 */
import { useMemo, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Input,
  Label,
  LoadingState,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
} from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Plus, Trash2, Pencil, Radio } from "lucide-react";
import { toast } from "sonner";
import { useConnections } from "@/lib/connections/queries";
import {
  useAssociations,
  useCreateAssociation,
  useDeleteAssociation,
  useUpdateAssociation,
} from "@/lib/associations/queries";
import type { Association } from "@/lib/associations/types";
import type {
  AssociationCreateInput,
  AssociationUpdateInput,
} from "@/lib/associations/schemas";

const STATUS_VARIANT: Record<string, "secondary" | "default" | "destructive"> = {
  ok: "default",
  unverified: "secondary",
  error: "destructive",
};

function splitCsv(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

interface FormState {
  connectionId: string;
  sourcePath: string;
  include: string;
  exclude: string;
  pollFrequency: string;
  storageMode: "copy" | "reference";
  groupingRule: "none" | "shared_basename";
  metadataStrategy: "raster_auto" | "sidecar" | "defaults_only";
  postIngest: "leave" | "delete" | "move";
  movePath: string;
}

function emptyForm(): FormState {
  return {
    connectionId: "",
    sourcePath: "",
    include: "",
    exclude: "",
    pollFrequency: "300",
    storageMode: "copy",
    groupingRule: "none",
    metadataStrategy: "raster_auto",
    postIngest: "leave",
    movePath: "",
  };
}

function formFromAssociation(a: Association): FormState {
  const c = a.config as Record<string, unknown>;
  const grouping = (c.grouping as Record<string, unknown>) ?? {};
  const metadata = (c.metadata as Record<string, unknown>) ?? {};
  const postIngestRaw = typeof c.post_ingest === "string" ? c.post_ingest : "leave";
  const isMove = postIngestRaw.startsWith("move:");
  return {
    connectionId: a.connection_id,
    sourcePath: typeof c.source_path === "string" ? c.source_path : "",
    include: Array.isArray(c.include) ? (c.include as string[]).join(", ") : "",
    exclude: Array.isArray(c.exclude) ? (c.exclude as string[]).join(", ") : "",
    pollFrequency: String(c.poll_frequency_seconds ?? 300),
    storageMode: c.storage_mode === "reference" ? "reference" : "copy",
    groupingRule: grouping.rule === "shared_basename" ? "shared_basename" : "none",
    metadataStrategy:
      metadata.strategy === "sidecar" || metadata.strategy === "defaults_only"
        ? (metadata.strategy as "sidecar" | "defaults_only")
        : "raster_auto",
    postIngest: isMove ? "move" : postIngestRaw === "delete" ? "delete" : "leave",
    movePath: isMove ? postIngestRaw.slice("move:".length) : "",
  };
}

/** Build the §5.1 ingest config from the form; nested defaults filled server-side. */
function buildConfig(form: FormState) {
  const postIngest =
    form.postIngest === "move" ? `move:${form.movePath.trim()}` : form.postIngest;
  return {
    source_path: form.sourcePath.trim(),
    include: splitCsv(form.include),
    exclude: splitCsv(form.exclude),
    poll_frequency_seconds: Number(form.pollFrequency),
    storage_mode: form.storageMode,
    grouping: { rule: form.groupingRule },
    metadata: { strategy: form.metadataStrategy },
    post_ingest: postIngest,
  };
}

interface DataFlowTabProps {
  collectionId: string;
}

export function DataFlowTab({ collectionId }: DataFlowTabProps) {
  const associations = useAssociations(collectionId);
  const connections = useConnections();
  const createMutation = useCreateAssociation(collectionId);
  const updateMutation = useUpdateAssociation(collectionId);
  const deleteMutation = useDeleteAssociation(collectionId);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Association | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [deleteTarget, setDeleteTarget] = useState<Association | null>(null);

  // Only ingest associations this phase; delivery lands in Phase 5.
  const ingest = useMemo(
    () => (associations.data ?? []).filter((a) => a.direction === "ingest"),
    [associations.data],
  );

  const openCreate = () => {
    setEditing(null);
    setForm(emptyForm());
    setDialogOpen(true);
  };

  const openEdit = (a: Association) => {
    setEditing(a);
    setForm(formFromAssociation(a));
    setDialogOpen(true);
  };

  const update = (patch: Partial<FormState>) =>
    setForm((prev) => ({ ...prev, ...patch }));

  const submit = () => {
    if (!editing && !form.connectionId) {
      toast.error("Pick a connection to ingest from");
      return;
    }
    if (!form.sourcePath.trim()) {
      toast.error("A source path is required");
      return;
    }
    if (form.postIngest === "move" && !form.movePath.trim()) {
      toast.error("A destination path is required for the move action");
      return;
    }
    const config = buildConfig(form);

    if (editing) {
      const input: AssociationUpdateInput = { config, enabled: editing.enabled };
      updateMutation.mutate(
        { id: editing.id, input },
        {
          onSuccess: () => {
            toast.success("Ingest source updated");
            setDialogOpen(false);
          },
          onError: (err) => toast.error(err.message),
        },
      );
    } else {
      const input: AssociationCreateInput = {
        connection_id: form.connectionId,
        direction: "ingest",
        enabled: true,
        config,
        expectation: null,
      };
      createMutation.mutate(input, {
        onSuccess: () => {
          toast.success("Ingest source added");
          setDialogOpen(false);
        },
        onError: (err) => toast.error(err.message),
      });
    }
  };

  const toggleEnabled = (a: Association, enabled: boolean) => {
    updateMutation.mutate(
      { id: a.id, input: { enabled } },
      {
        onError: (err) => toast.error(err.message),
      },
    );
  };

  const confirmDelete = () => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        toast.success("Ingest source removed");
        setDeleteTarget(null);
      },
      onError: (err) => toast.error(err.message),
    });
  };

  if (associations.isLoading) return <LoadingState message="Loading data flow…" />;
  if (associations.error) {
    return (
      <ErrorState
        message={
          associations.error instanceof Error
            ? associations.error.message
            : "Failed to load associations"
        }
        onRetry={() => associations.refetch()}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Ingest sources</h2>
          <p className="text-sm text-muted-foreground">
            Connections polled for files to ingest into this collection.
          </p>
        </div>
        <Button size="sm" onClick={openCreate}>
          <Plus className="h-4 w-4 mr-1.5" />
          Add source
        </Button>
      </div>

      {ingest.length === 0 ? (
        <EmptyState
          title="No ingest sources yet"
          description="Associate a connection to start pulling files into this collection."
        />
      ) : (
        <div className="grid gap-3">
          {ingest.map((a) => {
            const cfg = a.config as Record<string, unknown>;
            return (
              <Card key={a.id}>
                <CardHeader className="pb-2">
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="text-base flex items-center gap-2">
                      <Radio className="h-4 w-4" />
                      {a.connection.name ?? a.connection_id}
                    </CardTitle>
                    <div className="flex items-center gap-2">
                      {a.connection.protocol && (
                        <Badge variant="outline" className="text-xs font-mono">
                          {a.connection.protocol}
                        </Badge>
                      )}
                      {a.connection.status && (
                        <Badge
                          variant={STATUS_VARIANT[a.connection.status] ?? "secondary"}
                          className="text-xs"
                        >
                          {a.connection.status}
                        </Badge>
                      )}
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                    <dt className="text-muted-foreground">Source path</dt>
                    <dd className="font-mono truncate">
                      {String(cfg.source_path ?? "—")}
                    </dd>
                    <dt className="text-muted-foreground">Poll every</dt>
                    <dd>{String(cfg.poll_frequency_seconds ?? "—")}s</dd>
                    <dt className="text-muted-foreground">Storage mode</dt>
                    <dd>
                      <Badge variant="secondary" className="text-xs">
                        {String(cfg.storage_mode ?? "copy")}
                      </Badge>
                    </dd>
                  </dl>
                  <div className="flex items-center justify-between pt-1">
                    <label className="flex items-center gap-2 text-sm">
                      <Switch
                        checked={a.enabled}
                        onCheckedChange={(v) => toggleEnabled(a, v)}
                        aria-label="Enabled"
                      />
                      {a.enabled ? "Enabled" : "Disabled"}
                    </label>
                    <div className="flex items-center gap-1.5">
                      <Button variant="ghost" size="sm" onClick={() => openEdit(a)}>
                        <Pencil className="h-3.5 w-3.5 mr-1.5" />
                        Edit
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteTarget(a)}
                      >
                        <Trash2 className="h-3.5 w-3.5 mr-1.5 text-destructive" />
                        Remove
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit ingest source" : "Add ingest source"}</DialogTitle>
            <DialogDescription>
              Poll a connection for files and ingest them into this collection.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
            <div className="space-y-1.5">
              <Label htmlFor="df-connection">Connection</Label>
              <Select
                value={form.connectionId}
                onValueChange={(v) => update({ connectionId: v })}
                disabled={!!editing}
              >
                <SelectTrigger id="df-connection" aria-label="Connection">
                  <SelectValue placeholder="Select a connection" />
                </SelectTrigger>
                <SelectContent>
                  {(connections.data ?? []).map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name} ({c.protocol})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {editing && (
                <p className="text-xs text-muted-foreground">
                  The connection can't be changed — remove and re-add to repoint.
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="df-source-path">Source path</Label>
              <Input
                id="df-source-path"
                value={form.sourcePath}
                onChange={(e) => update({ sourcePath: e.target.value })}
                placeholder="/outgoing/products"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="df-include">Include globs</Label>
                <Input
                  id="df-include"
                  value={form.include}
                  onChange={(e) => update({ include: e.target.value })}
                  placeholder="**/*.tif, **/*.xml"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="df-exclude">Exclude globs</Label>
                <Input
                  id="df-exclude"
                  value={form.exclude}
                  onChange={(e) => update({ exclude: e.target.value })}
                  placeholder="**/*.tmp"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="df-poll">Poll frequency (s)</Label>
                <Input
                  id="df-poll"
                  type="number"
                  min={60}
                  value={form.pollFrequency}
                  onChange={(e) => update({ pollFrequency: e.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="df-storage">Storage mode</Label>
                <Select
                  value={form.storageMode}
                  onValueChange={(v) =>
                    update({ storageMode: v as FormState["storageMode"] })
                  }
                >
                  <SelectTrigger id="df-storage" aria-label="Storage mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="copy">copy (into platform storage)</SelectItem>
                    <SelectItem value="reference">reference (s3 sources)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="df-grouping">Grouping</Label>
                <Select
                  value={form.groupingRule}
                  onValueChange={(v) =>
                    update({ groupingRule: v as FormState["groupingRule"] })
                  }
                >
                  <SelectTrigger id="df-grouping" aria-label="Grouping">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">none (one file per item)</SelectItem>
                    <SelectItem value="shared_basename">shared basename</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="df-metadata">Metadata</Label>
                <Select
                  value={form.metadataStrategy}
                  onValueChange={(v) =>
                    update({ metadataStrategy: v as FormState["metadataStrategy"] })
                  }
                >
                  <SelectTrigger id="df-metadata" aria-label="Metadata strategy">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="raster_auto">raster_auto</SelectItem>
                    <SelectItem value="sidecar">sidecar</SelectItem>
                    <SelectItem value="defaults_only">defaults only</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="df-post">After ingest</Label>
                <Select
                  value={form.postIngest}
                  onValueChange={(v) =>
                    update({ postIngest: v as FormState["postIngest"] })
                  }
                >
                  <SelectTrigger id="df-post" aria-label="Post-ingest action">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="leave">leave in place</SelectItem>
                    <SelectItem value="delete">delete</SelectItem>
                    <SelectItem value="move">move to…</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {form.postIngest === "move" && (
                <div className="space-y-1.5">
                  <Label htmlFor="df-move">Move to path</Label>
                  <Input
                    id="df-move"
                    value={form.movePath}
                    onChange={(e) => update({ movePath: e.target.value })}
                    placeholder="/archived"
                  />
                </div>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={submit}
              disabled={createMutation.isPending || updateMutation.isPending}
            >
              {editing ? "Save changes" : "Add source"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove ingest source</DialogTitle>
            <DialogDescription>
              Stop ingesting from "{deleteTarget?.connection.name ?? deleteTarget?.connection_id}"?
              The connection and already-ingested items are kept; only this
              association and its file ledger are removed.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirmDelete}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? "Removing…" : "Remove source"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
