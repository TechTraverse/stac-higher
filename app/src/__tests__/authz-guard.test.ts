// @vitest-environment node
// (server-side authz code — no DOM involved)
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/audit/log", () => ({
  writeAudit: vi.fn(async () => true),
}));

import { writeAudit } from "@/lib/audit/log";
import { applyApiGuard, type GuardContext } from "@/lib/authz/guard";
import {
  canMutate,
  isAdmin,
  matchGatedRoute,
} from "@/lib/authz/permissions";
import type {
  AuthContext,
  CanonicalIdentity,
  CanonicalRole,
} from "@/lib/auth/types";

const mockWriteAudit = vi.mocked(writeAudit);

function identity(roles: CanonicalRole[], groups = ["earth-observation"]): CanonicalIdentity {
  return { sub: "user-1", email: "u@example.com", name: "User", groups, roles };
}

function authed(roles: CanonicalRole[], groups?: string[]): AuthContext {
  return { authenticated: true, mode: "oidc", identity: identity(roles, groups) };
}

const anonymous: AuthContext = { authenticated: false, mode: "oidc", identity: null };

function makeContext(
  method: string,
  path: string,
  auth: AuthContext,
): GuardContext {
  const url = new URL(`http://localhost:4321${path}`);
  return {
    request: new Request(url, { method }),
    url,
    locals: { auth },
  };
}

function okNext(status = 200, body: unknown = { ok: true }) {
  return vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
  );
}

beforeEach(() => {
  mockWriteAudit.mockClear();
});

describe("matchGatedRoute", () => {
  it("gates extension mutations", () => {
    expect(matchGatedRoute("POST", "/api/extensions")).toEqual({
      action: "create",
      resourceType: "extension",
      resourceId: null,
    });
    expect(matchGatedRoute("POST", "/api/extensions/import")).toMatchObject({
      action: "create",
    });
    expect(matchGatedRoute("PUT", "/api/extensions/abc-123")).toEqual({
      action: "update",
      resourceType: "extension",
      resourceId: "abc-123",
    });
    expect(matchGatedRoute("DELETE", "/api/extensions/abc-123/")).toEqual({
      action: "delete",
      resourceType: "extension",
      resourceId: "abc-123",
    });
  });

  it("leaves reads and read-shaped utilities open", () => {
    expect(matchGatedRoute("GET", "/api/extensions")).toBeNull();
    expect(matchGatedRoute("GET", "/api/extensions/abc-123")).toBeNull();
    expect(matchGatedRoute("GET", "/api/extensions/abc-123/schema")).toBeNull();
    expect(matchGatedRoute("POST", "/api/extensions/preview")).toBeNull();
    expect(matchGatedRoute("POST", "/api/extensions/resolve-schema")).toBeNull();
    expect(matchGatedRoute("POST", "/api/proxy")).toBeNull();
    expect(matchGatedRoute("GET", "/api/audit")).toBeNull();
    expect(matchGatedRoute("GET", "/collections")).toBeNull();
  });
});

describe("capability checks (§7 matrix)", () => {
  it("member cannot mutate; operator and admin can", () => {
    expect(canMutate(identity(["member"]))).toBe(false);
    expect(canMutate(identity([]))).toBe(false);
    expect(canMutate(identity(["operator"]))).toBe(true);
    expect(canMutate(identity(["admin"]))).toBe(true);
    expect(canMutate(identity(["member", "operator"]))).toBe(true);
  });

  it("isAdmin only for admin", () => {
    expect(isAdmin(identity(["operator"]))).toBe(false);
    expect(isAdmin(identity(["admin"]))).toBe(true);
  });
});

describe("applyApiGuard — permission matrix", () => {
  it("anonymous mutation → 401 with the consistent error shape + denied audit row", async () => {
    const next = okNext();
    const res = await applyApiGuard(
      makeContext("POST", "/api/extensions", anonymous),
      next,
    );
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({
      error: "Authentication required for this action",
      code: "unauthenticated",
    });
    expect(next).not.toHaveBeenCalled();
    expect(mockWriteAudit).toHaveBeenCalledTimes(1);
    expect(mockWriteAudit).toHaveBeenCalledWith(
      expect.objectContaining({
        actor: "anonymous",
        actorGroups: [],
        action: "create",
        resourceType: "extension",
        detail: expect.objectContaining({
          outcome: "denied",
          reason: "unauthenticated",
        }),
      }),
    );
  });

  it("member mutation → 403 + denied audit row", async () => {
    const next = okNext();
    const res = await applyApiGuard(
      makeContext("DELETE", "/api/extensions/ext-1", authed(["member"])),
      next,
    );
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({
      error: "This action requires the operator or admin role",
      code: "forbidden",
    });
    expect(next).not.toHaveBeenCalled();
    expect(mockWriteAudit).toHaveBeenCalledWith(
      expect.objectContaining({
        actor: "user-1",
        actorGroups: ["earth-observation"],
        action: "delete",
        resourceId: "ext-1",
        detail: expect.objectContaining({
          outcome: "denied",
          reason: "insufficient_role",
          roles: ["member"],
        }),
      }),
    );
  });

  it.each([["operator"], ["admin"]] as const)(
    "%s mutation → handler runs + allowed audit row with the response status",
    async (role) => {
      const next = okNext(201, { id: "new-ext-9" });
      const res = await applyApiGuard(
        makeContext("POST", "/api/extensions", authed([role])),
        next,
      );
      expect(res.status).toBe(201);
      expect(next).toHaveBeenCalledTimes(1);
      expect(mockWriteAudit).toHaveBeenCalledTimes(1);
      expect(mockWriteAudit).toHaveBeenCalledWith(
        expect.objectContaining({
          actor: "user-1",
          action: "create",
          resourceType: "extension",
          // created id extracted from the response body
          resourceId: "new-ext-9",
          detail: expect.objectContaining({ outcome: "allowed", status: 201 }),
        }),
      );
    },
  );

  it("allowed audit row still lands when the handler fails (status recorded)", async () => {
    const next = okNext(500, { error: "boom" });
    const res = await applyApiGuard(
      makeContext("PUT", "/api/extensions/ext-2", authed(["operator"])),
      next,
    );
    expect(res.status).toBe(500);
    expect(mockWriteAudit).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "update",
        resourceId: "ext-2",
        detail: expect.objectContaining({ outcome: "allowed", status: 500 }),
      }),
    );
  });

  it("does not consume the response body when extracting the created id", async () => {
    const next = okNext(201, { id: "new-ext-9" });
    const res = await applyApiGuard(
      makeContext("POST", "/api/extensions", authed(["operator"])),
      next,
    );
    // The caller can still read the body after the guard audited it.
    expect(await res.json()).toEqual({ id: "new-ext-9" });
  });

  it.each([
    ["GET", "/api/extensions", anonymous],
    ["GET", "/api/extensions/ext-1", authed(["member"])],
    ["POST", "/api/extensions/preview", anonymous],
    ["POST", "/api/extensions/resolve-schema", authed(["member"])],
  ] as const)(
    "read/utility %s %s passes through with no audit row",
    async (method, path, auth) => {
      const next = okNext();
      const res = await applyApiGuard(makeContext(method, path, auth), next);
      expect(res.status).toBe(200);
      expect(next).toHaveBeenCalledTimes(1);
      expect(mockWriteAudit).not.toHaveBeenCalled();
    },
  );
});
