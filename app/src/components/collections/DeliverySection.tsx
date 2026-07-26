/**
 * Data-flow tab — delivery half (Slice D, ROADMAP §8). Lists the collection's
 * deliver associations with the §5.1 config summary, enable/disable, edit and
 * remove, a backfill action (Slice C bridge: POST then poll), and delivery
 * status surfaced from delivery_log — including redeliver on dead-lettered
 * rows.
 */
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  Switch,
} from "@stac-higher/shared";
import { Plus, Trash2, Pencil, Send, History, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { associationKeys } from "@/lib/query/keys";
import {
  useBackfill,
  useDeliveries,
  useRedeliver,
  useRequestBackfill,
  useUpdateAssociation,
} from "@/lib/associations/queries";
import type {
  Association,
  Delivery,
  DeliveryCounts,
  DeliveryStatus,
} from "@/lib/associations/types";
import type { Connection } from "@/lib/connections/types";
import { AssociationDeleteDialog } from "./AssociationDeleteDialog";
import { DeliveryFormDialog } from "./DeliveryFormDialog";

const CONNECTION_STATUS_VARIANT: Record<
  string,
  "secondary" | "default" | "destructive"
> = {
  ok: "default",
  unverified: "secondary",
  error: "destructive",
};

const DELIVERY_STATUS_VARIANT: Record<
  DeliveryStatus,
  "secondary" | "default" | "destructive" | "outline"
> = {
  pending: "secondary",
  delivering: "secondary",
  delivered: "default",
  failed: "destructive",
  dead: "destructive",
};

/** Non-zero per-status counts, in lifecycle order. */
function countEntries(counts: DeliveryCounts): [DeliveryStatus, number][] {
  const order: DeliveryStatus[] = [
    "delivered",
    "pending",
    "delivering",
    "failed",
    "dead",
  ];
  return order.flatMap((status) =>
    counts[status] > 0 ? [[status, counts[status]] as [DeliveryStatus, number]] : [],
  );
}

const RECENT_LIMIT = 8;

interface DeliveryStatusPanelProps {
  collectionId: string;
  association: Association;
}

function DeliveryStatusPanel({
  collectionId,
  association,
}: DeliveryStatusPanelProps) {
  const deliveries = useDeliveries(collectionId, association.id);
  const redeliver = useRedeliver(collectionId, association.id);

  if (deliveries.isLoading) {
    return (
      <p className="text-sm text-muted-foreground">Loading delivery status…</p>
    );
  }
  if (deliveries.error) {
    return (
      <p className="text-sm text-muted-foreground">
        Could not load delivery status.
      </p>
    );
  }
  const data = deliveries.data;
  if (!data) return null;
  const entries = countEntries(data.counts);
  if (entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No deliveries yet — new items are delivered as they land.
      </p>
    );
  }
  const recent = data.deliveries.slice(0, RECENT_LIMIT);
  const hasRetryState = data.counts.failed > 0 || data.counts.dead > 0;

  const onRedeliver = (delivery: Delivery) => {
    redeliver.mutate(delivery.id, {
      onSuccess: () =>
        toast.success(`Redelivery of ${delivery.item_id} requested`),
      onError: (err) => toast.error(err.message),
    });
  };

  return (
    <div className="space-y-2" data-testid="delivery-status">
      <div className="flex flex-wrap items-center gap-1.5">
        {entries.map(([status, count]) => (
          <Badge
            key={status}
            variant={DELIVERY_STATUS_VARIANT[status]}
            className="text-xs"
          >
            {count} {status}
          </Badge>
        ))}
      </div>
      <ul className="divide-y rounded-md border text-sm">
        {recent.map((d) => (
          <li key={d.id} className="px-3 py-1.5 space-y-0.5">
            <div className="flex items-center gap-2">
              <span className="font-mono truncate flex-1" title={d.item_id}>
                {d.item_id}
              </span>
              <Badge
                variant={DELIVERY_STATUS_VARIANT[d.status]}
                className="text-xs"
              >
                {d.status}
              </Badge>
              <span
                className="text-xs text-muted-foreground whitespace-nowrap"
                title="Attempts this cycle — a new item event or a redeliver starts a fresh cycle"
              >
                {d.attempts} attempt{d.attempts === 1 ? "" : "s"}
              </span>
              {d.status === "dead" && (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 text-xs"
                  onClick={() => onRedeliver(d)}
                  disabled={redeliver.isPending}
                >
                  <RotateCcw className="h-3 w-3 mr-1" />
                  Redeliver
                </Button>
              )}
            </div>
            {d.error && (d.status === "failed" || d.status === "dead") && (
              <p
                className="text-xs text-muted-foreground truncate"
                title={d.error}
              >
                {d.error}
              </p>
            )}
          </li>
        ))}
      </ul>
      {hasRetryState && (
        <p className="text-xs text-muted-foreground">
          Attempt counts are per delivery cycle — a new item event or a
          redeliver starts a fresh cycle.
        </p>
      )}
    </div>
  );
}

interface DeliveryCardProps {
  collectionId: string;
  association: Association;
  onEdit: (a: Association) => void;
  onDelete: (a: Association) => void;
}

