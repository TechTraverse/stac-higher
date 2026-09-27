/**
 * Create/edit dialog for a delivery destination (Slice D) — the §5.1 delivery
 * config surface: path template, CQL2 item filter, asset-key filter, payload
 * sidecar toggles, on_update / overwrite policies, retry budget, and the
 * per-connection transfer concurrency cap. Mirrors the ingest dialog's plain
 * useState form pattern; the Zod schema on the server is the contract.
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
import { toast } from "sonner";
import type { Connection } from "@/lib/connections/types";
import {
  useCreateAssociation,
  useUpdateAssociation,
} from "@/lib/associations/queries";
import type { Association } from "@/lib/associations/types";
import { deliveryConfigSchema } from "@/lib/associations/schemas";
import type { DeliveryConfig } from "@/lib/associations/schemas";
import { expectationSecondsField, parseExpectationSeconds, splitCsv } from "./shared";

interface DeliveryFormState {
  connectionId: string;
  pathTemplate: string;
  itemFilter: string;
  assetKeys: string;
  payloadItemJson: boolean;
  payloadChecksums: "none" | "md5" | "sha256";
  payloadCompletionMarker: boolean;
  onUpdate: "redeliver" | "ignore";
  overwrite: "never" | "always" | "if_newer";
  retryMaxAttempts: string;
  retryBackoff: "exponential" | "fixed";
  maxConcurrentTransfers: string;
  /** §5.1 NRT SLO window (M2-A) — "" = no expectation declared. */
  deliverWithin: string;
}

function emptyForm(): DeliveryFormState {
  return {
    connectionId: "",
    pathTemplate: "",
    itemFilter: "",
    assetKeys: "",
    payloadItemJson: false,
    payloadChecksums: "none",
    payloadCompletionMarker: false,
    onUpdate: "redeliver",
    overwrite: "if_newer",
    retryMaxAttempts: "5",
    retryBackoff: "exponential",
    maxConcurrentTransfers: "4",
    deliverWithin: "",
  };
}

/** Seed the form from a stored config. The server only writes schema-complete
 * configs, so parse once through the contract instead of re-narrowing each
 * field by hand (an unparseable config falls back to the empty form). */
function formFromAssociation(a: Association): DeliveryFormState {
  const parsed = deliveryConfigSchema.safeParse(a.config);
  if (!parsed.success) return { ...emptyForm(), connectionId: a.connection_id };
  const c = parsed.data;
  return {
    connectionId: a.connection_id,
    pathTemplate: c.path_template,
    itemFilter: c.item_filter ?? "",
    assetKeys: c.asset_keys?.join(", ") ?? "",
    payloadItemJson: c.payload.item_json,
    payloadChecksums: c.payload.checksums ?? "none",
    payloadCompletionMarker: c.payload.completion_marker,
    onUpdate: c.on_update,
    overwrite: c.overwrite,
    retryMaxAttempts: String(c.retry.max_attempts),
    retryBackoff: c.retry.backoff,
    maxConcurrentTransfers: String(c.max_concurrent_transfers),
    deliverWithin: expectationSecondsField(a.expectation, "deliver_within_seconds"),
  };
}

/** Build the §5.1 delivery config from the form (empty optionals → null). */
function buildConfig(form: DeliveryFormState): DeliveryConfig {
  const assetKeys = splitCsv(form.assetKeys);
  return {
    path_template: form.pathTemplate.trim(),
    item_filter: form.itemFilter.trim() || null,
    asset_keys: assetKeys.length > 0 ? assetKeys : null,
    payload: {
      item_json: form.payloadItemJson,
      checksums: form.payloadChecksums === "none" ? null : form.payloadChecksums,
      completion_marker: form.payloadCompletionMarker,
    },
    on_update: form.onUpdate,
    overwrite: form.overwrite,
    retry: {
      max_attempts: Number(form.retryMaxAttempts),
      backoff: form.retryBackoff,
    },
    max_concurrent_transfers: Number(form.maxConcurrentTransfers),
  };
}

interface DeliveryFormDialogProps {
  collectionId: string;
  open: boolean;
  /** Existing association when editing; null when creating. */
  editing: Association | null;
  connections: Connection[];
  onOpenChange: (open: boolean) => void;
}

