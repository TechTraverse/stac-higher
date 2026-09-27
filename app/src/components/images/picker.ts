/**
 * The deploy form's image picker options (C-3, container-images spec §9.3):
 * every non-revoked image, with only the ones this process's GROUP may
 * deploy selectable. The rest are listed disabled with the reason. The
 * rules mirror the deploy gate (`lib/images/gate.ts`), which remains the
 * authority. This only saves a round trip to a 422.
 */
import type { ImageSnapshot } from "@/lib/images/reference";
import { IMAGE_STATUS_LABEL } from "@/lib/images/status";
import type { Image } from "@/lib/images/types";
import { exceptionLapsed } from "@/lib/images/verdict";
import { shortDigest } from "./format";

export interface ImageOption {
  id: string;
  label: string;
  /** Present only when the option is selectable. */
  snapshot: ImageSnapshot | null;
  disabledReason: string | null;
}

export function imageUsableReason(image: Image, groupId: string, now = new Date()): string | null {
  if (image.status !== "approved") return IMAGE_STATUS_LABEL[image.status];
  if (
    exceptionLapsed(
      { status: image.status, exceptionExpiresAt: image.exception?.expires_at ?? null, verdict: image.verdict },
      now,
    )
  ) {
    return "Exception expired";
  }
  if (image.stale === null) return "Image policy unavailable";
  if (image.stale) return "Stale: rescan before deploying";
  if (image.digest === null) return "Digest not resolved";
  const connection = image.registry_connection;
  if (connection) {
    if (connection.deleted) return "Registry credential was deleted";
    if (connection.group_id !== groupId) return "Pulled with another group's registry credential";
  }
  return null;
}

export function imagePickerOptions(
  images: readonly Image[],
  groupId: string,
  search = "",
  now = new Date(),
): ImageOption[] {
  const needle = search.trim().toLowerCase();
  return images
    .filter((image) => image.status !== "revoked")
    .filter(
      (image) => !needle || `${image.reference}:${image.tag_at_add}`.toLowerCase().includes(needle),
    )
    .map((image) => {
      const reason = imageUsableReason(image, groupId, now);
      return {
        id: image.id,
        label: `${image.reference}:${image.tag_at_add} · ${shortDigest(image.digest)}`,
        snapshot:
          reason === null && image.digest !== null
            ? { id: image.id, reference: image.reference, digest: image.digest }
            : null,
        disabledReason: reason,
      };
    })
    .sort(
      (a, b) =>
        Number(a.disabledReason !== null) - Number(b.disabledReason !== null) ||
        a.label.localeCompare(b.label),
    );
}