function DeliveryCard({
  collectionId,
  association: a,
  onEdit,
  onDelete,
}: DeliveryCardProps) {
  const qc = useQueryClient();
  const updateMutation = useUpdateAssociation(collectionId);
  const requestBackfill = useRequestBackfill(collectionId);
  const [backfillId, setBackfillId] = useState<string | null>(null);
  const backfill = useBackfill(collectionId, a.id, backfillId);
  const backfillStatus = backfill.data?.status;
  const itemsEnqueued = backfill.data?.items_enqueued ?? 0;
  const backfillError = backfill.data?.error;

  // Close out the poll on terminal states; the toast is the completion signal.
  useEffect(() => {
    if (!backfillId || !backfillStatus) return;
    if (backfillStatus === "completed") {
      toast.success(`Backfill completed — ${itemsEnqueued} item(s) enqueued`);
      setBackfillId(null);
      qc.invalidateQueries({
        queryKey: associationKeys.deliveries(collectionId, a.id),
      });
    } else if (backfillStatus === "failed") {
      toast.error(`Backfill failed${backfillError ? `: ${backfillError}` : ""}`);
      setBackfillId(null);
    }
  }, [backfillId, backfillStatus, itemsEnqueued, backfillError, qc, collectionId, a.id]);

  const startBackfill = () => {
    requestBackfill.mutate(a.id, {
      onSuccess: (b) => {
        setBackfillId(b.id);
        toast.success("Backfill requested — existing items will be delivered");
      },
      onError: (err) => toast.error(err.message),
    });
  };

  const toggleEnabled = (enabled: boolean) => {
    updateMutation.mutate(
      { id: a.id, input: { enabled } },
      { onError: (err) => toast.error(err.message) },
    );
  };

  const cfg = a.config as Record<string, unknown>;
  const backfillActive = backfillId !== null || requestBackfill.isPending;

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-base flex items-center gap-2">
            <Send className="h-4 w-4" />
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
                variant={
                  CONNECTION_STATUS_VARIANT[a.connection.status] ?? "secondary"
                }
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
          <dt className="text-muted-foreground">Path template</dt>
          <dd className="font-mono truncate" title={String(cfg.path_template ?? "")}>
            {String(cfg.path_template ?? "—")}
          </dd>
          <dt className="text-muted-foreground">On update</dt>
          <dd>{String(cfg.on_update ?? "redeliver")}</dd>
          <dt className="text-muted-foreground">Overwrite</dt>
          <dd>{String(cfg.overwrite ?? "if_newer")}</dd>
          {typeof cfg.item_filter === "string" && cfg.item_filter && (
            <>
              <dt className="text-muted-foreground">Item filter</dt>
              <dd className="font-mono truncate" title={cfg.item_filter}>
                {cfg.item_filter}
              </dd>
            </>
          )}
        </dl>

        <DeliveryStatusPanel collectionId={collectionId} association={a} />

        {backfillActive && (
          <p className="text-sm text-muted-foreground" role="status">
            Backfill {backfillStatus ?? "requested"} — {itemsEnqueued} item(s)
            enqueued…
          </p>
        )}

        <div className="flex items-center justify-between pt-1">
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={a.enabled}
              onCheckedChange={toggleEnabled}
              aria-label="Enabled"
            />
            {a.enabled ? "Enabled" : "Disabled"}
          </label>
          <div className="flex items-center gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              onClick={startBackfill}
              disabled={!a.enabled || backfillActive}
              title="Deliver the collection's existing items to this destination"
            >
              <History className="h-3.5 w-3.5 mr-1.5" />
              Backfill
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onEdit(a)}>
              <Pencil className="h-3.5 w-3.5 mr-1.5" />
              Edit
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onDelete(a)}>
              <Trash2 className="h-3.5 w-3.5 mr-1.5 text-destructive" />
              Remove
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

interface DeliverySectionProps {
  collectionId: string;
  /** The collection's deliver-direction associations. */
  associations: Association[];
  connections: Connection[];
}

export function DeliverySection({
  collectionId,
  associations,
  connections,
}: DeliverySectionProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Association | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Association | null>(null);

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };

  const openEdit = (a: Association) => {
    setEditing(a);
    setDialogOpen(true);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Delivery destinations</h2>
          <p className="text-sm text-muted-foreground">
            Connections this collection's items are delivered to as they land.
          </p>
        </div>
        <Button size="sm" onClick={openCreate}>
          <Plus className="h-4 w-4 mr-1.5" />
          Add destination
        </Button>
      </div>

      {associations.length === 0 ? (
        <EmptyState
          icon={Send}
          title="No delivery destinations yet"
          description="Associate a connection to deliver this collection's items as they are created or updated."
        />
      ) : (
        <div className="grid gap-3">
          {associations.map((a) => (
            <DeliveryCard
              key={a.id}
              collectionId={collectionId}
              association={a}
              onEdit={openEdit}
              onDelete={setDeleteTarget}
            />
          ))}
        </div>
      )}

      <DeliveryFormDialog
        collectionId={collectionId}
        open={dialogOpen}
        editing={editing}
        connections={connections}
        onOpenChange={setDialogOpen}
      />

      <AssociationDeleteDialog
        collectionId={collectionId}
        target={deleteTarget}
        onClose={() => setDeleteTarget(null)}
      />
    </div>
  );
}
