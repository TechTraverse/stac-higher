import { useEffect, useState } from "react";
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
  ErrorState,
  Input,
  Label,
  LoadingState,
  Switch,
  Textarea,
} from "@stac-higher/shared";
import { Loader2, Play, RotateCcw, Rocket, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useAuthMe } from "@/lib/query/auth";
import { getTestRun, requestTestRun } from "@/lib/processes/api";
import {
  useCreateOutput,
  useCreateSource,
  useDeleteOutput,
  useDeleteSource,
  useDeployRevision,
  useOutputs,
  useProcess,
  useRerunRun,
  useRevisions,
  useRuns,
  useSources,
  useUpdateProcess,
} from "@/lib/processes/queries";
import type { ProcessCheck, ProcessRun } from "@/lib/processes/types";

/**
 * `/processes/[id]` — the M5-A editor (spec §10).
 *
 * The code editor is a PLAIN TEXTAREA on purpose (P9-C): slice 1 adds no
 * editor dependency, and CodeMirror 6 is the recorded upgrade path if
 * operators ask for one (Monaco never). Everything here writes through the
 * audited routes; nothing executes locally.
 */

const STARTER_CODE = `# Runs in an isolated container (ADR 0013).
# Trigger items arrive as input; write outputs to the run's staging prefix.

def main(items):
    for item in items:
        print(item["id"])
`;

function useCanMutate(groupId: string | undefined) {
  const { data: me } = useAuthMe();
  const roles = me?.identity?.roles ?? [];
  const groups = me?.identity?.groups ?? [];
  const operator = roles.includes("operator") || roles.includes("admin");
  const inGroup =
    roles.includes("admin") || (groupId !== undefined && groups.includes(groupId));
  return operator && inGroup;
}

// ---------------------------------------------------------------------------
// settings
// ---------------------------------------------------------------------------

