/**
 * Access rules for the staged-upload routes (Phase 7 spec §4.1–4.2).
 *
 * The middleware guard enforces ROLE on the mint (POST /api/uploads is in the
 * gated table). These helpers cover the GROUP dimension and the poll route's
 * visibility: a session is visible to admins, to members of its group (the
 * collection's owning group captured at mint time), and to its creator (which
 * is what makes sessions on UNOWNED collections — group_id null — pollable
 * by the operator who minted them). A non-visible row is a 404, not a 403 —
 * existence is scoped, matching the connections/associations convention.
 */
import type { CanonicalIdentity } from "@/lib/auth/types";
import { isAdmin } from "@/lib/authz/permissions";
import type { StagedUpload } from "./storage";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Guard the path param before it hits the uuid column (avoids a 500). */
export function isUploadId(value: string | undefined): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

/** Poll-route visibility: admin, session group member, or creator. */
export function canSeeStagedUpload(
  identity: CanonicalIdentity,
  upload: StagedUpload,
): boolean {
  if (isAdmin(identity)) return true;
  if (upload.groupId !== null && identity.groups.includes(upload.groupId)) {
    return true;
  }
  return upload.createdBy === identity.sub;
}
