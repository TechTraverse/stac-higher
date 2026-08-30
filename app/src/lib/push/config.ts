/**
 * BFF shared-secret plumbing (Phase 7 spec §4.3 / §5.2, ADR 0015).
 *
 * The enforced-mode proxy policy exempts requests carrying `X-BFF-Auth` from
 * the `externally_writable` CQL2 gate — app-mediated writes are RBAC-gated
 * and audited app-side and must reach every collection the UI manages. The
 * app EMITS the header on every forwarded catalog write when the secret is
 * configured; pass-through mode (no secret in either env) is byte-for-byte
 * unchanged.
 *
 * The MANDATORY side ("both sides fail fast at startup if unset") is an
 * enforced-overlay property: the app cannot distinguish "dev without a
 * secret" from "enforced overlay without a secret", so the fail-fast lives in
 * P7-G's compose (required-variable interpolation on both services), not
 * here. The header VALUE must never be logged — treat it like a credential.
 */
import type { Env } from "@/lib/auth/config";

export const BFF_AUTH_HEADER = "X-BFF-Auth";

/** The shared secret, or null when unset/blank (pass-through deployments). */
export function getBffSharedSecret(env: Env = process.env): string | null {
  const raw = env.CATALOG_BFF_SHARED_SECRET;
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  return trimmed.length > 0 ? trimmed : null;
}
