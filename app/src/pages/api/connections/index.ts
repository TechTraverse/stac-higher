/**
 * /api/connections — list + create (ROADMAP Phase 2, §7).
 *
 * GET  — any authenticated user (member+). Members/operators see their own
 *        groups' connections; admins see all. Credentials are NEVER in the
 *        response — only `credentials_set`.
 * POST — operator|admin (role enforced by the middleware guard AND here,
 *        defense in depth). group_id must be one of the caller's groups
 *        unless the caller is admin. Credentials are validated, sealed into
 *        the AES-256-GCM envelope, and stored write-only.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canMutate, isAdmin } from "@/lib/authz/permissions";
import { canAccessGroup, jsonResponse } from "@/lib/connections/access";
import { getEncryptionProvider, CredentialKeyError } from "@/lib/connections/crypto";
import { parseConnectionCreate } from "@/lib/connections/schemas";
import { createConnection, listConnections } from "@/lib/connections/storage";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to list connections",
    );
  }
  try {
    const connections = await listConnections(
      isAdmin(auth.identity) ? null : auth.identity.groups,
    );
    return jsonResponse(200, { connections });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

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
    const parsed = parseConnectionCreate(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const data = parsed.data;

    // §7: operators create connections only in their own groups.
    if (!canAccessGroup(auth.identity, data.group_id)) {
      return authzError(
        403,
        "forbidden",
        "group_id must be one of your groups",
      );
    }

    // An anonymous s3 connection stores an EMPTY envelope rather than no
    // envelope: the pipeline's build_adapter treats "no credentials" as a
    // configuration error, and `{}` is exactly what an unsigned adapter needs.
    const envelope = getEncryptionProvider().encrypt(
      JSON.stringify(data.credentials ?? {}),
    );
    const connection = await createConnection({
      name: data.name,
      description: data.description,
      protocol: data.protocol,
      config: data.config,
      encryptedCredentials: envelope,
      groupId: data.group_id,
      createdBy: auth.identity.sub,
      enabled: data.enabled,
    });
    return jsonResponse(201, connection);
  } catch (err) {
    if (err instanceof CredentialKeyError) {
      // Server misconfiguration — fail loudly with the actionable message.
      return jsonResponse(500, { error: err.message });
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
