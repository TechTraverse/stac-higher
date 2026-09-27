/**
 * Staleness (container-images spec §4.3) is computed, never stored. It only
 * means something for an image that has passed a scan (`approved`,
 * `flagged`). Every other status is already blocked for its own reason, so
 * it reads as not stale. The boundary is the deploy gate's
 * (`evaluateImageGate`): a scan exactly `scanWindowDays` ago is still fresh.
 * `null` means "unknown" because the policy (and so the window) could not
 * be read.
 */
import type { ImageStatus } from "./status";

const DAY_MS = 86_400_000;

export function isImageStale(
  status: ImageStatus,
  lastScannedAt: Date | string | null,
  scanWindowDays: number | null,
  now: Date,
): boolean | null {
  if (status !== "approved" && status !== "flagged") return false;
  if (scanWindowDays === null) return null;
  if (lastScannedAt === null) return true;
  const scanned = new Date(lastScannedAt).getTime();
  return scanned < now.getTime() - scanWindowDays * DAY_MS;
}
