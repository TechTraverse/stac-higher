import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
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
} from "@stac-higher/shared";
import { FileText, Loader2, Package, Play, RotateCcw, Rocket, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { EnvEditor } from "@/components/processes/EnvEditor";
import { CodeEditor } from "@/components/processes/CodeEditor";
import { FlowStrip } from "@/components/monitoring/FlowStrip";
import { useFlowHistory } from "@/lib/monitoring/graph-queries";
import { processVerdict } from "@/components/processes/health";
import { healthDotClass, type LineageHealth } from "@stac-higher/shared";
import { useConnections } from "@/lib/connections/queries";
import { useAuthMe } from "@/lib/query/auth";
import { getTestRun, requestTestRun } from "@/lib/processes/api";
import { useBuiltinExtractors } from "@/lib/extractors/queries";
import {
  useCreateOutput,
  useCreateSource,
  useDeleteOutput,
  useDeleteSource,
  useDeployBuiltinRevision,
  useDeployRevision,
  useOutputs,
  useProcess,
  useRerunRun,
  useRevisions,
  useRuns,
  useSources,
  useUpdateProcess,
} from "@/lib/processes/queries";
import {
  PROCESS_NETWORK_LEVELS,
  type NetworkLevel,
  type ProcessEnv,
  type ProcessKind,
} from "@/lib/processes/schemas";
import {
  DEFAULT_NETWORK_MAX,
  getNetworkMax,
  networkLevelWithinCap,
} from "@/lib/processes/network";
import type {
  Process,
  ProcessCheck,
  ProcessRun,
  ProcessSource,
} from "@/lib/processes/types";

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

/**
 * Network profile options (GOES spec §4). Slice 1 realises `isolated` only:
 * the other levels are shown, disabled, so the operator can see what is
 * coming — and the cap (`PUBLIC_PROCESS_NETWORK_MAX`, the UI mirror of the
 * pipeline's `PROCESS_NETWORK_MAX`) is read now so nothing changes shape
 * when the egress proxy lands.
 */
const NETWORK_LEVEL_LABELS: Record<NetworkLevel, string> = {
  isolated: "Isolated (platform storage only)",
  inputs: "Inputs (hosts of the input assets)",
  hosts: "Named hosts",
  open: "Open internet",
};

/** The write gate stores only `isolated` this slice (see `schemas.ts`). */
const NETWORK_LEVELS_AVAILABLE: readonly NetworkLevel[] = ["isolated"];

function readUiNetworkMax(): NetworkLevel {
  try {
    return getNetworkMax({
      PROCESS_NETWORK_MAX: import.meta.env.PUBLIC_PROCESS_NETWORK_MAX as
        | string
        | undefined,
    });
  } catch {
    // A misconfigured mirror must not break the page; the pipeline enforces
    // the real cap at launch regardless of what the form offered.
    return DEFAULT_NETWORK_MAX;
  }
}

export function CodeCard({
  id,
  groupId,
  currentCode,
  currentEnv,
  currentRevision,
  canMutate,
  kind,
}: {
  id: string;
  groupId: string;
  currentCode: string | null;
  currentEnv: ProcessEnv;
  currentRevision: string | null;
  canMutate: boolean;
  kind: ProcessKind;
}) {
  const [code, setCode] = useState(currentCode ?? STARTER_CODE);
  const [env, setEnv] = useState<ProcessEnv>(currentEnv);
  const [memoryMb, setMemoryMb] = useState(512);
  // GOES spec §6.5: an extractor runs against one ingested file rather than a
  // batch, so it defaults to a much shorter timeout than a transform.
  const [timeoutSeconds, setTimeoutSeconds] = useState(
    kind === "extractor" ? 120 : 900,
  );
  const [networkLevel, setNetworkLevel] = useState<NetworkLevel>("isolated");
  const networkMax = readUiNetworkMax();
  const deployMutation = useDeployRevision();
  // Every connection the caller can see; EnvEditor narrows to the PROCESS's
  // group, which is the scope a secret_ref may name.
  const { data: connections } = useConnections();

  useEffect(() => {
    if (currentCode !== null) setCode(currentCode);
  }, [currentCode]);

  // A deploy starts from what is deployed: re-sync when the current revision
  // moves, so the form is an edit of the live env rather than a blank slate
  // that would silently drop it.
  useEffect(() => {
    setEnv(currentEnv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRevision]);

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
            // Slice 1: `hosts` is only meaningful at the `hosts` level, which
            // the write gate does not accept yet.
            network: { level: networkLevel, hosts: [] },
            // Hand-written code runs on the base platform image; the
            // `stactools` alias is what X-4's built-in template deploys
            // (X-queue spec §8).
            runtime_image: "default",
          },
          code,
          env,
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
        <CodeEditor
          ariaLabel="Process code"
          value={code}
          onChange={setCode}
          language="python"
          disabled={!canMutate}
          minHeight="26rem"
        />
        <EnvEditor
          value={env}
          onChange={setEnv}
          connections={connections ?? []}
          groupId={groupId}
          disabled={!canMutate}
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
        <div className="grid gap-2">
          <Label htmlFor="network-level">Network access</Label>
          {/* Plain <select>, like the env and trigger-kind pickers on this
              page: a small native control with disabled options. */}
          <select
            id="network-level"
            aria-label="Network access"
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm disabled:cursor-not-allowed disabled:opacity-50 sm:max-w-sm"
            value={networkLevel}
            disabled={!canMutate}
            onChange={(e) => setNetworkLevel(e.target.value as NetworkLevel)}
          >
            {PROCESS_NETWORK_LEVELS.map((level) => (
              <option
                key={level}
                value={level}
                disabled={
                  !NETWORK_LEVELS_AVAILABLE.includes(level) ||
                  !networkLevelWithinCap(level, networkMax)
                }
              >
                {NETWORK_LEVEL_LABELS[level]}
              </option>
            ))}
          </select>
          <p className="text-xs text-muted-foreground">
            Slice 1 runs every process isolated; inputs are staged into the run.
            Higher levels arrive with the egress proxy and are enabled per
            deployment (PROCESS_NETWORK_MAX).
          </p>
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
              <Label htmlFor="source-collection">Source product</Label>
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
            No output product yet — runs would have nowhere to publish.
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
              <Label htmlFor="output-collection">Output product</Label>
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
  isExtractor,
}: {
  run: ProcessRun;
  processId: string;
  canMutate: boolean;
  isExtractor: boolean;
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
      <div className="flex shrink-0 items-center gap-2">
        {run.log_ref && (
          // A plain link, not a fetch: the route 302s to a short-lived
          // presigned URL and the log can be 10 MB, so the browser downloads
          // it directly rather than through the app.
          <Button variant="ghost" size="sm" asChild>
            <a
              href={`/api/processes/${processId}/runs/${run.id}/log`}
              target="_blank"
              rel="noreferrer"
            >
              <FileText className="h-4 w-4" />
              Log
            </a>
          </Button>
        )}
      {/* Extractor runs are not re-runnable: their ingest rows are re-driven
          by the failed-retry sweep, and the route refuses the verb (409). */}
      {canMutate && !isExtractor && run.status === "dead" && (
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
    </div>
  );
}

function RunsCard({
  id,
  canMutate,
  isExtractor,
}: {
  id: string;
  canMutate: boolean;
  isExtractor: boolean;
}) {
  const { data: runs, isLoading } = useRuns(id);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent runs</CardTitle>
        <CardDescription>
          Each run pins the revision that executed it.{" "}
          {isExtractor
            ? "A dead extractor run is not re-run from here — its files go back through the ingest retry sweep."
            : "A dead run can be re-run — it re-executes that same revision, not whatever is current."}
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
          <RunRow
            key={run.id}
            run={run}
            processId={id}
            canMutate={canMutate}
            isExtractor={isExtractor}
          />
        ))}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// status + history
