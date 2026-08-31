import { useState } from "react";
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
  ErrorState,
  Input,
  Label,
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
import { Cpu, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useAuthMe } from "@/lib/query/auth";
import { FlowStrip } from "@/components/monitoring/FlowStrip";
import { useFlowHistory } from "@/lib/monitoring/graph-queries";
import { useSources } from "@/lib/processes/queries";
import {
  useCreateProcess,
  useDeleteProcess,
  useProcesses,
} from "@/lib/processes/queries";
import type { Process } from "@/lib/processes/types";

/**
 * `/processes` — the M5-A dashboard (spec §10).
 *
 * Slice 1 shows what the app knows without the run ledger: deploy state, the
 * §7 ceiling, and enablement. The health rollup and run sparkline arrive with
 * M5-E/M5-F, which is when `flow_stats` and `flow_stats_daily` start being
 * written — showing an empty chart now would read as "no runs" rather than
 * "not measured yet".
 */
function DeployBadge({ process }: { process: Process }) {
  if (!process.current_revision) {
    return <Badge variant="secondary">No revision</Badge>;
  }
  if (!process.enabled) return <Badge variant="outline">Disabled</Badge>;
  return <Badge variant="default">Deployed</Badge>;
}

function ProcessCard({
  process,
  canMutate,
  onDelete,
}: {
  process: Process;
  canMutate: boolean;
  onDelete: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <CardTitle className="flex items-center gap-2">
              <a href={`/processes/${process.id}`} className="hover:underline">
                {process.name}
              </a>
              <DeployBadge process={process} />
            </CardTitle>
            <CardDescription>
              {process.description || "No description"}
            </CardDescription>
          </div>
          {canMutate && (
            <Button variant="ghost" size="sm" onClick={onDelete}>
              <Trash2 className="h-4 w-4" />
              <span className="sr-only">Delete {process.name}</span>
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="text-sm text-muted-foreground flex flex-wrap gap-x-6 gap-y-1">
          <span>Group: {process.group_id}</span>
          <span>Ceiling: {process.max_runs_per_hour} runs/hour</span>
        </div>
        <ProcessSparkline processId={process.id} />
      </CardContent>
    </Card>
  );
}

/**
 * A 30-day run strip for the process's FIRST source.
 *
 * `flow_stats_daily` is keyed per source, and a process usually has one. With
 * several, showing the first is honest at dashboard altitude — the detail
 * page is where per-source telemetry belongs — and summing them would hide a
 * dead source behind a busy sibling, which is the opposite of what a health
 * strip is for.
 */
function ProcessSparkline({ processId }: { processId: string }) {
  const { data: sources } = useSources(processId);
  const first = sources?.[0]?.id ?? null;
  const { data: history } = useFlowHistory("process", first);

  if (!sources || sources.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No source attached — this process never triggers.
      </p>
    );
  }
  return (
    <div className="flex items-center gap-3">
      <FlowStrip days={history ?? []} metric="runs" label="run history" />
      <span className="text-xs text-muted-foreground">
        30 days
        {sources.length > 1 ? ` · ${sources[0].collection_id}` : ""}
      </span>
    </div>
  );
}

function CreateProcessDialog({
  open,
  onOpenChange,
  groups,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  groups: string[];
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [groupId, setGroupId] = useState(groups[0] ?? "");
  const createMutation = useCreateProcess();

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      const created = await createMutation.mutateAsync({
        name,
        description,
        group_id: groupId,
        enabled: true,
        max_runs_per_hour: 60,
      });
      toast.success(`Created ${created.name}`);
      onOpenChange(false);
      // A new process has nothing deployed, so the only useful next step is
      // the editor — go straight there rather than back to an inert card.
      window.location.href = `/processes/${created.id}`;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not create process");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>New process</DialogTitle>
            <DialogDescription>
              Create the process, then deploy a revision to give it something
              to run.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="process-name">Name</Label>
              <Input
                id="process-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="cloud-mask"
                required
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="process-description">Description</Label>
              <Input
                id="process-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What this process produces"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="process-group">Owning group</Label>
              <Input
                id="process-group"
                value={groupId}
                onChange={(e) => setGroupId(e.target.value)}
                required
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending && (
                <Loader2 className="h-4 w-4 animate-spin" />
              )}
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ProcessesContent() {
  const { data: processes, isLoading, error, refetch } = useProcesses();
  const { data: me } = useAuthMe();
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Process | null>(null);
  const deleteMutation = useDeleteProcess();

  const roles = me?.identity?.roles ?? [];
  const canMutate = roles.includes("operator") || roles.includes("admin");
  const groups = me?.identity?.groups ?? [];

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    try {
      await deleteMutation.mutateAsync(pendingDelete.id);
      toast.success(`Deleted ${pendingDelete.name}`);
      setPendingDelete(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not delete");
    }
  };

  return (
    <div className="container mx-auto px-4 py-8 space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Processes</h1>
          <p className="text-muted-foreground">
            User-defined transforms that turn catalog items into new items.
          </p>
        </div>
        {canMutate && (
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" />
            New process
          </Button>
        )}
      </div>

      {isLoading && <LoadingState message="Loading processes…" />}
      {error && (
        <ErrorState
          message={error instanceof Error ? error.message : "Failed to load"}
          onRetry={() => refetch()}
        />
      )}
      {processes && processes.length === 0 && (
        <EmptyState
          icon={Cpu}
          title="No processes yet"
          description={
            canMutate
              ? "Create one, deploy a revision, then attach a source collection."
              : "No processes are visible to your groups."
          }
        />
      )}
      {processes && processes.length > 0 && (
        <div className="grid gap-4">
          {processes.map((process) => (
            <ProcessCard
              key={process.id}
              process={process}
              canMutate={canMutate}
              onDelete={() => setPendingDelete(process)}
            />
          ))}
        </div>
      )}

      <CreateProcessDialog
        open={creating}
        onOpenChange={setCreating}
        groups={groups}
      />

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {pendingDelete?.name}?</DialogTitle>
            <DialogDescription>
              The process stops triggering and disappears from this list. Its
              run history is kept, and the name becomes available again.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirmDelete}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending && (
                <Loader2 className="h-4 w-4 animate-spin" />
              )}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function ProcessesPage() {
  return (
    <QueryProvider>
      <Header />
      <ProcessesContent />
    </QueryProvider>
  );
}
