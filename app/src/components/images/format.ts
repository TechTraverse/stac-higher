/**
 * Presentation helpers for images (C-3, container-images spec §9.2). Pure,
 * so the dashboard, the detail sheet and the deploy form agree.
 */
import { imageScanResultSchema, type ImageScanResult } from "@/lib/images/scan-result";
import type { ImageScan } from "@/lib/images/types";

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
