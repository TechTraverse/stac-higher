/**
 * POST /api/processes/builtin — create-or-reuse a group's built-in extractor
 * process (X-4, X-queue spec §7).
 *
 * `{ builtin_id, group_id }` → the group's LIVE process for that registry
 * entry (200), or a new one (201): `kind: extractor`, named from the registry
 * label, marked with `builtin_id`, revision 1 deployed from the template in
 * the same transaction. Operator+ of the group (admin anywhere), audited by
 * the guard as a process `create` — on reuse the audit detail still carries
 * the existing id from the body. Idempotent per group, by the migration-028
 * partial unique index: two concurrent picks converge on one process.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { canAccessGroup } from "@/lib/connections/access";
import { findBuiltinExtractor } from "@/lib/extractors/registry";
import {
  builtinProcessDefaults,
  builtinRevisionTemplate,
} from "@/lib/extractors/template";
import { jsonResponse } from "@/lib/http/response";
import { processBuiltinCreateSchema } from "@/lib/processes/schemas";
import {
  createBuiltinProcess,
  DuplicateProcessNameError,
  findBuiltinProcess,
} from "@/lib/processes/storage";

export const POST: APIRoute = async ({ request, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required for this action",
    );
  }
  if (!canMutate(auth.identity)) {
    return authzError(
      403,
      "forbidden",
      "This action requires the operator or admin role",
    );
  }
  try {
    const body = await request.json().catch(() => null);
    const parsed = processBuiltinCreateSchema.safeParse(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const { builtin_id: builtinId, group_id: groupId } = parsed.data;
    if (!canAccessGroup(auth.identity, groupId)) {
      return authzError(403, "forbidden", "group_id must be one of your groups");
    }
    const entry = findBuiltinExtractor(builtinId);
    if (!entry) {
      return jsonResponse(404, {
        error: `'${builtinId}' is not a built-in extractor this platform ships`,
      });
    }

    const existing = await findBuiltinProcess(groupId, builtinId);
    if (existing) return jsonResponse(200, existing);

    const created = await createBuiltinProcess({
      ...builtinProcessDefaults(entry),
      groupId,
      builtinId,
      createdBy: auth.identity.sub,
      revision: builtinRevisionTemplate(entry),
    });
    if (created) return jsonResponse(201, created);
    // Lost the race to a concurrent pick: the index guarantees exactly one
    // live row, so the re-read is the reuse path.
    const raced = await findBuiltinProcess(groupId, builtinId);
    if (raced) return jsonResponse(200, raced);
    return jsonResponse(500, { error: "built-in process vanished during creation" });
  } catch (err) {
    if (err instanceof DuplicateProcessNameError) {
      return jsonResponse(409, {
        error:
          `${err.message}. A built-in extractor is named from its registry label; ` +
          "rename the existing process to pick the built-in one.",
      });
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
