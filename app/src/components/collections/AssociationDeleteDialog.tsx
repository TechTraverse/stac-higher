/**
 * Shared confirm dialog for removing an ingest or deliver association
 * (ADR 0009 soft delete): shows the counted deletion impact and keeps the
 * "history is retained" framing consistent across both Data-flow halves.
 */
import { Button } from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  useAssociationDeleteImpact,
  useDeleteAssociation,
} from "@/lib/associations/queries";
import type { Association } from "@/lib/associations/types";

interface AssociationDeleteDialogProps {
  collectionId: string;
  /** The association pending removal; null keeps the dialog closed. */
  target: Association | null;
  onClose: () => void;
}

export function AssociationDeleteDialog({
  collectionId,
  target,
  onClose,
}: AssociationDeleteDialogProps) {
  const deleteMutation = useDeleteAssociation(collectionId);
  const impact = useAssociationDeleteImpact(collectionId, target?.id ?? null);
  // All per-direction copy derives from one noun; only the description
  // genuinely differs in wording.
  const noun =
    target?.direction === "deliver" ? "delivery destination" : "ingest source";
  const title = `Remove ${noun}`;
  const connectionLabel = target?.connection.name ?? target?.connection_id;

  const confirm = () => {
    if (!target) return;
    deleteMutation.mutate(target.id, {
      onSuccess: () => {
        toast.success(`${noun[0].toUpperCase()}${noun.slice(1)} removed`);
        onClose();
      },
      onError: (err) => toast.error(err.message),
    });
  };

  return (
    <Dialog open={!!target} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {target?.direction === "deliver"
              ? `Stop delivering to "${connectionLabel}"? The connection and
                 already-delivered payloads are kept, and the delivery history
                 is retained.`
              : `Stop ingesting from "${connectionLabel}"? The connection and
                 already-ingested items are kept, and the ingest history is
                 retained.`}
          </DialogDescription>
        </DialogHeader>
        <div className="text-sm space-y-1.5">
          {impact.isLoading ? (
            <p className="text-muted-foreground">Calculating impact…</p>
          ) : impact.data ? (
            <>
              {impact.data.reference_items > 0 && (
                <p>
                  {impact.data.reference_items} reference-backed item(s) will
                  be removed from the catalog — with the flow gone they would
                  have no update path (ADR 0009/0011). Disable the flow
                  instead to keep them.
                </p>
              )}
              <p className="text-muted-foreground">
                History retained: {impact.data.history.ingest_files} file
                records, {impact.data.history.delivery_log} delivery records.
              </p>
            </>
          ) : impact.isError ? (
            <p className="text-muted-foreground">
              Could not calculate the deletion impact.
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={confirm}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? "Removing…" : `Remove ${noun}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
