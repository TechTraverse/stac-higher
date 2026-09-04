/**
 * Create/edit dialog for an ingest source (Phase 4) — the §5.1 ingest config
 * surface: source path, include/exclude globs, poll frequency, storage mode,
 * grouping rule, metadata strategy, the post-ingest action, and (W-1) the
 * date window, path template and per-poll cap. Mirrors `DeliveryFormDialog`'s
 * plain useState form pattern; the Zod schema on the server is the contract.
 */
import { useState } from "react";
import {
  Button,
  Input,
  Label,
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
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
import { useBuiltinExtractors } from "@/lib/extractors/queries";
import type { BuiltinSupports } from "@/lib/extractors/schemas";
import { useCreateBuiltinProcess, useProcesses } from "@/lib/processes/queries";

interface IngestFormState {
  connectionId: string;
  sourcePath: string;
  include: string;
  exclude: string;
  pollFrequency: string;
  storageMode: "copy" | "reference";
  groupingRule: "none" | "shared_basename";
  metadataStrategy: "raster_auto" | "sidecar" | "defaults_only" | "extractor";
  /** The extractor process id, when `metadataStrategy` is "extractor". */
  extractorProcessId: string;
  /** X-4: a built-in extractor picked from the registry instead — resolved to
   * a group-owned process id (create-or-reuse) at submit time. Exclusive with
   * `extractorProcessId`. */
  builtinId: string;
  postIngest: "leave" | "delete" | "move";
  movePath: string;
  /** §5.1 expectation window (M2-A) — "" = no expectation declared. */
  expectActivity: string;
  /** W-1 date window + listing bounds — "" = unset (absent from the config). */
  windowBegin: string;
  windowEnd: string;
  pathTemplate: string;
  maxFilesPerPoll: string;
}

/** Picker values for registry entries — a process id never starts with this. */
const BUILTIN_VALUE_PREFIX = "builtin:";

/** X-queue spec §3.6: `grouped` packages need the group the grouping rule
 * assembles; `single_file` packages are refused for grouped associations. */
export function builtinSupportsGrouping(
  supports: BuiltinSupports,
  rule: IngestFormState["groupingRule"],
): boolean {
  return supports === "grouped" ? rule !== "none" : rule === "none";
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
    extractorProcessId: "",
    builtinId: "",
    postIngest: "leave",
    movePath: "",
    expectActivity: "",
    windowBegin: "",
    windowEnd: "",
    pathTemplate: "",
    maxFilesPerPoll: "",
  };
}

/** Seed the form from a stored config. The server only writes schema-complete
 * configs, so parse once through the contract instead of re-narrowing each
 * field by hand. A config the CURRENT schema rejects (written before a schema
 * tightening) falls back to empty config fields — but the expectation is
 * independent of `config` and must survive the fallback, or saving the form
 * would silently disarm the flow monitor for this source. */
function formFromAssociation(a: Association): IngestFormState {
  const expectActivity = expectationSecondsField(
    a.expectation,
    "expect_activity_within_seconds",
  );
  const parsed = ingestConfigSchema.safeParse(a.config);
  if (!parsed.success) {
    return { ...emptyForm(), connectionId: a.connection_id, expectActivity };
  }
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
    extractorProcessId: c.metadata.extractor?.process_id ?? "",
    builtinId: "",
    postIngest: isMove ? "move" : c.post_ingest === "delete" ? "delete" : "leave",
    movePath: isMove ? c.post_ingest.slice("move:".length) : "",
    expectActivity,
    windowBegin: c.window?.begin ?? "",
    windowEnd: c.window?.end ?? "",
    pathTemplate: c.path_template ?? "",
    maxFilesPerPoll:
      c.max_files_per_poll === undefined ? "" : String(c.max_files_per_poll),
  };
}

