/**
 * Permission guard for the Astro API routes (ROADMAP §7 + Phase 1).
 *
 * Called from `src/middleware.ts` around every request. Non-gated requests
 * (all reads, non-API pages) pass through untouched. Gated mutations:
 *
 *   - anonymous            → 401 + a denied audit row
 *   - authenticated,       → 403 + a denied audit row
 *     no operator/admin role
 *   - operator/admin       → handler runs; one allowed audit row records the
 *                            outcome (response status)
 *
 * Every gated mutation therefore lands exactly one audit_log row, allowed or
 * denied. Audit writes are failure-proof (`writeAudit` never throws) — an
 * audit outage logs loudly but never 500s the request.
 *
 * Compatibility (Phase 1 hard constraint): in dev-bypass mode the static
 * identity is an operator, so local dev, unit tests, and the e2e suite keep
 * working without logging in. Reads are never gated.
 *
 * This is a plain function (no `astro:middleware` import) so unit tests can
 * exercise it directly with a structural context.
 */
import type { AuthContext } from "@/lib/auth/types";
import { writeAudit } from "@/lib/audit/log";
import { canMutate, matchGatedRoute } from "./permissions";

/** Structural subset of Astro's APIContext that the guard needs. */
export interface GuardContext {
  request: Request;
  url: URL;
  locals: { auth: AuthContext };
}

/** Consistent JSON error shape for authz failures. */
export function authzError(
  status: 401 | 403,
  code: "unauthenticated" | "forbidden",
  message: string,
): Response {
  return new Response(JSON.stringify({ error: message, code }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Pull `id` out of a successful create response (best-effort, never throws). */
async function extractCreatedId(response: Response): Promise<string | null> {
  try {
    if (!response.ok) return null;
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) return null;
    const body = (await response.clone().json()) as { id?: unknown };
    return typeof body.id === "string" ? body.id : null;
  } catch {
    return null;
  }
}

export async function applyApiGuard(
  context: GuardContext,
  next: () => Promise<Response>,
): Promise<Response> {
  const gate = matchGatedRoute(context.request.method, context.url.pathname);
  if (!gate) return next();

  const auth = context.locals.auth;
  const requestDetail = {
    method: context.request.method.toUpperCase(),
    path: context.url.pathname,
  };

  if (!auth?.authenticated) {
    await writeAudit({
      actor: "anonymous",
      actorGroups: [],
      action: gate.action,
      resourceType: gate.resourceType,
      resourceId: gate.resourceId,
      detail: { ...requestDetail, outcome: "denied", reason: "unauthenticated" },
    });
    return authzError(
      401,
      "unauthenticated",
      "Authentication required for this action",
    );
  }

  const identity = auth.identity;
  if (!canMutate(identity)) {
    await writeAudit({
      actor: identity.sub,
      actorGroups: identity.groups,
      action: gate.action,
      resourceType: gate.resourceType,
      resourceId: gate.resourceId,
      detail: {
        ...requestDetail,
        outcome: "denied",
        reason: "insufficient_role",
        roles: identity.roles,
      },
    });
    return authzError(
      403,
      "forbidden",
      "This action requires the operator or admin role",
    );
  }

  const response = await next();

  const resourceId =
    gate.resourceId ??
    (gate.action === "create" ? await extractCreatedId(response) : null);

  await writeAudit({
    actor: identity.sub,
    actorGroups: identity.groups,
    action: gate.action,
    resourceType: gate.resourceType,
    resourceId,
    detail: { ...requestDetail, outcome: "allowed", status: response.status },
  });

  return response;
}
