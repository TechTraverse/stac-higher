/**
 * GET /api/collections/[id]/settings/impact — the counted dry-run preview
 * behind the Settings tab's warn-and-proceed dialog (M2-F, spec §5.3,
 * ADR 0009 §6 / 0011).
 *
 * `?retention_days=N` → how many items are ALREADY past the window (what the
 * first retention sweep would expire); `?archived=true` → every item counts.
 * Read-only (member+); pgstac-less dev DBs answer with null counts so the UI
 * can say "unknown" instead of 500ing.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { jsonResponse } from "@/lib/connections/access";
import { query } from "@/lib/db/connection";

export interface SettingsImpact {
  total_items: number | null;
  /** Items the first sweep would expire under the proposed settings. */
  expired_items: number | null;
}

export const GET: APIRoute = async ({ params, url, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to preview settings impact",
    );
  }
  const collectionId = params.id;
  if (!collectionId) {
    return jsonResponse(404, { error: "Collection not found" });
  }

  const archivedParam = url.searchParams.get("archived");
  const archived = archivedParam === "true";
  const retentionParam = url.searchParams.get("retention_days");
  let retentionDays: number | null = null;
  if (retentionParam !== null) {
    retentionDays = Number(retentionParam);
    if (!Number.isInteger(retentionDays) || retentionDays < 1) {
      return jsonResponse(400, {
        error: "retention_days must be a positive integer",
      });
    }
  }

  try {
    const total = await query<{ count: string }>(
      `SELECT count(*) AS count FROM pgstac.items WHERE collection = $1`,
      [collectionId],
    );
    const totalItems = Number(total.rows[0]?.count ?? 0);

    let expiredItems = 0;
    if (archived) {
      expiredItems = totalItems; // archive expires everything (ADR 0009)
    } else if (retentionDays !== null) {
      const expired = await query<{ count: string }>(
        `SELECT count(*) AS count FROM pgstac.items
          WHERE collection = $1
            AND datetime < now() - make_interval(days => $2)`,
        [collectionId, retentionDays],
      );
      expiredItems = Number(expired.rows[0]?.count ?? 0);
    }
    const impact: SettingsImpact = {
      total_items: totalItems,
      expired_items: expiredItems,
    };
    return jsonResponse(200, impact);
  } catch {
    // pgstac absent (unit/CI DB) — the preview degrades to "unknown".
    const impact: SettingsImpact = { total_items: null, expired_items: null };
    return jsonResponse(200, impact);
  }
};
