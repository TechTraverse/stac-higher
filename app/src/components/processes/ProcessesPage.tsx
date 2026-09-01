import { useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import {
  Badge,
  Button,
  Card,
  CardContent,
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
import { healthDotClass, type LineageHealth } from "@stac-higher/shared";
import { toast } from "sonner";
import { useAuthMe } from "@/lib/query/auth";
import { timeAgo } from "@/components/monitoring/shared";
import { RunSparkline } from "@/components/processes/RunSparkline";
import {
  formatDuration,
  processVerdict,
  realRuns,
  runDurationMs,
  successRateOverRuns,
  triggerSummary,
} from "@/components/processes/health";
import { useOutputs, useRuns, useSources } from "@/lib/processes/queries";
import {
  useCreateProcess,
  useDeleteProcess,
  useProcesses,
} from "@/lib/processes/queries";
import type { Process } from "@/lib/processes/types";

const HEALTH_VAR: Record<LineageHealth, string> = {
  ok: "success",
  warn: "warning",
  error: "danger",
  unknown: "border",
};

const HEALTH_TEXT: Record<LineageHealth, string> = {
  ok: "text-success",
  warn: "text-warning",
  error: "text-danger",
  unknown: "text-muted-foreground",
};

/**
 * `/processes` — the M5-A dashboard (spec §10).
 *
 * Slice 1 shows what the app knows without the run ledger: deploy state, the
 * §7 ceiling, and enablement. The health rollup and run sparkline arrive with
 * M5-E/M5-F, which is when `flow_stats` and `flow_stats_daily` start being
 * written — showing an empty chart now would read as "no runs" rather than
 * "not measured yet".
 */
/**
 * One process at dashboard altitude (mockup 05): what it is, what it reads and
 * writes, when it last ran and how it has been doing.
 *
 * ONE request per card (`useRuns`, polling off — see the hook): the run ledger
 * answers status, last run, duration and the sparkline together. The 30-day
 * daily strip is NOT here — it is per-source and belongs on the detail page,
 * where the source it describes is visible.
 */
function ProcessLinks({
  label,
  ids,
}: {
  label: string;
  ids: string[];
}) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="w-8 shrink-0 text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
        {label}
      </span>
      {ids.length === 0 ? (
        <span className="text-[12px] text-muted-foreground/60">none</span>
      ) : (
        <span className="flex flex-wrap gap-x-2 gap-y-0.5">
          {ids.map((id) => (
            <a
              key={id}
              href={`/collections/${encodeURIComponent(id)}`}
              className="tech text-[11.5px] text-primary hover:underline"
            >
              {id}
            </a>
          ))}
        </span>
      )}
    </div>
  );
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
  const { data: sources } = useSources(process.id);
  const { data: outputs } = useOutputs(process.id);
  const { data: runs } = useRuns(process.id, { poll: false });

  const ledger = realRuns(runs);
  const verdict = processVerdict(process, runs, sources?.length);
  const { rate, counted } = successRateOverRuns(ledger);
  const last = ledger.find((r) => r.started_at !== null) ?? ledger[0] ?? null;
  const trigger = triggerSummary(sources);

  return (
    <Card
      data-testid={`process-card-${process.id}`}
      className="border-l-2"
      style={{ borderLeftColor: `var(--color-${HEALTH_VAR[verdict.health]})` }}
    >
      <CardContent className="px-5 py-4">
        <div className="flex flex-wrap items-start gap-x-6 gap-y-4">
          {/* identity + status */}
          <div className="min-w-0 flex-1 basis-56">
            <div className="flex flex-wrap items-center gap-2">
              <a
                href={`/processes/${process.id}`}
                className="truncate text-[15px] font-bold hover:underline"
              >
                {process.name}
              </a>
            </div>
            <p
              className={`mt-0.5 text-[12px] text-muted-foreground ${trigger.mono ? "tech" : ""}`}
            >
              {trigger.text}
            </p>
            <div className="mt-1.5 flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className={`h-2 w-2 shrink-0 rounded-full ${healthDotClass(verdict.health)}`}
              />
              <span
                className={`text-[12.5px] font-semibold ${HEALTH_TEXT[verdict.health]}`}
              >
                {verdict.label}
              </span>
              {verdict.reason && (
                <span className="truncate text-[11.5px] text-muted-foreground">
                  · {verdict.reason}
                </span>
              )}
            </div>
          </div>

          {/* wiring */}
          <div className="min-w-0 flex-1 basis-56 space-y-1">
            <ProcessLinks
              label="in"
              ids={(sources ?? []).map((s) => s.collection_id)}
            />
            <ProcessLinks
              label="out"
              ids={(outputs ?? []).map((o) => o.collection_id)}
            />
          </div>

          {/* recency + rate */}
          <div className="min-w-0 basis-44 text-[12.5px]">
            <div>
              <span className="text-muted-foreground">Last run </span>
              <span className="font-semibold">
                {last ? timeAgo(last.started_at ?? last.created_at) : "never"}
              </span>
            </div>
            {last && (
              <div className="tech text-[11.5px] text-muted-foreground">
                {formatDuration(runDurationMs(last))}
              </div>
            )}
            <div className="mt-1 text-[11.5px] text-muted-foreground">
              {rate === null
                ? "no completed runs"
                : `${rate.toFixed(1)}% success · last ${counted}`}
            </div>
          </div>

          {/* history */}
          <div className="shrink-0">
            <RunSparkline runs={ledger} />
          </div>

          {canMutate && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 shrink-0 px-2 text-muted-foreground hover:text-destructive"
              onClick={onDelete}
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span className="sr-only">Delete {process.name}</span>
            </Button>
          )}
        </div>

        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t border-border pt-2.5 text-[11.5px] text-muted-foreground">
          <span>
            Group <span className="tech">{process.group_id}</span>
          </span>
          <span>Ceiling {process.max_runs_per_hour} runs/hour</span>
          {process.description && (
            <span className="min-w-0 truncate">{process.description}</span>
          )}
        </div>
      </CardContent>
    </Card>
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
              ? "Create one, deploy a revision, then attach a source product."
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
    <AppShell>
      <ProcessesContent />
    </AppShell>
  );
}
