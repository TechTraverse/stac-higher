/**
 * TanStack Query hooks for processes (M5-A).
 *
 * Mutations invalidate by the `processKeys` prefix so a change refreshes the
 * list and the detail together — a deploy, for instance, moves both the
 * revision list and the parent's `current_revision`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { processKeys } from "@/lib/query/keys";
import {
  createOutput,
  createProcess,
  createSource,
  deleteOutput,
  deleteProcess,
  deleteSource,
  deployRevision,
  getProcess,
  listOutputs,
  listProcesses,
  listRevisions,
  listRuns,
  listSources,
  rerunRun,
  updateProcess,
  updateSource,
} from "./api";
import type {
  ProcessCreate,
  ProcessRevisionCreate,
  ProcessSourceCreate,
  ProcessSourceUpdate,
  ProcessUpdate,
} from "./schemas";

export function useProcesses() {
  return useQuery({ queryKey: processKeys.list(), queryFn: listProcesses });
}

export function useProcess(id: string) {
  return useQuery({
    queryKey: processKeys.detail(id),
    queryFn: () => getProcess(id),
    enabled: !!id,
  });
}

export function useRevisions(id: string) {
  return useQuery({
    queryKey: processKeys.revisions(id),
    queryFn: () => listRevisions(id),
    enabled: !!id,
  });
}

export function useSources(id: string) {
  return useQuery({
    queryKey: processKeys.sources(id),
    queryFn: () => listSources(id),
    enabled: !!id,
  });
}

export function useOutputs(id: string) {
  return useQuery({
    queryKey: processKeys.outputs(id),
    queryFn: () => listOutputs(id),
    enabled: !!id,
  });
}

/**
 * The run ledger for one process.
 *
 * `poll` is on by default because the detail page watches a run it just
 * started. The DASHBOARD turns it off: one poll per process every 10s is a
 * lot of traffic for a list nobody is watching a single run on, and it still
 * refetches on mount and on window focus.
 */
export function useRuns(id: string, { poll = true }: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: processKeys.runs(id),
    queryFn: () => listRuns(id),
    enabled: !!id,
    refetchInterval: poll ? 10_000 : false,
  });
}

/** Every mutation below invalidates the whole process subtree — the surfaces
 * are small and interdependent, so a narrower invalidation would buy nothing
 * but a chance to miss one. */
function useProcessMutation<TArgs, TResult>(
  mutationFn: (args: TArgs) => Promise<TResult>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => qc.invalidateQueries({ queryKey: processKeys.all() }),
  });
}

export function useCreateProcess() {
  return useProcessMutation((input: ProcessCreate) => createProcess(input));
}

export function useUpdateProcess() {
  return useProcessMutation(({ id, input }: { id: string; input: ProcessUpdate }) =>
    updateProcess(id, input),
  );
}

export function useDeleteProcess() {
  return useProcessMutation((id: string) => deleteProcess(id));
}

export function useDeployRevision() {
  return useProcessMutation(
    ({ id, input }: { id: string; input: ProcessRevisionCreate }) =>
      deployRevision(id, input),
  );
}

export function useCreateSource() {
  return useProcessMutation(
    ({ id, input }: { id: string; input: ProcessSourceCreate }) =>
      createSource(id, input),
  );
}

export function useUpdateSource() {
  return useProcessMutation(
    ({
      id,
      sourceId,
      input,
    }: {
      id: string;
      sourceId: string;
      input: ProcessSourceUpdate;
    }) => updateSource(id, sourceId, input),
  );
}

export function useDeleteSource() {
  return useProcessMutation(({ id, sourceId }: { id: string; sourceId: string }) =>
    deleteSource(id, sourceId),
  );
}

export function useCreateOutput() {
  return useProcessMutation(
    ({ id, collectionId }: { id: string; collectionId: string }) =>
      createOutput(id, collectionId),
  );
}

export function useRerunRun() {
  return useProcessMutation(({ id, runId }: { id: string; runId: string }) =>
    rerunRun(id, runId),
  );
}

export function useDeleteOutput() {
  return useProcessMutation(({ id, outputId }: { id: string; outputId: string }) =>
    deleteOutput(id, outputId),
  );
}
