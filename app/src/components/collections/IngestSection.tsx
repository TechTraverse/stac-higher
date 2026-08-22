/**
 * Data-flow tab — ingest half (Phase 4, ROADMAP §8). Lists the collection's
 * ingest associations with the §5.1 config summary, enable/disable, edit and
 * remove. Mirrors `DeliverySection`'s structure: card list + mount-per-open
 * form dialog + the shared impact delete dialog.
 */
import { useState } from "react";
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
import { Plus, Trash2, Pencil, Radio } from "lucide-react";
import { toast } from "sonner";
import { useUpdateAssociation } from "@/lib/associations/queries";
import type { Association } from "@/lib/associations/types";
import type { Connection } from "@/lib/connections/types";
import { AssociationDeleteDialog } from "./AssociationDeleteDialog";
import { IngestFormDialog } from "./IngestFormDialog";
import { CONNECTION_STATUS_VARIANT } from "./shared";

interface IngestCardProps {
  collectionId: string;
  association: Association;
  onEdit: (a: Association) => void;
  onDelete: (a: Association) => void;
}

function IngestCard({
  collectionId,
  association: a,
  onEdit,
  onDelete,
}: IngestCardProps) {
  const updateMutation = useUpdateAssociation(collectionId);

  const toggleEnabled = (enabled: boolean) => {
    updateMutation.mutate(
      { id: a.id, input: { enabled } },
      { onError: (err) => toast.error(err.message) },
    );
  };

  const cfg = a.config as Record<string, unknown>;

  return (
    <Card>
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
                variant={CONNECTION_STATUS_VARIANT[a.connection.status]}
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
              onCheckedChange={toggleEnabled}
              aria-label="Enabled"
            />
            {a.enabled ? "Enabled" : "Disabled"}
          </label>
          <div className="flex items-center gap-1.5">
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

interface IngestSectionProps {
  collectionId: string;
  /** The collection's ingest-direction associations. */
  associations: Association[];
  connections: Connection[];
}

export function IngestSection({
  collectionId,
  associations,
  connections,
}: IngestSectionProps) {
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

      {associations.length === 0 ? (
        <EmptyState
          icon={Radio}
          title="No ingest sources yet"
          description="Associate a connection to start pulling files into this collection."
        />
      ) : (
        <div className="grid gap-3">
          {associations.map((a) => (
            <IngestCard
              key={a.id}
              collectionId={collectionId}
              association={a}
              onEdit={openEdit}
              onDelete={setDeleteTarget}
            />
          ))}
        </div>
      )}

      {/* Mounted only while open (the ConnectionsPage pattern): a fresh mount
          per open seeds the form from `editing` with plain lazy state. */}
      {dialogOpen && (
        <IngestFormDialog
          collectionId={collectionId}
          open={dialogOpen}
          editing={editing}
          connections={connections}
          onOpenChange={setDialogOpen}
        />
      )}

      <AssociationDeleteDialog
        collectionId={collectionId}
        target={deleteTarget}
        onClose={() => setDeleteTarget(null)}
      />
    </div>
  );
}