// ---------------------------------------------------------------------------

const HEALTH_BADGE: Record<LineageHealth, string> = {
  ok: "border-success-border bg-success-subtle text-success",
  warn: "border-warning-border bg-warning-subtle text-warning",
  error: "border-danger-border bg-danger-subtle text-danger",
  unknown: "border-border bg-muted text-muted-foreground",
};

/** The header verdict, from the same run-ledger derivation the dashboard uses. */
function DeployState({ process }: { process: Process }) {
  const { data: sources } = useSources(process.id);
  const { data: runs } = useRuns(process.id);
  const verdict = processVerdict(process, runs, sources?.length);
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-sm border px-3 py-1 text-[12.5px] font-bold ${HEALTH_BADGE[verdict.health]}`}
    >
      <span
        aria-hidden="true"
        className={`h-2 w-2 rounded-full ${healthDotClass(verdict.health)}`}
      />
      {verdict.label}
      {verdict.reason && (
        <span className="font-medium opacity-80">· {verdict.reason}</span>
      )}
    </span>
  );
}

/**
 * The 30-day daily strip, per SOURCE.
 *
 * It used to sit on the dashboard card showing only the first source, which
 * was honest but ambiguous. `flow_stats_daily` is keyed per source, so this is
 * where it belongs: next to the sources it actually describes, one strip each.
 */
function HistoryCard({ id }: { id: string }) {
  const { data: sources } = useSources(id);
  if (!sources || sources.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Run history</CardTitle>
        <CardDescription>
          Daily rollup per source, 30 days. A hatched cell is a day the rollup
          did not run — distinct from a quiet day.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {sources.map((source) => (
          <SourceHistory key={source.id} source={source} />
        ))}
      </CardContent>
    </Card>
  );
}

function SourceHistory({ source }: { source: ProcessSource }) {
  const { data: history } = useFlowHistory("process", source.id);
  return (
    <div className="flex flex-wrap items-center gap-3">
      <FlowStrip days={history ?? []} metric="runs" label="run history" />
      <span className="tech text-[11.5px] text-muted-foreground">
        {source.collection_id}
      </span>
    </div>
  );
}

/**
 * X-4: a built-in process is read-only — its code is the two-line body that
 * hands the run to the stactools library in the runtime image, and the ONLY
 * way its revision moves is "Update to current": a new revision from the
 * registry the platform currently ships. This card replaces the code editor.
 */
export function BuiltinCard({
  id,
  builtinId,
  currentRevision,
  canMutate,
}: {
  id: string;
  builtinId: string;
  currentRevision: string | null;
  canMutate: boolean;
}) {
  const { data: registry } = useBuiltinExtractors();
  const entry = registry?.find((e) => e.id === builtinId) ?? null;
  const update = useDeployBuiltinRevision();
  const onUpdate = async () => {
    try {
      await update.mutateAsync(id);
      toast.success("Deployed the current built-in revision");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed");
    }
  };
  return (
    <Card data-testid="builtin-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Package className="h-4 w-4" aria-hidden="true" />
          Built-in
        </CardTitle>
        <CardDescription>
          {entry
            ? `${entry.label} — ${entry.package} ${entry.version}, on the stactools runtime image.`
            : "This built-in extractor is no longer in the platform's registry; the deployed revision keeps running, but there is no current template to update to."}
          {" "}
          The code is managed by the platform and read-only: each run hands the
          staged files to the package and merges the item it builds onto the
          draft.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12.5px]">
          <dt className="text-muted-foreground">Registry id</dt>
          <dd className="tech">{builtinId}</dd>
          <dt className="text-muted-foreground">Image</dt>
          <dd className="tech">stactools</dd>
          {entry && (
            <>
              <dt className="text-muted-foreground">Products</dt>
              <dd className="tech">{entry.products.join(", ")}</dd>
              <dt className="text-muted-foreground">Files</dt>
              <dd>{entry.supports === "grouped" ? "one item per group" : "one item per file"}</dd>
            </>
          )}
          <dt className="text-muted-foreground">Revision</dt>
          <dd className="tech">{currentRevision ?? "none"}</dd>
        </dl>
        <pre className="tech overflow-x-auto rounded-sm border bg-muted/40 p-3 text-[12px]">
          {`from stac_higher_stactools import run\nrun(${JSON.stringify(builtinId)})`}
        </pre>
        {canMutate && (
          <div>
            <Button
              size="sm"
              onClick={onUpdate}
              disabled={update.isPending || entry === null}
            >
              {update.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Rocket className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Update to current
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ExtractorCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Extractor</CardTitle>
        <CardDescription>
          This process fixes up items as an ingest source brings them in. It
          is selected on an ingest association's metadata strategy (a
          collection's Data flow tab) and has no trigger sources or output
          collections of its own. Each run receives the draft items in its
          input manifest (<code className="tech">kind: "extract"</code>) and
          writes one <code className="tech">{"{item_id}.json"}</code> per item
          back — id, collection and asset hrefs unchanged.
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

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
    <main className="flex-1 space-y-5 p-6">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0">
          <a
            href="/processes"
            className="text-[12.5px] font-semibold text-primary hover:underline"
          >
            ← Processes
          </a>
          <div className="mt-1 flex flex-wrap items-center gap-2.5">
            <h1 className="text-2xl font-bold tracking-tight">{process.name}</h1>
            <DeployState process={process} />
            {process.kind === "extractor" && (
              <Badge variant="outline">extractor</Badge>
            )}
            {process.builtin_id !== null && (
              <Badge variant="outline">built-in</Badge>
            )}
          </div>
          <p className="tech mt-0.5 text-[11.5px] text-muted-foreground">
            {process.id}
          </p>
          <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
            {process.description || "No description"}
          </p>
        </div>
      </div>

      {/* Editor layout (mockup 06): what the process IS on the left, what it
          RUNS on the right, and what it DID underneath. On narrow viewports it
          stacks back into one column in the same reading order. */}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] xl:items-start">
        <div className="space-y-5">
          <SettingsCard
            id={process.id}
            name={process.name}
            description={process.description}
            enabled={process.enabled}
            maxRunsPerHour={process.max_runs_per_hour}
            canMutate={canMutate}
          />
          {process.kind === "extractor" ? (
            <ExtractorCard />
          ) : (
            <>
              <SourcesCard id={process.id} canMutate={canMutate} />
              <OutputsCard id={process.id} canMutate={canMutate} />
            </>
          )}
        </div>

        <div className="space-y-5 xl:sticky xl:top-20">
          {process.builtin_id !== null ? (
            <BuiltinCard
              id={process.id}
              builtinId={process.builtin_id}
              currentRevision={process.current_revision}
              canMutate={canMutate}
            />
          ) : (
            <CodeCard
              id={process.id}
              groupId={process.group_id}
              currentCode={current?.code ?? null}
              currentEnv={(current?.env ?? []) as ProcessEnv}
              currentRevision={process.current_revision}
              canMutate={canMutate}
              kind={process.kind}
            />
          )}
          <TestRunCard
            id={process.id}
            hasRevision={process.current_revision !== null}
            canMutate={canMutate}
          />
        </div>
      </div>

      <HistoryCard id={process.id} />
      <RunsCard
        id={process.id}
        canMutate={canMutate}
        isExtractor={process.kind === "extractor"}
      />
    </main>
  );
}

export function ProcessDetailPage({ id }: { id: string }) {
  return (
    <AppShell>
      <ProcessDetailContent id={id} />
    </AppShell>
  );
}
