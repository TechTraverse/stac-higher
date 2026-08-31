/**
 * Client functions for the `/api/processes` surface (Phase 9 / M5-A).
 *
 * Same-origin JSON, and errors surface the guard shape `{error, code}` as an
 * `Error` carrying `.code`/`.status` — the connections/api.ts contract.
 *
 * Note what is NOT here: nothing runs a process. A "test run" POSTs a request
 * row and returns something to poll (ADR 0004); the app never reaches into
 * the executor.
 */
import type {
  Process,
  ProcessCheck,
  ProcessRun,
  ProcessOutput,
  ProcessRevision,
  ProcessSource,
} from "./types";
import type {
  ProcessCreate,
  ProcessRevisionCreate,
  ProcessSourceCreate,
  ProcessSourceUpdate,
  ProcessUpdate,
} from "./schemas";

export class ProcessApiError extends Error {
  code?: string;
  status: number;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ProcessApiError";
    this.status = status;
    this.code = code;
  }
}

async function processFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`/api/processes${path}`, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as Record<string, unknown>);
    const message =
      (typeof body.error === "string" && body.error) ||
      `Request failed: ${res.status}`;
    const code = typeof body.code === "string" ? body.code : undefined;
    throw new ProcessApiError(message, res.status, code);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const enc = encodeURIComponent;

export async function listProcesses(): Promise<Process[]> {
  return (await processFetch<{ processes: Process[] }>("")).processes;
}

export async function getProcess(id: string): Promise<Process> {
  return processFetch<Process>(`/${enc(id)}`);
}

export async function createProcess(input: ProcessCreate): Promise<Process> {
  return processFetch<Process>("", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateProcess(
  id: string,
  input: ProcessUpdate,
): Promise<Process> {
  return processFetch<Process>(`/${enc(id)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export async function deleteProcess(id: string): Promise<void> {
  await processFetch(`/${enc(id)}`, { method: "DELETE" });
}

export async function listRevisions(id: string): Promise<ProcessRevision[]> {
  return (
    await processFetch<{ revisions: ProcessRevision[] }>(`/${enc(id)}/revisions`)
  ).revisions;
}

/** Deploy: create an immutable revision and make it current, in one call. */
export async function deployRevision(
  id: string,
  input: ProcessRevisionCreate,
): Promise<ProcessRevision> {
  return processFetch<ProcessRevision>(`/${enc(id)}/revisions`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function listSources(id: string): Promise<ProcessSource[]> {
  return (await processFetch<{ sources: ProcessSource[] }>(`/${enc(id)}/sources`))
    .sources;
}

export async function createSource(
  id: string,
  input: ProcessSourceCreate,
): Promise<ProcessSource> {
  return processFetch<ProcessSource>(`/${enc(id)}/sources`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateSource(
  id: string,
  sourceId: string,
  input: ProcessSourceUpdate,
): Promise<ProcessSource> {
  return processFetch<ProcessSource>(`/${enc(id)}/sources/${enc(sourceId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export async function deleteSource(
  id: string,
  sourceId: string,
): Promise<void> {
  await processFetch(`/${enc(id)}/sources/${enc(sourceId)}`, {
    method: "DELETE",
  });
}

export async function listOutputs(id: string): Promise<ProcessOutput[]> {
  return (await processFetch<{ outputs: ProcessOutput[] }>(`/${enc(id)}/outputs`))
    .outputs;
}

export async function createOutput(
  id: string,
  collectionId: string,
): Promise<ProcessOutput> {
  return processFetch<ProcessOutput>(`/${enc(id)}/outputs`, {
    method: "POST",
    body: JSON.stringify({ collection_id: collectionId }),
  });
}

export async function deleteOutput(
  id: string,
  outputId: string,
): Promise<void> {
  await processFetch(`/${enc(id)}/outputs/${enc(outputId)}`, {
    method: "DELETE",
  });
}

export async function listRuns(id: string, limit = 50): Promise<ProcessRun[]> {
  return (
    await processFetch<{ runs: ProcessRun[] }>(`/${enc(id)}/runs?limit=${limit}`)
  ).runs;
}

/** Re-run a DEAD run (the `redeliver` analog). Returns the requeued row; the
 * pipeline's run tick picks it up. */
export async function rerunRun(id: string, runId: string): Promise<ProcessRun> {
  return processFetch<ProcessRun>(`/${enc(id)}/runs/${enc(runId)}/rerun`, {
    method: "POST",
  });
}

/** Request a test run (ADR 0004). Returns the row to poll — NOT a result. */
export async function requestTestRun(id: string): Promise<ProcessCheck> {
  return processFetch<ProcessCheck>(`/${enc(id)}/test`, { method: "POST" });
}

export async function getTestRun(
  id: string,
  checkId: string,
): Promise<ProcessCheck> {
  return processFetch<ProcessCheck>(`/${enc(id)}/checks/${enc(checkId)}`);
}
