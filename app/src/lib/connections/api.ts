/**
 * Client functions for the `/api/connections` surface (Phase 2).
 *
 * All requests are same-origin JSON. Credentials are WRITE-ONLY: the API never
 * returns secrets or raw host keys, so nothing here ever reads them back — a
 * `Connection` only carries `credentials_set` and a host-key fingerprint.
 * Errors surface the guard shape `{error, code}` (401/403) as an `Error` with
 * `.code`/`.status` attached.
 */
import type { Connection, ConnectionCheck } from "./types";
import type {
  ConnectionCreateInput,
  ConnectionUpdateInput,
} from "./schemas";

export class ConnectionApiError extends Error {
  code?: string;
  status: number;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ConnectionApiError";
    this.status = status;
    this.code = code;
  }
}

async function connectionFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`/api/connections${path}`, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as Record<string, unknown>);
    const message =
      (typeof body.error === "string" && body.error) ||
      `Request failed: ${res.status}`;
    const code = typeof body.code === "string" ? body.code : undefined;
    throw new ConnectionApiError(message, res.status, code);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export async function listConnections(): Promise<Connection[]> {
  const data = await connectionFetch<{ connections: Connection[] }>("");
  return data.connections;
}

export async function getConnection(id: string): Promise<Connection> {
  return connectionFetch<Connection>(`/${encodeURIComponent(id)}`);
}

export async function createConnection(
  input: ConnectionCreateInput,
): Promise<Connection> {
  return connectionFetch<Connection>("", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateConnection(
  id: string,
  input: ConnectionUpdateInput,
): Promise<Connection> {
  return connectionFetch<Connection>(`/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export async function deleteConnection(id: string): Promise<void> {
  await connectionFetch<void>(`/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function testConnection(id: string): Promise<ConnectionCheck> {
  const data = await connectionFetch<{ check: ConnectionCheck }>(
    `/${encodeURIComponent(id)}/test`,
    { method: "POST" },
  );
  return data.check;
}

export async function pollCheck(
  id: string,
  checkId: string,
): Promise<ConnectionCheck> {
  const data = await connectionFetch<{ check: ConnectionCheck }>(
    `/${encodeURIComponent(id)}/checks/${encodeURIComponent(checkId)}`,
  );
  return data.check;
}

export async function resetHostKey(id: string): Promise<Connection> {
  return connectionFetch<Connection>(
    `/${encodeURIComponent(id)}/host-key/reset`,
    { method: "POST" },
  );
}

export interface TestResult {
  ok: boolean;
  message: string;
  latencyMs?: number;
  timedOut: boolean;
}

const sleep = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Run a full test: request a check, then poll it until the pipeline settles it
 * (`done`|`failed`) or the poll budget is exhausted. Bounded by `maxTries` so
 * it can never loop forever if the pipeline is down.
 */
export async function runConnectionTest(
  id: string,
  {
    intervalMs = 1500,
    maxTries = 20,
  }: { intervalMs?: number; maxTries?: number } = {},
): Promise<TestResult> {
  const check = await testConnection(id);
  let current = check;
  for (let attempt = 0; attempt < maxTries; attempt++) {
    if (current.status === "done" || current.status === "failed") {
      const result = current.result ?? {};
      const ok =
        current.status === "done" && (result as { ok?: unknown }).ok === true;
      const message =
        typeof (result as { message?: unknown }).message === "string"
          ? ((result as { message: string }).message)
          : ok
            ? "Connection succeeded"
            : "Connection failed";
      const latency = (result as { latency_ms?: unknown }).latency_ms;
      return {
        ok,
        message,
        latencyMs: typeof latency === "number" ? latency : undefined,
        timedOut: false,
      };
    }
    await sleep(intervalMs);
    current = await pollCheck(id, check.id);
  }
  return {
    ok: false,
    message: "Test timed out waiting for the pipeline to respond",
    timedOut: true,
  };
}
