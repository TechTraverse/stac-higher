// @vitest-environment node
/**
 * The image verbs (C-3, container-images spec §9.1, §4.3, §4.4). Rescan is
 * operator+. Exception and revoke are ADMIN, which the guard (operator-level)
 * cannot express, so the routes enforce it. The exception's reason reaches
 * the audit row through `locals.auditDetail`: exceptions are columns and
 * their history IS the audit log (spec §4.4).
 *
 * Controller rulings amending the task-5 brief (each pinned here):
 *   - A malformed path id is 400 `invalid_id` (the Task 4 `invalidImageId`
 *     helper), never a 404 — consistent with every other image route
 *     (pinned in api-images.test.ts).
 *   - Exception and revoke reject a plain operator AND a member with 403
 *     `forbidden`, in-route (the guard only gates operator+).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/images/storage", () => ({
  getImage: vi.fn(),
  requestImageScan: vi.fn(),
  grantImageException: vi.fn(),
  revokeImage: vi.fn(),
  getImageForGate: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { resetImagePolicyCache } from "@/lib/images/policy";
import {
  getImage,
  grantImageException,
  requestImageScan,
  revokeImage,
} from "@/lib/images/storage";
import { POST as rescanRoute } from "@/pages/api/images/[id]/rescan";
import { POST as exceptionRoute } from "@/pages/api/images/[id]/exception";
import { POST as revokeRoute } from "@/pages/api/images/[id]/revoke";

const IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f";
const SCAN = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e70";
const DAY = 86_400_000;

function authed(roles: CanonicalRole[], groups = ["earth-observation"]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "admin-1", email: null, name: null, groups, roles },
  };
}
const member = authed(["member"]);
const operator = authed(["operator"]);
const admin = authed(["admin"], []);

type RouteHandler = (ctx: never) => Promise<Response> | Response;

async function call(handler: RouteHandler, auth: AuthContext, body?: unknown, id = IMG) {
  const url = new URL(`http://localhost:4321/api/images/${id}/verb`);
  const locals: { auth: AuthContext; auditDetail?: Record<string, unknown> } = { auth };
  const res = await handler({
    url,
    locals,
    params: { id },
    request: new Request(url, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  } as never);
  return { res, locals };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetImagePolicyCache();
  vi.mocked(getImage).mockResolvedValue({ id: IMG, status: "approved" } as never);
});
afterEach(() => {
  vi.unstubAllEnvs();
  resetImagePolicyCache();
});

describe("POST /api/images/[id]/rescan", () => {
  it("is operator+", async () => {
    expect((await call(rescanRoute, member)).res.status).toBe(403);
    expect(requestImageScan).not.toHaveBeenCalled();
  });

  it("queues a scan and answers 202 with its id and kind", async () => {
    vi.mocked(requestImageScan).mockResolvedValue({ outcome: "requested", scan_id: SCAN, kind: "rescan" });
    const { res, locals } = await call(rescanRoute, operator);
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({ image_id: IMG, scan_id: SCAN, kind: "rescan" });
    expect(requestImageScan).toHaveBeenCalledWith(IMG, "admin-1");
    expect(locals.auditDetail).toEqual({ scan_id: SCAN, kind: "rescan" });
  });

  it("maps each refusal to its status and code", async () => {
    vi.mocked(requestImageScan).mockResolvedValue({ outcome: "not_found" });
    expect((await call(rescanRoute, operator)).res.status).toBe(404);

    vi.mocked(requestImageScan).mockResolvedValue({ outcome: "revoked" });
    let { res } = await call(rescanRoute, operator);
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("image_revoked");

    vi.mocked(requestImageScan).mockResolvedValue({ outcome: "already_pending", scan_id: SCAN });
    ({ res } = await call(rescanRoute, operator));
    expect(res.status).toBe(409);
    expect(await res.json()).toMatchObject({ code: "scan_pending", scan_id: SCAN });
  });

  it("rejects a malformed id with 400 invalid_id before any storage call", async () => {
    const { res } = await call(rescanRoute, operator, undefined, "nope");
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("invalid_id");
    expect(requestImageScan).not.toHaveBeenCalled();
  });
});

describe("POST /api/images/[id]/exception", () => {
  const inDays = (days: number) => new Date(Date.now() + days * DAY).toISOString();
  const REASON = "Upstream fix lands in 2.13; tracked in TT-12";

  it("is admin only: an operator is 403 even though the guard let them through", async () => {
    const { res } = await call(exceptionRoute, operator, { reason: REASON, expires_at: inDays(30) });
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
    expect(grantImageException).not.toHaveBeenCalled();
  });

  it("is admin only: a member is also 403", async () => {
    const { res } = await call(exceptionRoute, member, { reason: REASON, expires_at: inDays(30) });
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
    expect(grantImageException).not.toHaveBeenCalled();
  });

  it("grants, returns the image, and records the reason in the audit row", async () => {
    vi.mocked(grantImageException).mockResolvedValue({ outcome: "granted" });
    const expires = inDays(30);
    const { res, locals } = await call(exceptionRoute, admin, { reason: REASON, expires_at: expires });
    expect(res.status).toBe(200);
    expect((await res.json()).image.id).toBe(IMG);
    const input = vi.mocked(grantImageException).mock.calls[0][0];
    expect(input).toMatchObject({ imageId: IMG, reason: REASON, by: "admin-1" });
    expect(input.expiresAt.toISOString()).toBe(new Date(expires).toISOString());
    expect(locals.auditDetail).toEqual({ reason: REASON, expires_at: new Date(expires).toISOString() });
  });

  it("refuses an expiry in the past or beyond the policy's exception_max_days", async () => {
    let { res } = await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(-1) });
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("exception_expiry_invalid");

    ({ res } = await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(91) }));
    expect(res.status).toBe(422);
    const body = await res.json();
    expect(body.code).toBe("exception_too_long");
    expect(body.error).toMatch(/90 days/);
    expect(grantImageException).not.toHaveBeenCalled();
  });

  it("refuses a thin reason", async () => {
    const { res } = await call(exceptionRoute, admin, { reason: "ok", expires_at: inDays(5) });
    expect(res.status).toBe(400);
  });

  it("says why the image cannot take an exception, including the re-grant remedy (Fix A)", async () => {
    vi.mocked(grantImageException).mockResolvedValue({ outcome: "wrong_status", status: "pending" });
    const { res } = await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(5) });
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.code).toBe("image_not_exceptionable");
    expect(body.error).toMatch(/pending/);
    expect(body.error).toMatch(/replaces the exception on an approved image that carries one/);

    vi.mocked(grantImageException).mockResolvedValue({ outcome: "not_found" });
    expect((await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(5) })).res.status).toBe(404);
  });

  it("re-grants over an approved image whose own exception already expired (storage decides; the route just forwards)", async () => {
    vi.mocked(grantImageException).mockResolvedValue({ outcome: "granted" });
    vi.mocked(getImage).mockResolvedValue({ id: IMG, status: "approved" } as never);
    const { res } = await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(30) });
    expect(res.status).toBe(200);
    expect(grantImageException).toHaveBeenCalledWith(
      expect.objectContaining({ imageId: IMG, reason: REASON }),
    );
  });

  it("fails closed when the policy is unreadable", async () => {
    vi.stubEnv("PROCESS_IMAGE_POLICY_FILE", "/nonexistent/image-policy.json");
    resetImagePolicyCache();
    const { res } = await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(5) });
    expect(res.status).toBe(503);
    expect((await res.json()).code).toBe("image_policy_unavailable");
  });

  it("rejects a malformed id with 400 invalid_id before any storage call", async () => {
    const { res } = await call(exceptionRoute, admin, { reason: REASON, expires_at: inDays(5) }, "nope");
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("invalid_id");
    expect(grantImageException).not.toHaveBeenCalled();
  });
});

describe("POST /api/images/[id]/revoke", () => {
  it("is admin only: an operator is 403", async () => {
    expect((await call(revokeRoute, operator)).res.status).toBe(403);
    expect(revokeImage).not.toHaveBeenCalled();
  });

  it("is admin only: a member is also 403", async () => {
    const { res } = await call(revokeRoute, member);
    expect(res.status).toBe(403);
    expect(revokeImage).not.toHaveBeenCalled();
  });

  it("revokes and returns the image", async () => {
    vi.mocked(revokeImage).mockResolvedValue({ outcome: "revoked" });
    vi.mocked(getImage).mockResolvedValue({ id: IMG, status: "revoked" } as never);
    const { res } = await call(revokeRoute, admin);
    expect(res.status).toBe(200);
    expect((await res.json()).image.status).toBe("revoked");
  });

  it("maps not_found and already_revoked", async () => {
    vi.mocked(revokeImage).mockResolvedValue({ outcome: "not_found" });
    expect((await call(revokeRoute, admin)).res.status).toBe(404);
    vi.mocked(revokeImage).mockResolvedValue({ outcome: "already_revoked" });
    const { res } = await call(revokeRoute, admin);
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("image_already_revoked");
  });

  it("rejects a malformed id with 400 invalid_id before any storage call", async () => {
    const { res } = await call(revokeRoute, admin, undefined, "nope");
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("invalid_id");
    expect(revokeImage).not.toHaveBeenCalled();
  });
});