/** Build the §5.1 ingest config from the form; nested defaults filled server-side. */
function buildConfig(form: IngestFormState) {
  const postIngest =
    form.postIngest === "move" ? `move:${form.movePath.trim()}` : form.postIngest;
  // W-1: a blank field must be ABSENT, never an empty string — the schema is
  // `.strict()` and the pipeline treats absence as "today's behaviour".
  const windowBegin = form.windowBegin.trim();
  const windowEnd = form.windowEnd.trim();
  const pathTemplate = form.pathTemplate.trim();
  const maxFiles = form.maxFilesPerPoll.trim();
  return {
    source_path: form.sourcePath.trim(),
    include: splitCsv(form.include),
    exclude: splitCsv(form.exclude),
    poll_frequency_seconds: Number(form.pollFrequency),
    storage_mode: form.storageMode,
    grouping: { rule: form.groupingRule },
    metadata: {
      strategy: form.metadataStrategy,
      ...(form.metadataStrategy === "extractor" && form.extractorProcessId
        ? { extractor: { process_id: form.extractorProcessId } }
        : {}),
    },
    post_ingest: postIngest,
    ...(windowBegin
      ? { window: { begin: windowBegin, ...(windowEnd ? { end: windowEnd } : {}) } }
      : {}),
    ...(pathTemplate ? { path_template: pathTemplate } : {}),
    ...(maxFiles ? { max_files_per_poll: Number(maxFiles) } : {}),
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
  const { data: processes } = useProcesses();
  const { data: builtinRegistry } = useBuiltinExtractors();
  const createBuiltin = useCreateBuiltinProcess();

  const update = (patch: Partial<IngestFormState>) =>
    setForm((prev) => ({ ...prev, ...patch }));

  // GOES spec §15: the association's group is the selected connection's —
  // client-side filter over the already group-scoped list.
  const connectionGroup =
    connections.find((c) => c.id === form.connectionId)?.group_id ?? null;
  const extractors = (processes ?? []).filter(
    (p) => p.kind === "extractor" && p.group_id === connectionGroup,
  );
  // X-4: the registry entries this group has NOT instantiated yet (an
  // instantiated one is already in `extractors`, as a process), filtered by
  // what the entry's package can build against this form's grouping rule
  // (spec §3.6): a `grouped` package needs the group, a `single_file` one
  // is refused for grouped associations.
  const instantiated = new Set(
    extractors.map((p) => p.builtin_id).filter((id): id is string => id !== null),
  );
  const builtinChoices = (builtinRegistry ?? []).filter(
    (entry) =>
      !instantiated.has(entry.id) &&
      builtinSupportsGrouping(entry.supports, form.groupingRule),
  );
  const pickerValue = form.builtinId
    ? `${BUILTIN_VALUE_PREFIX}${form.builtinId}`
    : form.extractorProcessId;

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
    if (
      form.metadataStrategy === "extractor" &&
      !form.extractorProcessId &&
      !form.builtinId
    ) {
      toast.error("Pick an extractor process");
      return;
    }
    // X-4: a built-in pick is resolved to a group-owned process FIRST
    // (create-or-reuse, audited), then stored exactly as a hand-written
    // extractor is — nothing downstream knows the difference.
    if (form.metadataStrategy === "extractor" && form.builtinId) {
      if (!connectionGroup) {
        toast.error("Pick a connection first — the built-in extractor is created in its group");
        return;
      }
      createBuiltin.mutate(
        { builtin_id: form.builtinId, group_id: connectionGroup },
        {
          onSuccess: (process) => persist({ ...form, extractorProcessId: process.id }),
          onError: (err: Error) => toast.error(err.message),
        },
      );
      return;
    }
    persist(form);
  };

  const persist = (form: IngestFormState) => {
    // The write contract validates the rest (numeric bounds included), so the
    // form can't drift from the server's schema.
    const parsedConfig = ingestConfigSchema.safeParse(buildConfig(form));
    if (!parsedConfig.success) {
      const issue = parsedConfig.error.issues[0];
      toast.error(`${issue.path.join(".")}: ${issue.message}`);
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
    const config = parsedConfig.data;

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

          <div className="grid grid-cols-3 gap-3">
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
              <Label htmlFor="df-max-files">Max files per poll</Label>
              <Input
                id="df-max-files"
                type="number"
                min={1}
                value={form.maxFilesPerPoll}
                onChange={(e) => update({ maxFilesPerPoll: e.target.value })}
                placeholder="unlimited"
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

          <fieldset className="space-y-3 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">Date window</legend>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="df-window-begin">Window begin</Label>
                <Input
                  id="df-window-begin"
                  value={form.windowBegin}
                  onChange={(e) => update({ windowBegin: e.target.value })}
                  placeholder="-6h"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="df-window-end">Window end</Label>
                <Input
                  id="df-window-end"
                  value={form.windowEnd}
                  onChange={(e) => update({ windowEnd: e.target.value })}
                  placeholder="now"
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Optional. Each bound is a timestamp (2026-08-01T00:00:00Z) or an
              offset like -6h, re-resolved on every poll; only files modified
              inside the window are ingested.
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="df-path-template">Path template</Label>
              <Input
                id="df-path-template"
                value={form.pathTemplate}
                onChange={(e) => update({ pathTemplate: e.target.value })}
                placeholder="{Y}/{j}/{H}/"
              />
              <p className="text-xs text-muted-foreground">
                Appended to the source path and expanded from the window into
                the prefixes worth listing ({"{Y} {m} {d} {j} {H}"}), so a huge
                archive is not paged in full on every poll. The max files per
                poll cap paces a wide window instead of ingesting it all at
                once.
              </p>
            </div>
          </fieldset>

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
                  <SelectItem value="extractor">extractor process</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {form.metadataStrategy === "extractor" && (
            <div className="space-y-1.5">
              <Label htmlFor="df-extractor">Extractor process</Label>
              <Select
                value={pickerValue}
                onValueChange={(v) =>
                  v.startsWith(BUILTIN_VALUE_PREFIX)
                    ? update({
                        builtinId: v.slice(BUILTIN_VALUE_PREFIX.length),
                        extractorProcessId: "",
                      })
                    : update({ extractorProcessId: v, builtinId: "" })
                }
              >
                <SelectTrigger id="df-extractor" aria-label="Extractor process">
                  <SelectValue
                    placeholder={
                      extractors.length || builtinChoices.length
                        ? "Pick an extractor"
                        : "No extractor in this connection's group"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {extractors.length > 0 && (
                    <SelectGroup>
                      <SelectLabel>Your extractors</SelectLabel>
                      {extractors.map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.builtin_id ? `${p.name} · built-in` : p.name}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  )}
                  {builtinChoices.length > 0 && (
                    <SelectGroup>
                      <SelectLabel>Built-in</SelectLabel>
                      {builtinChoices.map((entry) => (
                        <SelectItem
                          key={entry.id}
                          value={`${BUILTIN_VALUE_PREFIX}${entry.id}`}
                        >
                          {entry.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  )}
                </SelectContent>
              </Select>
              <p className="text-[12px] text-muted-foreground">
                Each file's draft item is handed to this process before it is
                catalogued. Reference-mode files are staged for the run.
                {form.builtinId && (
                  <>
                    {" "}
                    Picking a built-in extractor creates a read-only process in
                    this connection's group on save (or reuses the one it has).
                  </>
                )}
              </p>
            </div>
          )}

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
