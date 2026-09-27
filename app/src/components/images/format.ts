/**
 * Presentation helpers for images (C-3, container-images spec §9.2). Pure,
 * so the dashboard, the detail sheet and the deploy form agree.
 */
import { imageScanResultSchema, type ImageScanResult } from "@/lib/images/scan-result";
import type { Image, ImageScan } from "@/lib/images/types";
import { readScanDiff, type ImageScanDiff } from "@/lib/images/scan-diff";

/** "sha256:" + 12 hex characters: enough to tell digests apart (the gate's
 * own message uses the same cut). */
export function shortDigest(digest: string | null | undefined): string {
  return digest ? digest.slice(0, 19) : "unresolved";
}

/**
 * Spec §3.2: runs on a user image are forced to uid 10001 whatever the
 * image's USER says. The scan records the image config, and the dashboard
 * warns so an author hears it before a run fails on an unreadable file.
 */
export function configWarning(config: Record<string, unknown> | null | undefined): string | null {
  if (!config) return null;
  const user = typeof config.user === "string" ? config.user.trim() : "";
  if (user === "") return "image declares no USER (runs as root by default); runs as 10001";
  const name = user.split(":")[0];
  if (name === "root" || name === "0") return `image declares USER ${user}; runs as 10001`;
  if (name !== "10001") {
    return `image declares USER ${user}; runs as 10001, so the files it needs must be readable by that uid`;
  }
  return null;
}

export type Finding = NonNullable<ImageScanResult["top"]>[number];

/** Spec §9.2: risk-sorted, KEV first. */
export function sortFindings(findings: readonly Finding[]): Finding[] {
  return [...findings].sort(
    (a, b) => Number(b.kev) - Number(a.kev) || b.risk - a.risk || a.id.localeCompare(b.id),
  );
}

/**
 * The ONE scan whose findings the detail sheet shows: `image.last_scan_id`
 * when it names a scan in the list, else the newest `done` one (`scans` is
 * newest first, as the detail route returns it). If that scan's `result`
 * does not read as the §6.4 document (C-2 stores it with `verdict`/`diff`
 * beside it; the lenient reader drops those), the caller must say so — it
 * must NEVER silently fall back to an older scan's findings, which would
 * show findings for a digest that is not the one the image last resolved.
 */
export type LatestScanResult =
  | { status: "ok"; result: ImageScanResult }
  | { status: "unreadable" }
  | { status: "none" };

export function latestScanResult(
  scans: readonly ImageScan[],
  lastScanId: string | null,
): LatestScanResult {
  const target =
    (lastScanId !== null ? scans.find((s) => s.id === lastScanId) : undefined) ??
    scans.find((s) => s.status === "done");
  if (!target || target.status !== "done" || !target.result) return { status: "none" };
  const parsed = imageScanResultSchema.safeParse(target.result);
  if (parsed.success && parsed.data.error === null) return { status: "ok", result: parsed.data };
  return { status: "unreadable" };
}

/** A failed scan's message (`result.error`, spec §6.3 step 5). */
export function scanError(scan: ImageScan | null | undefined): string | null {
  const error = scan?.result?.error;
  return typeof error === "string" && error.length > 0 ? error : null;
}

/** "DB 9 days old" (spec §6.1): the baked Grype DB's age at the last scan.
 * A time that fails to parse (`NaN`) must never read as fresh: `null`
 * ("unknown"), the same as no date at all. */
export function dbAgeDays(dbBuiltAt: string | null | undefined, now: Date): number | null {
  if (!dbBuiltAt) return null;
  const built = Date.parse(dbBuiltAt);
  if (!Number.isFinite(built)) return null;
  return Math.max(0, Math.floor((now.getTime() - built) / 86_400_000));
}

/**
 * Has an exception's expiry date passed (C-4)? The DATE alone decides the
 * word "expired"; whether new deploys are refused is `exceptionLapsed`'s
 * (verdict-aware) question, asked separately. Expiry exactly `now` counts
 * as expired (the gate's boundary), and an unparseable date never reads as
 * live.
 */
export function isExceptionExpired(expiresAt: string, now: Date): boolean {
  const t = Date.parse(expiresAt);
  return !Number.isFinite(t) || t <= now.getTime();
}

/** Spec §4.4 + C-4: an exception approves a rejected or flagged image, and a
 * new grant replaces the one an approved image carries. An approved image
 * without one passed on its own. */
export function canTakeException(image: Pick<Image, "status" | "exception">): boolean {
  return (
    image.status === "rejected" ||
    image.status === "flagged" ||
    (image.status === "approved" && image.exception !== null)
  );
}

/** The `expires_at` a grant of `days` sends. The server measures its cap
 * from its own, later "now", so a whole-day grant at the cap stays inside.
 * F7: when `days` sits exactly at the policy's `exception_max_days` cap
 * (`maxDays`), a 5-minute margin is subtracted so a browser clock running
 * slightly ahead of the server does not risk a 422 `exception_too_long`. */
export function exceptionExpiry(days: number, now: Date, maxDays?: number): string {
  const margin = maxDays !== undefined && days === maxDays ? 5 * 60_000 : 0;
  return new Date(now.getTime() + days * 86_400_000 - margin).toISOString();
}

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "negligible", "unknown"] as const;

/** "+2 high, −1 medium" (U+2212, spec §9.2): only the severities that moved. */
export function formatCountsDelta(delta: ImageScanDiff["counts_delta"]): string {
  const parts = SEVERITY_ORDER.filter((s) => delta[s] !== 0).map(
    (s) => `${delta[s] > 0 ? "+" : "−"}${Math.abs(delta[s])} ${s}`,
  );
  return parts.length > 0 ? parts.join(", ") : "no change in counts";
}

/**
 * One history line (spec §9.2): the count change since the previous scan,
 * dated by that scan when it is listed, then new KEVs, newly fixed findings
 * and a verdict flip. Null for an admission or a scan without a diff.
 */
export function scanDiffSummary(scan: ImageScan, scans: readonly ImageScan[]): string | null {
  const diff = readScanDiff(scan.result);
  if (!diff) return null;
  const previous = diff.previous_scan_id
    ? scans.find((s) => s.id === diff.previous_scan_id)
    : undefined;
  const since = previous?.finished_at
    ? ` since ${previous.finished_at.slice(0, 10)}`
    : diff.previous_scan_id
      ? " since the previous scan"
      : "";
  const extras: string[] = [];
  if (diff.new_kev.length > 0) extras.push(`${diff.new_kev.length} new KEV`);
  if (diff.newly_fixed.length > 0) extras.push(`${diff.newly_fixed.length} newly fixed`);
  if (diff.verdict_changed) extras.push("verdict changed");
  return `${formatCountsDelta(diff.counts_delta)}${since}${extras.length > 0 ? ` · ${extras.join(", ")}` : ""}`;
}

/** A rescan's drift note (spec §8.2, informational): where the tag points
 * now when it moved, or that the registry did not answer the HEAD. */
export function scanTagDrift(scan: ImageScan): string | null {
  const raw = scan.result?.tag_drift;
  if (raw === null || typeof raw !== "object") return null;
  const drift = raw as { current_digest?: unknown; drifted?: unknown };
  if (drift.current_digest === null) return "tag not checked: the registry did not answer";
  if (drift.drifted === true && typeof drift.current_digest === "string") {
    return `tag moved to ${shortDigest(drift.current_digest)}`;
  }
  return null;
}
