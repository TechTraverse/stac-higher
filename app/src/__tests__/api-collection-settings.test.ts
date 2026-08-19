// @vitest-environment node
/**
 * /api/collections/[id]/settings (M2-E): auth, role, the two group rules
 * (current-owner + transfer-target), validation, and the upsert call.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/collections/settings", () => ({
  getCollectionSettings: vi.fn(),
  upsertCollectionSettings: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));
vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import {
  getCollectionSettings,
  upsertCollectionSettings,
} from "@/lib/collections/settings";
import type { CollectionSettings } from "@/lib/collections/settings";
import { GET as getRoute, PUT as putRoute } from "@/pages/api/collections/[id]/settings";

const mockGet = vi.mocked(getCollectionSettings);
const mockUpsert = vi.mocked(upsertCollectionSettings);

const EO = "earth-observation";

function settings(overrides: Partial<CollectionSettings> = {}): CollectionSettings {
  return {
    collectionId: "sentinel-2",
    groupId: null,
    externallyWritable: false,
    retentionDays: null,
    gcGraceDays: 30,
    archived: false,
    ...overrides,
  };
}

function payload(overrides: Record<string, unknown> = {}) {
  return {
    group_id: EO,
    externally_writable: true,
    retention_days: 30,
    gc_grace_days: 7,
    archived: false,
    ...overrides,
  };
}

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}

const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  auth: AuthContext,
  { method = "GET", body }: { method?: string; body?: unknown } = {},
) {
  const url = new URL("http://localhost:4321/api/collections/sentinel-2/settings");
  return handler({
    url,
    locals: { auth },
    request: new Request(url, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
    params: { id: "sentinel-2" },
  } as never);
}

beforeEach(() => {
  mockGet.mockReset().mockResolvedValue(settings());
  mockUpsert.mockReset().mockResolvedValue(settings({ groupId: EO }));
});

describe("GET /api/collections/[id]/settings", () => {
  it("401s an unauthenticated caller", async () => {
    expect((await call(getRoute, anon)).status).toBe(401);
  });

  it("returns the (default-applied) settings to any member", async () => {
    const res = await call(getRoute, authed(["member"]));
    expect(res.status).toBe(200);
    expect((await res.json()).gcGraceDays).toBe(30);
  });
});

describe("PUT /api/collections/[id]/settings", () => {
  it("401/403s non-operators", async () => {
    expect(
      (await call(putRoute, anon, { method: "PUT", body: payload() })).status,
    ).toBe(401);
    expect(
      (await call(putRoute, authed(["member"]), { method: "PUT", body: payload() }))
        .status,
    ).toBe(403);
  });

  it("upserts for an operator on an unowned collection (ADR 0003)", async () => {
    const res = await call(putRoute, authed(["operator"]), {
      method: "PUT",
      body: payload(),
    });
    expect(res.status).toBe(200);
    expect(mockUpsert).toHaveBeenCalledWith("sentinel-2", {
      groupId: EO,
      externallyWritable: true,
      retentionDays: 30,
      gcGraceDays: 7,
      archived: false,
    });
  });

  it("403s an operator on a collection owned by another group", async () => {
    mockGet.mockResolvedValue(settings({ groupId: "other-group" }));
    const res = await call(putRoute, authed(["operator"]), {
      method: "PUT",
      body: payload(),
    });
    expect(res.status).toBe(403);
    expect(mockUpsert).not.toHaveBeenCalled();
  });

  it("403s a transfer to a group the caller is not in", async () => {
    const res = await call(putRoute, authed(["operator"]), {
      method: "PUT",
      body: payload({ group_id: "someone-elses-group" }),
    });
    expect(res.status).toBe(403);
    expect(mockUpsert).not.toHaveBeenCalled();
  });

  it("admins bypass both group rules", async () => {
    mockGet.mockResolvedValue(settings({ groupId: "other-group" }));
    const res = await call(putRoute, authed(["admin"], []), {
      method: "PUT",
      body: payload({ group_id: "someone-elses-group" }),
    });
    expect(res.status).toBe(200);
  });

  it("400s invalid documents (retention floor, unknown keys)", async () => {
    for (const body of [
      payload({ retention_days: 0 }),
      payload({ gc_grace_days: -1 }),
      payload({ bogus: true }),
      { group_id: null }, // missing required fields
    ]) {
      const res = await call(putRoute, authed(["operator"]), {
        method: "PUT",
        body,
      });
      expect(res.status).toBe(400);
    }
    expect(mockUpsert).not.toHaveBeenCalled();
  });
});
