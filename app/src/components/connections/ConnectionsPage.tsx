import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { connectionKeys } from "@/lib/query/keys";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
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
  useConnections,
  useDeleteConnection,
  useResetHostKey,
} from "@/lib/connections/queries";
import { runConnectionTest } from "@/lib/connections/api";
import { isSshFamily } from "@/lib/connections/schemas";
import type { Connection } from "@/lib/connections/types";
import { ConnectionForm } from "./ConnectionForm";

function StatusBadge({ status }: { status: Connection["status"] }) {
  if (status === "ok") return <Badge variant="default">OK</Badge>;
  if (status === "error") return <Badge variant="destructive">Error</Badge>;
  return <Badge variant="secondary">Unverified</Badge>;
}

function ConnectionCard({
  connection,
  onEdit,
  onDelete,
}: {
  connection: Connection;
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
              <Badge variant="outline" className="uppercase">
                {connection.protocol}
              </Badge>
              <StatusBadge status={connection.status} />
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
  const { data: connections, isLoading, isError, error } = useConnections();
  const deleteMutation = useDeleteConnection();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Connection | undefined>();
  const [deleteTarget, setDeleteTarget] = useState<Connection | null>(null);

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

  return (
    <>
      <Header />
      <main className="w-full max-w-4xl flex-1 p-6 mx-auto">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Connections</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Group-owned endpoints the pipeline ingests from and delivers to
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

        {isLoading ? (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Loading connections…
          </div>
        ) : isError ? (
          <Card className="border-destructive/50">
            <CardContent className="py-8 text-center text-sm text-destructive">
              {error instanceof Error
                ? error.message
                : "Failed to load connections"}
            </CardContent>
          </Card>
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
        ) : (
          <div className="grid gap-4">
            {connections.map((conn) => (
              <ConnectionCard
                key={conn.id}
                connection={conn}
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
                Are you sure you want to delete "{deleteTarget?.name}"? This
                cannot be undone.
              </DialogDescription>
            </DialogHeader>
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
    <QueryProvider>
      <ConnectionsInner />
    </QueryProvider>
  );
}
