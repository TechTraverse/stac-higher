/**
 * Credential budget at deploy time (R-1, spec §8).
 *
 * On AWS, STS role chaining caps a run's credentials at one hour, and the
 * pipeline mints `timeout + grace` for each run. The pipeline refuses at
 * mint (R-2); this is the deploy-time half, so the operator hears no at the
 * form. A revision's budget is the hardware profile's `max_queue_wait_seconds`
 * + the revision's `timeout_seconds` + the grace; it must fit
 * `PROCESS_MAX_CREDENTIAL_SECONDS` (unset = no limit). Shapes shared with the
 * pipeline are pinned by `tests/contract-fixtures/process-credential-budget.json`.
 */
import { jsonResponse } from "@/lib/http/response";
import { findProfile, type HardwareProfileSet } from "@/lib/processes/hardware";

export const MAX_CREDENTIAL_SECONDS_ENV = "PROCESS_MAX_CREDENTIAL_SECONDS";
export const CREDENTIAL_GRACE_SECONDS_ENV = "PROCESS_CREDENTIAL_GRACE_SECONDS";
export const DEFAULT_PROCESS_CREDENTIAL_GRACE_SECONDS = 300;
/** The pipeline never asks STS for less (`MIN_DURATION_SECONDS`), so a lower cap could never mint. */
export const STS_MIN_SESSION_SECONDS = 900;
export const CREDENTIAL_BUDGET_CODE = "credential_budget_exceeded";
export const CREDENTIAL_BUDGET_STATUS = 422;

type Env = Record<string, string | undefined>;

export function getMaxCredentialSeconds(env: Env = process.env): number | null {
  const raw = env[MAX_CREDENTIAL_SECONDS_ENV]?.trim();
  if (!raw) return null;
  if (!/^\d+$/.test(raw) || Number(raw) < STS_MIN_SESSION_SECONDS) {
    throw new Error(
      `${MAX_CREDENTIAL_SECONDS_ENV} is '${raw}'; it must be an integer number of seconds ≥ ${STS_MIN_SESSION_SECONDS}`,
    );
  }
  return Number(raw);
}

export function getCredentialGraceSeconds(env: Env = process.env): number {
  const raw = env[CREDENTIAL_GRACE_SECONDS_ENV]?.trim();
  if (!raw) return DEFAULT_PROCESS_CREDENTIAL_GRACE_SECONDS;
  if (!/^\d+$/.test(raw)) {
    throw new Error(`${CREDENTIAL_GRACE_SECONDS_ENV} is '${raw}'; it must be a non-negative integer number of seconds`);
  }
  return Number(raw);
}

export interface CredentialBudgetInput {
  maxCredentialSeconds: number | null;
  maxQueueWaitSeconds: number;
  timeoutSeconds: number;
  graceSeconds: number;
}

/** The message to refuse with, or null when there is no cap or the budget fits. */
export function credentialBudgetError(input: CredentialBudgetInput): string | null {
  const { maxCredentialSeconds: max, maxQueueWaitSeconds: wait, timeoutSeconds: timeout, graceSeconds: grace } = input;
  const budget = wait + timeout + grace;
  if (max === null || budget <= max) return null;
  return (
    `credential budget ${budget}s (max_queue_wait_seconds ${wait} + timeout_seconds ${timeout} + grace ${grace}) ` +
    `exceeds this deployment's cap of ${max}s (${MAX_CREDENTIAL_SECONDS_ENV})`
  );
}

/** Null when allowed, or when the profile is missing (the hardware gate refuses that first). */
export function revisionCredentialBudgetError(
  runtime: { timeout_seconds: number; hardware: { profile: string } },
  profiles: HardwareProfileSet,
  env: Env = process.env,
): string | null {
  const maxCredentialSeconds = getMaxCredentialSeconds(env);
  const graceSeconds = getCredentialGraceSeconds(env);
  const profile = findProfile(profiles, runtime.hardware.profile);
  if (!profile) return null;
  return credentialBudgetError({
    maxCredentialSeconds,
    maxQueueWaitSeconds: profile.max_queue_wait_seconds,
    timeoutSeconds: runtime.timeout_seconds,
    graceSeconds,
  });
}

export function credentialBudgetRefused(message: string): Response {
  return jsonResponse(CREDENTIAL_BUDGET_STATUS, { error: message, code: CREDENTIAL_BUDGET_CODE });
}