export function DeliveryFormDialog({
  collectionId,
  open,
  editing,
  connections,
  onOpenChange,
}: DeliveryFormDialogProps) {
  const createMutation = useCreateAssociation(collectionId);
  const updateMutation = useUpdateAssociation(collectionId);
  // The parent mounts this dialog only while open, so lazy state seeds the
  // form once per open — no re-seed bookkeeping needed.
  const [form, setForm] = useState<DeliveryFormState>(() =>
    editing ? formFromAssociation(editing) : emptyForm(),
  );

  const update = (patch: Partial<DeliveryFormState>) =>
    setForm((prev) => ({ ...prev, ...patch }));

  const submit = () => {
    if (!editing && !form.connectionId) {
      toast.error("Pick a connection to deliver to");
      return;
    }
    if (!form.pathTemplate.trim()) {
      toast.error("A path template is required");
      return;
    }
    // The write contract validates the rest (numeric bounds included), so the
    // form can't drift from the server's schema.
    const parsed = deliveryConfigSchema.safeParse(buildConfig(form));
    if (!parsed.success) {
      const issue = parsed.error.issues[0];
      toast.error(`${issue.path.join(".")}: ${issue.message}`);
      return;
    }
    const config = parsed.data;
    const deliverSeconds = parseExpectationSeconds(form.deliverWithin);
    if (deliverSeconds === undefined) {
      toast.error("The delivery SLO must be a whole number of seconds (≥ 1)");
      return;
    }
    const expectation =
      deliverSeconds === null ? null : { deliver_within_seconds: deliverSeconds };

    const callbacks = {
      onSuccess: () => {
        toast.success(
          editing ? "Delivery destination updated" : "Delivery destination added",
        );
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
          direction: "deliver",
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
          <DialogTitle>
            {editing ? "Edit delivery destination" : "Add delivery destination"}
          </DialogTitle>
          <DialogDescription>
            Deliver this collection's items to a connection as they are created
            or updated.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
          <div className="space-y-1.5">
            <Label htmlFor="dl-connection">Connection</Label>
            <Select
              value={form.connectionId}
              onValueChange={(v) => update({ connectionId: v })}
              disabled={!!editing}
            >
              <SelectTrigger id="dl-connection" aria-label="Connection">
                <SelectValue placeholder="Select a connection" />
              </SelectTrigger>
              <SelectContent>
                {connections.filter((c) => c.protocol !== "registry").map((c) => (
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
            <Label htmlFor="dl-path-template">Path template</Label>
            <Input
              id="dl-path-template"
              value={form.pathTemplate}
              onChange={(e) => update({ pathTemplate: e.target.value })}
              placeholder="{collection}/{yyyy}/{mm}/{dd}/{item_id}/{filename}"
            />
            <p className="text-xs text-muted-foreground">
              Rendered per asset. Tokens: {"{collection} {item_id} {filename}"}{" "}
              {"{yyyy} {mm} {dd}"}.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="dl-item-filter">Item filter (CQL2)</Label>
            <Input
              id="dl-item-filter"
              value={form.itemFilter}
              onChange={(e) => update({ itemFilter: e.target.value })}
              placeholder="eo:cloud_cover < 20"
            />
            <p className="text-xs text-muted-foreground">
              Optional — leave empty to deliver every item.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="dl-asset-keys">Asset keys</Label>
            <Input
              id="dl-asset-keys"
              value={form.assetKeys}
              onChange={(e) => update({ assetKeys: e.target.value })}
              placeholder="visual, thumbnail"
            />
            <p className="text-xs text-muted-foreground">
              Optional comma-separated list — leave empty to deliver all assets.
            </p>
          </div>

          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Payload</legend>
            <label className="flex items-center justify-between text-sm">
              Item JSON sidecar
              <Switch
                checked={form.payloadItemJson}
                onCheckedChange={(v) => update({ payloadItemJson: v })}
                aria-label="Item JSON sidecar"
              />
            </label>
            <label className="flex items-center justify-between text-sm">
              Completion marker (written last)
              <Switch
                checked={form.payloadCompletionMarker}
                onCheckedChange={(v) => update({ payloadCompletionMarker: v })}
                aria-label="Completion marker"
              />
            </label>
            <div className="flex items-center justify-between gap-3 text-sm">
              <Label htmlFor="dl-checksums" className="font-normal">
                Checksum sidecars
              </Label>
              <Select
                value={form.payloadChecksums}
                onValueChange={(v) =>
                  update({
                    payloadChecksums: v as DeliveryFormState["payloadChecksums"],
                  })
                }
              >
                <SelectTrigger
                  id="dl-checksums"
                  aria-label="Checksum sidecars"
                  className="w-32"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">none</SelectItem>
                  <SelectItem value="md5">md5</SelectItem>
                  <SelectItem value="sha256">sha256</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </fieldset>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="dl-on-update">On item update</Label>
              <Select
                value={form.onUpdate}
                onValueChange={(v) =>
                  update({ onUpdate: v as DeliveryFormState["onUpdate"] })
                }
              >
                <SelectTrigger id="dl-on-update" aria-label="On item update">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="redeliver">
                    redeliver changed assets
                  </SelectItem>
                  <SelectItem value="ignore">ignore updates</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dl-overwrite">Overwrite</Label>
              <Select
                value={form.overwrite}
                onValueChange={(v) =>
                  update({ overwrite: v as DeliveryFormState["overwrite"] })
                }
              >
                <SelectTrigger id="dl-overwrite" aria-label="Overwrite">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="if_newer">if newer</SelectItem>
                  <SelectItem value="always">always</SelectItem>
                  <SelectItem value="never">never</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="dl-max-attempts">Max attempts</Label>
              <Input
                id="dl-max-attempts"
                type="number"
                min={1}
                value={form.retryMaxAttempts}
                onChange={(e) => update({ retryMaxAttempts: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                Attempts per cycle before a delivery is dead-lettered.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dl-backoff">Retry backoff</Label>
              <Select
                value={form.retryBackoff}
                onValueChange={(v) =>
                  update({ retryBackoff: v as DeliveryFormState["retryBackoff"] })
                }
              >
                <SelectTrigger id="dl-backoff" aria-label="Retry backoff">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="exponential">exponential</SelectItem>
                  <SelectItem value="fixed">fixed</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="dl-concurrency">Concurrent transfers</Label>
            <Input
              id="dl-concurrency"
              type="number"
              min={1}
              value={form.maxConcurrentTransfers}
              onChange={(e) =>
                update({ maxConcurrentTransfers: e.target.value })
              }
            />
            <p className="text-xs text-muted-foreground">
              Per-connection cap; SFTP/FTP destinations transfer serially
              regardless.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="dl-deliver-within">Alert if not delivered within (s)</Label>
            <Input
              id="dl-deliver-within"
              type="number"
              min={1}
              value={form.deliverWithin}
              onChange={(e) => update({ deliverWithin: e.target.value })}
              placeholder="no expectation"
            />
            <p className="text-xs text-muted-foreground">
              Optional NRT SLO. When set, the flow monitor raises an alert if
              deliveries take longer than this from item event to delivered.
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
            {editing ? "Save changes" : "Add destination"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
