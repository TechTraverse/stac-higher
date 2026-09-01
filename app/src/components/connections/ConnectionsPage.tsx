import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { connectionKeys } from "@/lib/query/keys";
import { AppShell } from "@/components/layout/AppShell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  LoadingState,
} from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  KeyRound,
  Loader2,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { useAuthMe } from "@/lib/query/auth";
import {
  useConnectionDeleteImpact,
  useConnections,
  useDeleteConnection,
  useResetHostKey,
} from "@/lib/connections/queries";
import { runConnectionTest } from "@/lib/connections/api";
import { isSshFamily } from "@/lib/connections/schemas";
import type { Connection } from "@/lib/connections/types";
import { ConnectionForm } from "./ConnectionForm";
import { useFlows } from "@/lib/monitoring/queries";
import { healthDotClass, type LineageHealth } from "@stac-higher/shared";

type DirectionFilter = "all" | "ingest" | "deliver";

const DIRECTION_FILTERS: Array<{ value: DirectionFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "ingest", label: "Ingest sources" },
  { value: "deliver", label: "Distribution destinations" },
];

const STATUS_HEALTH: Record<Connection["status"], LineageHealth> = {
  ok: "ok",
  error: "error",
  unverified: "unknown",
};

const STATUS_LABEL: Record<Connection["status"], string> = {
  ok: "Reachable",
  error: "Unreachable",
  unverified: "Unverified",
};

const STATUS_TEXT: Record<Connection["status"], string> = {
  ok: "text-success",
  error: "text-danger",
  unverified: "text-muted-foreground",
};

/**
 * The health chip: a status DOT rather than a filled badge.
 *
 * The old badge used the primary colour for "OK", which read as decoration on
 * a page where colour is supposed to mean health (ADR 0017 §3). "Unverified"
 * is deliberately neutral — untested is not unhealthy.
 */
function StatusChip({ status }: { status: Connection["status"] }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden="true"
        className={`h-2 w-2 shrink-0 rounded-full ${healthDotClass(STATUS_HEALTH[status])}`}
      />
      <span className={`text-[12.5px] font-semibold ${STATUS_TEXT[status]}`}>
        {STATUS_LABEL[status]}
      </span>
    </span>
  );
}

/**
 * How this connection is actually wired, from the association list.
 *
 * Direction is NOT a property of a connection — it lives on the association
 * (`collection_connections.direction`), so the only truthful direction badge
 * is a derived one. A connection nobody has wired yet says so.
 */
function DirectionBadges({
  directions,
}: {
  directions: Array<"ingest" | "deliver">;
}) {
  if (directions.length === 0) {
    return (
      <Badge variant="outline" className="text-[11px] text-muted-foreground">
        not wired
      </Badge>
    );
  }
  return (
    <>
      {directions.includes("ingest") && (
        <Badge variant="outline" className="gap-1 text-[11px]">
          <ArrowDownToLine className="h-3 w-3" />
          ingest source
        </Badge>
      )}
      {directions.includes("deliver") && (
        <Badge variant="outline" className="gap-1 text-[11px]">
          <ArrowUpFromLine className="h-3 w-3" />
          distribution destination
        </Badge>
      )}
    </>
  );
}

