// @vitest-environment node
// (server-side route — no DOM involved)
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/audit/log", () => ({
  listAuditEntries: vi.fn(async () => ({ entries: [], nextCursor: null })),
}));

import { listAuditEntries } from "@/lib/audit/log";
import { GET } from "@/pages/api/audit";
import type {
  AuthContext,
  CanonicalRole,
} from "@/lib/auth/types";

const mockList = vi.mocked(listAuditEntries);

function authed(roles: CanonicalRole[], groups = ["earth-observation"]): AuthContext {
  return {
    authenticated: true,
    mode: "oidc",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}

const anonymous: AuthContext = {
  authenticated: false,
  mode: "oidc",
  identity: null,
};

function call(auth: AuthContext, search = "") {
  const url = new URL(`http://localhost:4321/api/audit${search}`);
  return GET({
    url,
    locals: { auth },
    request: new Request(url),
  } as unknown as Parameters<typeof GET>[0]);
}

beforeEach(() => {
  mockList.mockClear();
  mockList.mockResolvedValue({ entries: [], nextCursor: null });
});

describe("GET /api/audit", () => {
  it("rejects anonymous with 401", async () => {
    const res = await call(anonymous);
    expect(res.status).toBe(401);
    expect((await res.json()).code).toBe("unauthenticated");
    expect(mockList).not.toHaveBeenCalled();
  });

  it("rejects members with 403", async () => {
    const res = await call(authed(["member"]));
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
    expect(mockList).not.toHaveBeenCalled();
  });

  it("operators see only their own groups' rows", async () => {
    const res = await call(authed(["operator"], ["weather"]));
    expect(res.status).toBe(200);
    expect(mockList).toHaveBeenCalledWith(
      expect.objectContaining({ groups: ["weather"] }),
    );
  });

  it("admins see all rows (no group filter)", async () => {
    const res = await call(authed(["admin"], ["weather"]));
    expect(res.status).toBe(200);
    expect(mockList).toHaveBeenCalledWith(
      expect.objectContaining({ groups: null }),
    );
  });

  it("passes pagination params through, clamping the limit", async () => {
    await call(authed(["operator"]), "?limit=9999&before=120");
    expect(mockList).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 200, before: "120" }),
    );
  });

  it("defaults the limit to 50", async () => {
    await call(authed(["operator"]));
    expect(mockList).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 50, before: null }),
    );
  });

  it("rejects malformed pagination params with 400", async () => {
    expect((await call(authed(["operator"]), "?limit=zero")).status).toBe(400);
    expect((await call(authed(["operator"]), "?limit=-5")).status).toBe(400);
    expect((await call(authed(["operator"]), "?before=abc")).status).toBe(400);
    expect(mockList).not.toHaveBeenCalled();
  });

  it("returns entries and nextCursor", async () => {
    const entry = {
      id: "7",
      actor: "user-1",
      actorGroups: ["weather"],
      action: "login",
      resourceType: "session",
      resourceId: null,
      detail: {},
      at: "2026-07-14T12:00:00.000Z",
    };
    mockList.mockResolvedValueOnce({ entries: [entry], nextCursor: "7" });
    const res = await call(authed(["admin"]));
    expect(await res.json()).toEqual({ entries: [entry], nextCursor: "7" });
  });

  it("returns 500 with the standard error shape on a query failure", async () => {
    mockList.mockRejectedValueOnce(new Error("db down"));
    const res = await call(authed(["admin"]));
    expect(res.status).toBe(500);
    expect((await res.json()).error).toBe("db down");
  });
});