function SettingsCard({
  id,
  name,
  description,
  enabled,
  maxRunsPerHour,
  canMutate,
}: {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  maxRunsPerHour: number;
  canMutate: boolean;
}) {
  const [form, setForm] = useState({ name, description, maxRunsPerHour, enabled });
  const updateMutation = useUpdateProcess();

  // Re-sync when the query refetches (a deploy bumps updated_at), but never
  // clobber a field the operator is mid-edit on: the effect keys on the
  // SAVED values, so it only runs when the server's copy actually changed.
  useEffect(() => {
    setForm({ name, description, maxRunsPerHour, enabled });
  }, [name, description, maxRunsPerHour, enabled]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      await updateMutation.mutateAsync({
        id,
        input: {
          name: form.name,
          description: form.description,
          enabled: form.enabled,
          max_runs_per_hour: form.maxRunsPerHour,
        },
      });
      toast.success("Saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Settings</CardTitle>
        <CardDescription>
          The run-rate ceiling caps how often this process may be triggered.
          Breaching it defers runs rather than dropping them.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={save} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              value={form.name}
              disabled={!canMutate}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="description">Description</Label>
            <Input
              id="description"
              value={form.description}
              disabled={!canMutate}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ceiling">Max runs per hour</Label>
            <Input
              id="ceiling"
              type="number"
              min={1}
              value={form.maxRunsPerHour}
              disabled={!canMutate}
              onChange={(e) =>
                setForm({ ...form, maxRunsPerHour: Number(e.target.value) })
              }
            />
          </div>
          <div className="flex items-center gap-2">
            <Switch
              id="enabled"
              checked={form.enabled}
              disabled={!canMutate}
              onCheckedChange={(checked) =>
                setForm({ ...form, enabled: checked })
              }
            />
            <Label htmlFor="enabled">Enabled</Label>
          </div>
          {canMutate && (
            <div>
              <Button type="submit" disabled={updateMutation.isPending}>
                {updateMutation.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                Save
              </Button>
            </div>
          )}
        </form>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// code + deploy
// ---------------------------------------------------------------------------

function CodeCard({
  id,
  currentCode,
  currentRevision,
  canMutate,
}: {
  id: string;
  currentCode: string | null;
  currentRevision: string | null;
  canMutate: boolean;
}) {
  const [code, setCode] = useState(currentCode ?? STARTER_CODE);
  const [memoryMb, setMemoryMb] = useState(512);
  const [timeoutSeconds, setTimeoutSeconds] = useState(900);
  const deployMutation = useDeployRevision();

  useEffect(() => {
    if (currentCode !== null) setCode(currentCode);
  }, [currentCode]);

  const deploy = async () => {
    try {
      await deployMutation.mutateAsync({
        id,
        input: {
          runtime: {
            kind: "inline_python",
            image: null,
            memory_mb: memoryMb,
            timeout_seconds: timeoutSeconds,
            retry: { max_attempts: 3, backoff: "exponential" },
          },
          code,
          env: [],
        },
      });
      toast.success("Deployed a new revision");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Deploy failed");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Code</CardTitle>
        <CardDescription>
          Python, run on the platform executor image. Deploying creates an
          immutable revision and makes it current; every run pins the revision
          that executed it.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <Textarea
          aria-label="Process code"
          value={code}
          disabled={!canMutate}
          onChange={(e) => setCode(e.target.value)}
          className="font-mono text-sm min-h-64"
          spellCheck={false}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="grid gap-2">
            <Label htmlFor="memory">Memory (MB)</Label>
            <Input
              id="memory"
              type="number"
              min={128}
              value={memoryMb}
              disabled={!canMutate}
              onChange={(e) => setMemoryMb(Number(e.target.value))}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="timeout">Timeout (seconds)</Label>
            <Input
              id="timeout"
              type="number"
              min={1}
              max={86400}
              value={timeoutSeconds}
              disabled={!canMutate}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
            />
          </div>
        </div>
        {canMutate && (
          <div className="flex items-center gap-3">
            <Button onClick={deploy} disabled={deployMutation.isPending}>
              {deployMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Rocket className="h-4 w-4" />
              )}
              Deploy revision
            </Button>
            {currentRevision && (
              <span className="text-sm text-muted-foreground">
                Current: {currentRevision.slice(0, 8)}
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// sources / outputs
// ---------------------------------------------------------------------------

function SourcesCard({ id, canMutate }: { id: string; canMutate: boolean }) {
  const { data: sources, isLoading } = useSources(id);
  const [collectionId, setCollectionId] = useState("");
  const [kind, setKind] = useState<"item_event" | "cron">("item_event");
  const [schedule, setSchedule] = useState("*/15 * * * *");
  const createMutation = useCreateSource();
  const deleteMutation = useDeleteSource();

  const add = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      await createMutation.mutateAsync({
        id,
        input: {
          collection_id: collectionId,
          trigger:
            kind === "cron"
              ? { kind: "cron", schedule }
              : { kind: "item_event", item_filter: null },
          expectation: null,
          enabled: true,
        },
      });
      setCollectionId("");
      toast.success("Source attached");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not attach");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sources</CardTitle>
        <CardDescription>
          What triggers a run: items landing in a collection, or a schedule.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {isLoading && <LoadingState message="Loading sources…" />}
        {sources?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No sources yet — this process will never trigger.
          </p>
        )}
        {sources?.map((source) => (
          <div
            key={source.id}
            className="flex items-center justify-between gap-4 rounded-md border border-border p-3"
          >
            <div className="min-w-0">
              <div className="font-medium">{source.collection_id}</div>
              <div className="text-sm text-muted-foreground">
                {String(source.trigger.kind) === "cron"
                  ? `cron: ${String(source.trigger.schedule)}`
                  : "on new or updated items"}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {!source.enabled && <Badge variant="outline">Disabled</Badge>}
              {canMutate && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    deleteMutation.mutate({ id, sourceId: source.id })
                  }
                >
                  <Trash2 className="h-4 w-4" />
                  <span className="sr-only">
                    Detach {source.collection_id}
                  </span>
                </Button>
              )}
            </div>
          </div>
        ))}

        {canMutate && (
          <form onSubmit={add} className="grid gap-3 border-t border-border pt-4">
            <div className="grid gap-2">
              <Label htmlFor="source-collection">Source collection</Label>
              <Input
                id="source-collection"
                value={collectionId}
                onChange={(e) => setCollectionId(e.target.value)}
                placeholder="sentinel-2-l2a"
                required
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="trigger-kind">Trigger</Label>
              <select
                id="trigger-kind"
                className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                value={kind}
                onChange={(e) =>
                  setKind(e.target.value as "item_event" | "cron")
                }
              >
                <option value="item_event">On new or updated items</option>
                <option value="cron">On a schedule</option>
              </select>
            </div>
            {kind === "cron" && (
              <div className="grid gap-2">
                <Label htmlFor="schedule">Schedule (cron)</Label>
                <Input
                  id="schedule"
                  value={schedule}
                  onChange={(e) => setSchedule(e.target.value)}
                  placeholder="*/15 * * * *"
                />
              </div>
            )}
            <div>
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                Attach source
              </Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function OutputsCard({ id, canMutate }: { id: string; canMutate: boolean }) {
  const { data: outputs, isLoading } = useOutputs(id);
  const [collectionId, setCollectionId] = useState("");
  const createMutation = useCreateOutput();
  const deleteMutation = useDeleteOutput();

  const add = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      await createMutation.mutateAsync({ id, collectionId });
      setCollectionId("");
      toast.success("Output attached");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not attach");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Outputs</CardTitle>
        <CardDescription>
          Where validated items land. Detaching an output stops future
          publishing; items already published stay where they are.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {isLoading && <LoadingState message="Loading outputs…" />}
        {outputs?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No output collection yet — runs would have nowhere to publish.
          </p>
        )}
        {outputs?.map((output) => (
          <div
            key={output.id}
            className="flex items-center justify-between gap-4 rounded-md border border-border p-3"
          >
            <span className="font-medium">{output.collection_id}</span>
            {canMutate && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  deleteMutation.mutate({ id, outputId: output.id })
                }
              >
                <Trash2 className="h-4 w-4" />
                <span className="sr-only">Detach {output.collection_id}</span>
              </Button>
            )}
          </div>
        ))}

        {canMutate && (
          <form onSubmit={add} className="grid gap-3 border-t border-border pt-4">
            <div className="grid gap-2">
              <Label htmlFor="output-collection">Output collection</Label>
              <Input
                id="output-collection"
                value={collectionId}
                onChange={(e) => setCollectionId(e.target.value)}
                placeholder="cloud-masks"
                required
              />
            </div>
            <div>
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                Attach output
              </Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// test run (ADR 0004 bridge)
// ---------------------------------------------------------------------------

const TERMINAL_CHECK: ProcessCheck["status"][] = ["done", "failed"];

function TestRunCard({
  id,
  hasRevision,
  canMutate,
}: {
  id: string;
  hasRevision: boolean;
  canMutate: boolean;
}) {
  const [check, setCheck] = useState<ProcessCheck | null>(null);
  const [requesting, setRequesting] = useState(false);

  // Poll while the request is outstanding. The app never learns a result
  // directly — the pipeline claims the row and writes it back (ADR 0004) —
  // so polling is the only way to observe one, and it stops at a terminal
  // status rather than running forever.
  useEffect(() => {
    if (!check || TERMINAL_CHECK.includes(check.status)) return;
    const timer = setInterval(async () => {
      try {
        setCheck(await getTestRun(id, check.id));
      } catch {
        // A transient poll failure is not worth a toast; the next tick
        // retries and the status stays visible.
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [id, check]);

  const start = async () => {
    setRequesting(true);
    try {
      setCheck(await requestTestRun(id));
      toast.success("Test run requested");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not start");
    } finally {
      setRequesting(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Test run</CardTitle>
        <CardDescription>
          Runs the current revision once, in the same isolated container a
          triggered run uses.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {canMutate && (
          <div>
            <Button onClick={start} disabled={requesting || !hasRevision}>
              {requesting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              Run once
            </Button>
          </div>
        )}
        {!hasRevision && (
          <p className="text-sm text-muted-foreground">
            Deploy a revision first — there is nothing to run yet.
          </p>
        )}
        {check && (
          <div className="text-sm">
            <span className="text-muted-foreground">Status: </span>
            <Badge
              variant={check.status === "failed" ? "destructive" : "secondary"}
            >
              {check.status}
            </Badge>
            {check.result !== null && (
              <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 text-xs">
                {JSON.stringify(check.result, null, 2)}
              </pre>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// runs
// ---------------------------------------------------------------------------

const RUN_STATUS_VARIANT: Record<
  ProcessRun["status"],
  "default" | "secondary" | "destructive" | "outline"
> = {
  queued: "secondary",
  running: "secondary",
  succeeded: "default",
  failed: "outline",
  dead: "destructive",
};

function RunRow({
  run,
  processId,
  canMutate,
}: {
  run: ProcessRun;
  processId: string;
  canMutate: boolean;
}) {
  const rerunMutation = useRerunRun();

  const rerun = async () => {
    try {
      await rerunMutation.mutateAsync({ id: processId, runId: run.id });
      toast.success("Re-run queued");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not re-run");
    }
  };

  return (
    <div className="flex items-start justify-between gap-4 rounded-md border border-border p-3">
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-2">
          <Badge variant={RUN_STATUS_VARIANT[run.status]}>{run.status}</Badge>
          {run.is_test && <Badge variant="outline">test</Badge>}
          {run.rate_deferred_until && (
            // The §7 ceiling is holding this run back. Saying so beats a
            // run that silently sits in `queued` looking stuck.
            <Badge variant="outline">rate limited</Badge>
          )}
          <span className="text-sm text-muted-foreground">
            {new Date(run.created_at).toLocaleString()}
          </span>
        </div>
        <div className="text-sm text-muted-foreground">
          {run.input_items.length} input item
          {run.input_items.length === 1 ? "" : "s"} · attempt {run.attempts}
          {run.output_items.length > 0 &&
            ` · ${run.output_items.length} published`}
        </div>
        {run.error && (
          <p className="text-sm text-destructive break-words">{run.error}</p>
        )}
      </div>
      {canMutate && run.status === "dead" && (
        <Button
          variant="outline"
          size="sm"
          onClick={rerun}
          disabled={rerunMutation.isPending}
        >
          {rerunMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RotateCcw className="h-4 w-4" />
          )}
          Re-run
        </Button>
      )}
    </div>
  );
}

function RunsCard({ id, canMutate }: { id: string; canMutate: boolean }) {
  const { data: runs, isLoading } = useRuns(id);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent runs</CardTitle>
        <CardDescription>
          Each run pins the revision that executed it. A dead run can be
          re-run — it re-executes that same revision, not whatever is current.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {isLoading && <LoadingState message="Loading runs…" />}
        {runs?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No runs yet. Attach a source, or use the test run below.
          </p>
        )}
        {runs?.map((run) => (
          <RunRow key={run.id} run={run} processId={id} canMutate={canMutate} />
        ))}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------

function ProcessDetailContent({ id }: { id: string }) {
  const { data: process, isLoading, error, refetch } = useProcess(id);
  const { data: revisions } = useRevisions(id);
  const canMutate = useCanMutate(process?.group_id);

  if (isLoading) return <LoadingState message="Loading process…" />;
  if (error) {
    return (
      <ErrorState
        message={error instanceof Error ? error.message : "Failed to load"}
        onRetry={() => refetch()}
      />
    );
  }
  if (!process) return null;

  const current =
    revisions?.find((revision) => revision.id === process.current_revision) ??
    null;

  return (
    <div className="container mx-auto px-4 py-8 space-y-6">
      <div>
        <a
          href="/processes"
          className="text-sm text-muted-foreground hover:underline"
        >
          ← Processes
        </a>
        <h1 className="text-3xl font-bold">{process.name}</h1>
        <p className="text-muted-foreground">
          {process.description || "No description"}
        </p>
      </div>

      <SettingsCard
        id={process.id}
        name={process.name}
        description={process.description}
        enabled={process.enabled}
        maxRunsPerHour={process.max_runs_per_hour}
        canMutate={canMutate}
      />
      <CodeCard
        id={process.id}
        currentCode={current?.code ?? null}
        currentRevision={process.current_revision}
        canMutate={canMutate}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <SourcesCard id={process.id} canMutate={canMutate} />
        <OutputsCard id={process.id} canMutate={canMutate} />
      </div>
      <TestRunCard
        id={process.id}
        hasRevision={process.current_revision !== null}
        canMutate={canMutate}
      />
      <RunsCard id={process.id} canMutate={canMutate} />
    </div>
  );
}

export function ProcessDetailPage({ id }: { id: string }) {
  return (
    <QueryProvider>
      <Header />
      <ProcessDetailContent id={id} />
    </QueryProvider>
  );
}
