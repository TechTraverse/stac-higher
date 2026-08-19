/**
 * Shared load-and-authorize step for the alert action routes (M2-B, §7).
 * Missing and out-of-group both come back as the same 404, so the routes
 * cannot leak another group's alert ids. An alert whose derived group is gone
 * (connection row hard-deleted) is admin-only.
 */
import type { CanonicalIdentity } from "@/lib/auth/types";
import { isAdmin } from "@/lib/authz/permissions";
import { isUuid, jsonResponse } from "@/lib/connections/access";
import { getAlert, type ApiAlert } from "./storage";

export function canActOnAlert(
  identity: CanonicalIdentity,
  alert: ApiAlert,
): boolean {
  if (isAdmin(identity)) return true;
  return alert.group_id !== null && identity.groups.includes(alert.group_id);
}

export async function loadActionableAlert(
  identity: CanonicalIdentity,
  id: string | undefined,
): Promise<{ alert: ApiAlert } | { response: Response }> {
  if (!isUuid(id)) {
    return { response: jsonResponse(404, { error: "Alert not found" }) };
  }
  const alert = await getAlert(id);
  if (!alert || !canActOnAlert(identity, alert)) {
    return { response: jsonResponse(404, { error: "Alert not found" }) };
  }
  return { alert };
}