function ConnectionCard({
  connection,
  directions,
  onEdit,
  onDelete,
}: {
  connection: Connection;
  /** Derived from the association list — see DirectionBadges. */
  directions: Array<"ingest" | "deliver">;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [testing, setTesting] = useState(false);
  const resetMutation = useResetHostKey();
  const qc = useQueryClient();
  const ssh = isSshFamily(connection.protocol);

  const handleTest = async () => {
    setTesting(true);
    try {
      const result = await runConnectionTest(connection.id);
      const latency =
        result.latencyMs !== undefined ? ` (${result.latencyMs} ms)` : "";
      if (result.ok) {
        toast.success(`${connection.name}: ${result.message}${latency}`);
      } else {
        toast.error(`${connection.name}: ${result.message}${latency}`);
      }
    } catch (err) {
      toast.error(
        `Test failed: ${err instanceof Error ? err.message : "Unknown error"}`,
      );
    } finally {
      setTesting(false);
      // The drain job updates status/last_error/host_key on the row; refresh
      // the list so the badge and fingerprint reflect the test outcome.
      void qc.invalidateQueries({ queryKey: connectionKeys.all() });
    }
  };

  const handleResetHostKey = () => {
    resetMutation.mutate(connection.id, {
      onSuccess: () =>
        toast.success(`Host key pin cleared for ${connection.name}`),
      onError: (err) => toast.error(`Reset failed: ${err.message}`),
    });
  };

  return (
    <Card data-testid={`connection-card-${connection.id}`}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base">{connection.name}</CardTitle>
              <Badge variant="outline" className="tech text-[11px] uppercase">
                {connection.protocol}
              </Badge>
              <DirectionBadges directions={directions} />
              <StatusChip status={connection.status} />
              {!connection.enabled && (
                <Badge variant="secondary">Disabled</Badge>
              )}
            </div>
            {connection.description && (
              <CardDescription className="mt-1">
                {connection.description}
              </CardDescription>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={onEdit}
              aria-label="Edit connection"
            >
              <Pencil className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={onDelete}
              aria-label="Delete connection"
            >
              <Trash2 className="h-4 w-4 text-destructive" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge variant={connection.credentials_set ? "outline" : "secondary"}>
            {connection.credentials_set ? "Credentials set" : "No credentials"}
          </Badge>
          <span className="font-mono text-muted-foreground">
            group: {connection.group_id}
          </span>
        </div>

        {connection.host_key && (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <KeyRound className="h-3.5 w-3.5 shrink-0" />
            <span className="font-mono break-all">
              {connection.host_key.fingerprint}
            </span>
          </div>
        )}

        {connection.last_error && (
          <p className="text-xs text-destructive">{connection.last_error}</p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleTest}
            disabled={testing}
          >
            {testing ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Plug className="mr-1.5 h-3.5 w-3.5" />
            )}
            Test
          </Button>
          {ssh && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleResetHostKey}
              disabled={resetMutation.isPending || !connection.host_key}
            >
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              Reset host key
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ConnectionsInner() {
  const { data: auth } = useAuthMe();
  const {
    data: connections,
    isLoading,
    isError,
    error,
    refetch,
  } = useConnections();
  const { data: flows } = useFlows();
  const deleteMutation = useDeleteConnection();

  const [filter, setFilter] = useState<DirectionFilter>("all");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Connection | undefined>();
  const [deleteTarget, setDeleteTarget] = useState<Connection | null>(null);
  const deleteImpact = useConnectionDeleteImpact(deleteTarget?.id ?? null);

  const groups =
    auth?.authenticated && auth.identity ? auth.identity.groups : [];

  const handleDelete = () => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        toast.success(`Deleted connection: ${deleteTarget.name}`);
        setDeleteTarget(null);
      },
      onError: (err) => toast.error(`Delete failed: ${err.message}`),
    });
  };

  // Direction comes from the associations, not the connection row.
  const directionsById = new Map<string, Array<"ingest" | "deliver">>();
  for (const flow of flows ?? []) {
    const list = directionsById.get(flow.connection_id) ?? [];
    if (!list.includes(flow.direction)) list.push(flow.direction);
    directionsById.set(flow.connection_id, list);
  }

  const visible = (connections ?? []).filter((conn) => {
    if (filter === "all") return true;
    return (directionsById.get(conn.id) ?? []).includes(filter);
  });

  return (
    <>
      <main className="w-full max-w-4xl flex-1 p-6 mx-auto">
        <div className="mb-5 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Connections</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Endpoints a product ingests from, or distributes to
            </p>
          </div>
          <Button
            onClick={() => {
              setEditing(undefined);
              setFormOpen(true);
            }}
          >
            <Plus className="mr-1.5 h-4 w-4" />
            Add Connection
          </Button>
        </div>

        {/* Direction-first framing (ADR 0017): the filter is over how each
            connection is actually WIRED, which is the only place direction
            exists. */}
        <div
          role="radiogroup"
          aria-label="Filter by direction"
          className="mb-5 inline-flex rounded-md border border-border p-0.5"
        >
          {DIRECTION_FILTERS.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={filter === value}
              onClick={() => setFilter(value)}
              className={`rounded-sm px-3 py-1.5 text-[12.5px] font-semibold transition-colors ${
                filter === value
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {isLoading ? (
          <LoadingState message="Loading connections…" />
        ) : isError ? (
          <ErrorState
            message={
              error instanceof Error
                ? error.message
                : "Failed to load connections"
            }
            onRetry={() => refetch()}
          />
        ) : !connections || connections.length === 0 ? (
          <EmptyState
            icon={Plug}
            title="No connections yet"
            description="Add an SFTP, FTP, FTPS, SSH, or S3 endpoint for the pipeline to ingest from or deliver to."
            action={{
              label: "Add Connection",
              onClick: () => {
                setEditing(undefined);
                setFormOpen(true);
              },
            }}
          />
        ) : visible.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No connections are wired as{" "}
            {filter === "ingest" ? "ingest sources" : "distribution destinations"}{" "}
            yet.
          </p>
        ) : (
          <div className="grid gap-4">
            {visible.map((conn) => (
              <ConnectionCard
                key={conn.id}
                connection={conn}
                directions={directionsById.get(conn.id) ?? []}
                onEdit={() => {
                  setEditing(conn);
                  setFormOpen(true);
                }}
                onDelete={() => setDeleteTarget(conn)}
              />
            ))}
          </div>
        )}

        {formOpen && (
          <ConnectionForm
            open={formOpen}
            onOpenChange={(open) => {
              setFormOpen(open);
              if (!open) setEditing(undefined);
            }}
            initial={editing}
            groups={groups}
          />
        )}

        <Dialog
          open={!!deleteTarget}
          onOpenChange={(open) => !open && setDeleteTarget(null)}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete Connection</DialogTitle>
              <DialogDescription>
                Delete "{deleteTarget?.name}"? Its stored credentials are
                scrubbed immediately and its data flows stop. This cannot be
                undone.
              </DialogDescription>
            </DialogHeader>
            <div className="text-sm space-y-1.5">
              {deleteImpact.isLoading ? (
                <p className="text-muted-foreground">Calculating impact…</p>
              ) : deleteImpact.data ? (
                <>
                  {(deleteImpact.data.associations.ingest > 0 ||
                    deleteImpact.data.associations.deliver > 0) && (
                    <p>
                      {deleteImpact.data.associations.ingest} ingest and{" "}
                      {deleteImpact.data.associations.deliver} delivery{" "}
                      association(s) will stop.
                    </p>
                  )}
                  {deleteImpact.data.reference_items.map((r) => (
                    <p key={r.collection_id} className="text-destructive">
                      {r.items} reference-backed item(s) in "{r.collection_id}"
                      will be removed from the catalog — their bytes live at
                      this connection's source.
                    </p>
                  ))}
                  <p className="text-muted-foreground">
                    Ingest and delivery history is retained (
                    {deleteImpact.data.history.ingest_files} file records,{" "}
                    {deleteImpact.data.history.delivery_log} delivery records).
                  </p>
                </>
              ) : deleteImpact.isError ? (
                <p className="text-muted-foreground">
                  Could not calculate the deletion impact.
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setDeleteTarget(null)}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
              >
                Delete
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </main>
    </>
  );
}

export function ConnectionsPage() {
  return (
    <AppShell>
      <ConnectionsInner />
    </AppShell>
  );
}
