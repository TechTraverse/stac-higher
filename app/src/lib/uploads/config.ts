/**
 * Staged-upload configuration (Phase 7 push ingest, spec §4.1).
 *
 * ONE governing clock: staging expiry is the ledger row's age
 * (`staged_uploads.created_at + STAGING_TTL_SECONDS`) everywhere — the mint's
 * `expires_at`, the pipeline's finalize sweep (pending → expired), and the
 * byte-level TTL sweep all derive from it. The default MUST match the
 * pipeline's (`DEFAULT_STAGING_TTL_SECONDS` in `pipeline/config.py`, 24h);
 * both runtimes read the same `STAGING_TTL_SECONDS` env var in deployment.
 *
 * Injectable-`env` pattern, same as `getStorageConfig`.
 */

export const DEFAULT_STAGING_TTL_SECONDS = 86400; // 24h — pipeline/config.py

export function getStagingTtlSeconds(
  env: Record<string, string | undefined> = process.env,
): number {
  const raw = env.STAGING_TTL_SECONDS;
  if (raw === undefined) return DEFAULT_STAGING_TTL_SECONDS;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0
    ? parsed
    : DEFAULT_STAGING_TTL_SECONDS;
}
