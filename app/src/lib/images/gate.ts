/**
 * The deploy gate for user images (C-1, container-images spec §3). This is
 * the `PROCESS_NETWORK_MAX` dual-enforcement pattern: the Zod shape accepts
 * all three runtime kinds, this DB-backed check refuses a kind 2/3 revision
 * unless its snapshot names an APPROVED, FRESH `container_images` row with
 * the same reference and digest that is usable by the process's group, and
 * the pipeline re-checks at launch (C-2, spec §8.4).
 *
 * Order: exists -> approved -> reference/digest -> fresh -> group. A pending
 * image therefore says "not approved" rather than "digest mismatch" (its
 * digest is still NULL). The policy is read only for a user image, so a
 * broken policy file never blocks an inline deploy, and a user-image deploy
 * fails CLOSED (503) when the policy cannot be read.
 */
import { jsonResponse } from "@/lib/http/response";
import { loadImagePolicy } from "./policy";
import type { ImageSnapshot } from "./reference";
import { getImageForGate, type ImageGateRow } from "./storage";
import type { ImageGateReason } from "./status";

const DAY_MS = 86_400_000;

export interface ImageGateRefusal {
  reason: ImageGateReason;
  message: string;
}

export function evaluateImageGate(input: {
  row: ImageGateRow | null;
  snapshot: ImageSnapshot;
  processGroupId: string;
  now: Date;
  scanWindowDays: number;
}): ImageGateRefusal | null {
  const { row, snapshot, processGroupId, now, scanWindowDays } = input;
  // "sha256:" + 12 hex characters: enough to tell digests apart in a message.
  const named = `${snapshot.reference}@${snapshot.digest.slice(0, 19)}`;
  if (!row) {
    return {
      reason: "image_not_approved",
      message: `image ${named} is not in the platform's image registry; add it and let its scan pass before deploying`,
    };
  }
  if (row.status !== "approved") {
    return {
      reason: "image_not_approved",
      message: `image ${named} is ${row.status}; only an approved image can be deployed`,
    };
  }
  if (row.reference !== snapshot.reference || row.digest !== snapshot.digest) {
    return {
      reason: "image_digest_mismatch",
      message: `image ${snapshot.id} is ${row.reference}@${row.digest ?? "(unresolved)"}, not the ${snapshot.reference}@${snapshot.digest} this revision names`,
    };
  }
  const cutoff = now.getTime() - scanWindowDays * DAY_MS;
  if (row.last_scanned_at === null || row.last_scanned_at.getTime() < cutoff) {
    const when = row.last_scanned_at
      ? `last scanned ${row.last_scanned_at.toISOString()}`
      : "never scanned";
    return {
      reason: "image_stale",
      message: `image ${named} was ${when}, outside the ${scanWindowDays}-day scan window; rescan it before deploying`,
    };
  }
  if (row.registry_connection_id !== null && row.registry_connection_group_id !== processGroupId) {
    const message =
      row.registry_connection_group_id === null
        ? `image ${named} is pulled with a registry credential that was deleted; add a fresh credential before deploying`
        : `image ${named} is pulled with a registry credential that belongs to another group; only that group's processes can use it`;
    return { reason: "image_group_mismatch", message };
  }
  return null;
}

/** `snapshot` is `runtime.image` for kinds 2/3 and `null` for `inline_python`.
 * Throws `ImagePolicyUnavailable` when a user image is named and the policy
 * cannot be read (the caller answers 503). */
export async function checkImageGate(
  snapshot: ImageSnapshot | null,
  processGroupId: string,
  options: { now?: Date; env?: Record<string, string | undefined> } = {},
): Promise<ImageGateRefusal | null> {
  if (snapshot === null) return null;
  const policy = loadImagePolicy(options.env);
  const row = await getImageForGate(snapshot.id);
  return evaluateImageGate({
    row,
    snapshot,
    processGroupId,
    now: options.now ?? new Date(),
    scanWindowDays: policy.scan_window_days,
  });
}

export function imageGateRefused(refusal: ImageGateRefusal): Response {
  return jsonResponse(422, { error: refusal.message, code: refusal.reason });
}

export function imagePolicyUnavailable(err: Error): Response {
  return jsonResponse(503, { error: err.message, code: "image_policy_unavailable" });
}
