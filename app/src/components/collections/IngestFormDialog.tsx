/**
 * Create/edit dialog for an ingest source (Phase 4) — the §5.1 ingest config
 * surface: source path, include/exclude globs, poll frequency, storage mode,
 * grouping rule, metadata strategy, and the post-ingest action. Mirrors
 * `DeliveryFormDialog`'s plain useState form pattern; the Zod schema on the
 * server is the contract.
 */
import { useState } from "react";
import {
  Button,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import type { Connection } from "@/lib/connections/types";
import {
  useCreateAssociation,
  useUpdateAssociation,
} from "@/lib/associations/queries";
import type { Association } from "@/lib/associations/types";
import { ingestConfigSchema } from "@/lib/associations/schemas";
import { expectationSecondsField, parseExpectationSeconds, splitCsv } from "./shared";

interface IngestFormState {
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
  /** §5.1 expectation window (M2-A) — "" = no expectation declared. */
  expectActivity: string;
}

function emptyForm(): IngestFormState {
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
    expectActivity: "",
  };
}

/** Seed the form from a stored config. The server only writes schema-complete
 * configs, so parse once through the contract instead of re-narrowing each
 * field by hand (an unparseable config falls back to the empty form). */
function formFromAssociation(a: Association): IngestFormState {
  const parsed = ingestConfigSchema.safeParse(a.config);
  if (!parsed.success) return { ...emptyForm(), connectionId: a.connection_id };
  const c = parsed.data;
  const isMove = c.post_ingest.startsWith("move:");
  return {
    connectionId: a.connection_id,
    sourcePath: c.source_path,
    include: c.include.join(", "),
    exclude: c.exclude.join(", "),
    pollFrequency: String(c.poll_frequency_seconds),
    storageMode: c.storage_mode,
    groupingRule: c.grouping.rule,
    metadataStrategy: c.metadata.strategy,
    postIngest: isMove ? "move" : c.post_ingest === "delete" ? "delete" : "leave",
    movePath: isMove ? c.post_ingest.slice("move:".length) : "",
    expectActivity: expectationSecondsField(
      a.expectation,
      "expect_activity_within_seconds",
    ),
  };
}

/** Build the §5.1 ingest config from the form; nested defaults filled server-side. */
function buildConfig(form: IngestFormState) {
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

interface IngestFormDialogProps {
  collectionId: string;
  open: boolean;
  /** Existing association when editing; null when creating. */
  editing: Association | null;
  connections: Connection[];
  onOpenChange: (open: boolean) => void;
}

export function IngestFormDialog({
  collectionId,
  open,
  editing,
  connections,
  onOpenChange,
}: IngestFormDialogProps) {
  const createMutation = useCreateAssociation(collectionId);
  const updateMutation = useUpdateAssociation(collectionId);
  // The parent mounts this dialog only while open, so lazy state seeds the
  // form once per open — no re-seed bookkeeping needed.
  const [form, setForm] = useState<IngestFormState>(() =>
    editing ? formFromAssociation(editing) : emptyForm(),
  );

  const update = (patch: Partial<IngestFormState>) =>
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
    const expectSeconds = parseExpectationSeconds(form.expectActivity);
    if (expectSeconds === undefined) {
      toast.error("The activity window must be a whole number of seconds (≥ 1)");
      return;
    }
    const expectation =
      expectSeconds === null
        ? null
        : { expect_activity_within_seconds: expectSeconds };
    const config = buildConfig(form);

    const callbacks = {
      onSuccess: () => {
        toast.success(editing ? "Ingest source updated" : "Ingest source added");
        onOpenChange(false);
      },
      onError: (err: Error) => toast.error(err.message),
    };
    if (editing) {
      updateMutation.mutate(
        { id: editing.id, input: { config, enabled: editing.enabled, expectation } },
        callbacks,
      );
    } else {
      createMutation.mutate(
        {
          connection_id: form.connectionId,
          direction: "ingest",
          enabled: true,
          config,
          expectation,
        },
        callbacks,
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
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
                {connections.map((c) => (
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
                  update({ storageMode: v as IngestFormState["storageMode"] })
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
                  update({ groupingRule: v as IngestFormState["groupingRule"] })
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
                  update({ metadataStrategy: v as IngestFormState["metadataStrategy"] })
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
                  update({ postIngest: v as IngestFormState["postIngest"] })
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

          <div className="space-y-1.5">
            <Label htmlFor="df-expect-activity">Alert if no activity within (s)</Label>
            <Input
              id="df-expect-activity"
              type="number"
              min={1}
              value={form.expectActivity}
              onChange={(e) => update({ expectActivity: e.target.value })}
              placeholder="no expectation"
            />
            <p className="text-xs text-muted-foreground">
              Optional. When set, the flow monitor raises an alert if this
              source settles or itemizes nothing for that long.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
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
  );
}
