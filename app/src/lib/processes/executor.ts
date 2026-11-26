/**
 * The process-run executor backend (K-5, process-compute spec §6).
 *
 * `PROCESS_EXECUTOR` picks what runs process runs and image scans: `docker`
 * (the compose stack) or `kubernetes` (the chart: one Job per run, admitted by
 * Kueue). The pipeline reads the same variable and fails startup on an unknown
 * value; the app reports it (the hardware-profiles route) so the UI can word
 * its wait states. The vocabulary is pinned by
 * `tests/contract-fixtures/process-executor.json`.
 */

export const PROCESS_EXECUTOR_BACKENDS = ["docker", "kubernetes"] as const;
export type ExecutorBackend = (typeof PROCESS_EXECUTOR_BACKENDS)[number];
export const DEFAULT_EXECUTOR_BACKEND: ExecutorBackend = "docker";

function isExecutorBackend(value: string): value is ExecutorBackend {
  return (PROCESS_EXECUTOR_BACKENDS as readonly string[]).includes(value);
}

/** Read and validate the backend; blank means the default. Throws on an
 * unknown value — a misspelt backend must not be reported as Docker. */
export function getExecutorBackend(
  env: Record<string, string | undefined> = process.env,
): ExecutorBackend {
  const raw = (env.PROCESS_EXECUTOR ?? "").trim().toLowerCase() || DEFAULT_EXECUTOR_BACKEND;
  if (!isExecutorBackend(raw)) {
    throw new Error(
      `PROCESS_EXECUTOR must be one of ${PROCESS_EXECUTOR_BACKENDS.join(", ")}; got ${JSON.stringify(env.PROCESS_EXECUTOR)}`,
    );
  }
  return raw;
}
