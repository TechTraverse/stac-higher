/**
 * The deployment network cap for processes (GOES spec §4, ADR 0018).
 *
 * `PROCESS_NETWORK_MAX` is the highest `runtime.network.level` this deployment
 * permits. The pipeline reads the same variable and enforces it independently
 * at launch — a revision above the cap dies naming the level and the cap. The
 * app checks it on deploy so an operator hears "no" at the form, not an hour
 * later in a run's ledger row; the client mirror (`PUBLIC_PROCESS_NETWORK_MAX`)
 * lets the deploy card disable options above it up front.
 */
import { PROCESS_NETWORK_LEVELS, type NetworkLevel } from "@/lib/processes/schemas";

export const DEFAULT_NETWORK_MAX: NetworkLevel = "isolated";

function isNetworkLevel(value: string): value is NetworkLevel {
  return (PROCESS_NETWORK_LEVELS as readonly string[]).includes(value);
}

/** Read and validate the cap. Throws on an unknown value — a misspelt cap
 * must fail loudly rather than quietly become the most (or least) permissive. */
export function getNetworkMax(
  env: Record<string, string | undefined> = process.env,
): NetworkLevel {
  const raw = (env.PROCESS_NETWORK_MAX ?? DEFAULT_NETWORK_MAX).trim().toLowerCase();
  if (!isNetworkLevel(raw)) {
    throw new Error(
      `PROCESS_NETWORK_MAX must be one of ${PROCESS_NETWORK_LEVELS.join(", ")}; got ${JSON.stringify(env.PROCESS_NETWORK_MAX)}`,
    );
  }
  return raw;
}

/** Levels are ordered lowest first; a level is within the cap when it does
 * not sit above it. */
export function networkLevelWithinCap(level: NetworkLevel, cap: NetworkLevel): boolean {
  return PROCESS_NETWORK_LEVELS.indexOf(level) <= PROCESS_NETWORK_LEVELS.indexOf(cap);
}

export function NETWORK_CAP_MESSAGE(level: NetworkLevel, cap: NetworkLevel): string {
  return (
    `network level '${level}' exceeds this deployment's maximum '${cap}' ` +
    "(PROCESS_NETWORK_MAX)"
  );
}
