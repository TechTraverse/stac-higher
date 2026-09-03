/**
 * GET /api/collections/[id]/settings/impact — the counted dry-run preview
 * behind the Settings tab's warn-and-proceed dialog (M2-F, spec §5.3,
 * ADR 0009 §6 / 0011).
 *
 * `?retention_days=N` → how many items are ALREADY past the window (what the
 * first retention sweep would expire); `?retention_max_items=N` (W-2) → how
 * many sit beyond the newest N by item datetime; both → the DISTINCT union,
 * which is exactly what the sweep would mark (an item that is both old and
 * beyond the cap is deleted once, so it is counted once); `?archived=true` →
 * every item counts. Read-only (member+); pgstac-less dev DBs answer with
 * null counts so the UI can say "unknown" instead of 500ing.
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

  const maxItemsParam = url.searchParams.get("retention_max_items");
  let retentionMaxItems: number | null = null;
  if (maxItemsParam !== null) {
    retentionMaxItems = Number(maxItemsParam);
    if (!Number.isInteger(retentionMaxItems) || retentionMaxItems < 1) {
      return jsonResponse(400, {
        error: "retention_max_items must be a positive integer",
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
    } else if (retentionDays !== null && retentionMaxItems !== null) {
      // Mirrors the sweep's UNION (pipeline gc/repo.py list_expired_items).
      const expired = await query<{ count: string }>(
        `SELECT count(DISTINCT id) AS count FROM (
           SELECT id FROM pgstac.items
            WHERE collection = $1
              AND datetime < now() - make_interval(days => $2)
           UNION ALL
           SELECT id FROM (
             SELECT id FROM pgstac.items WHERE collection = $1
              ORDER BY datetime DESC, id DESC
             OFFSET $3
           ) beyond_cap
         ) expired`,
        [collectionId, retentionDays, retentionMaxItems],
      );
      expiredItems = Number(expired.rows[0]?.count ?? 0);
    } else if (retentionDays !== null) {
      const expired = await query<{ count: string }>(
        `SELECT count(*) AS count FROM pgstac.items
          WHERE collection = $1
            AND datetime < now() - make_interval(days => $2)`,
        [collectionId, retentionDays],
      );
      expiredItems = Number(expired.rows[0]?.count ?? 0);
    } else if (retentionMaxItems !== null) {
      // Everything past the newest N — the same ORDER BY … OFFSET the sweep
      // uses, id tiebreak included, so the preview and the deletion agree.
      const expired = await query<{ count: string }>(
        `SELECT count(*) AS count FROM (
           SELECT id FROM pgstac.items WHERE collection = $1
            ORDER BY datetime DESC, id DESC
           OFFSET $2
         ) beyond_cap`,
        [collectionId, retentionMaxItems],
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
