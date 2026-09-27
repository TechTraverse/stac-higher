/**
 * GET /api/images/[id] — one image with its last ten scans and who uses it
 * (C-3, container-images spec §9.1/§9.2). member+.
 *
 * The registry is platform-wide, but PROCESSES are group-owned, and a
 * process outside your groups is a 404 everywhere else. So `in_use_by`
 * names only the processes in the caller's groups (all of them for an
 * admin), and `in_use_elsewhere` counts the rest.
 */
import type { APIRoute } from "astro";
import { canAccessGroup, isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { currentImageView, imageNotFound, invalidImageId, requireImageRole } from "@/lib/images/access";
import { getImage, listImageScans, listImageUsers } from "@/lib/images/storage";

const SCAN_HISTORY = 10;

export const GET: APIRoute = async ({ params, locals }) => {
  const allowed = requireImageRole(locals.auth, "member");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id)) return invalidImageId();
  const id = params.id.toLowerCase();

  try {
    const image = await getImage(id, currentImageView());
    if (!image) return imageNotFound();
    const [scans, users] = await Promise.all([
      listImageScans(image.id, SCAN_HISTORY),
      listImageUsers(image.id),
    ]);
    const visible = users.filter((user) => canAccessGroup(allowed.identity, user.group_id));
    return jsonResponse(200, {
      image,
      scans,
      in_use_by: visible,
      in_use_elsewhere: users.length - visible.length,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
