// @vitest-environment node
/** GET /api/monitoring/flows (M2-D): auth + group scoping + shape stripping. */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/associations/storage", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("@/lib/associations/storage")>();
  return {
    ...original,
    listAssociationsForGroups: vi.fn(),
  };
});
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));
vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import {
  listAssociationsForGroups,
  type AssociationWithGroup,
} from "@/lib/associations/storage";
import { GET as flowsRoute } from "@/pages/api/monitoring/flows";

const mockList = vi.mocked(listAssociationsForGroups);

function association(): AssociationWithGroup {
  return {
    id: "as1",
    collection_id: "sentinel-2",
    connection_id: "c1",
    direction: "ingest",
    enabled: true,
    config: {},
    expectation: null,
    flow_stats: { files: 3 },
    created_by: "u1",
    created_at: "2026-08-01T00:00:00.000Z",
    updated_at: "2026-08-01T00:00:00.000Z",
    connection: { name: "src", protocol: "s3", status: "ok" },
    connectionGroupId: "g1",
  };
}

function authed(roles: CanonicalRole[], groups = ["g1"]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}

const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };

function call(auth: AuthContext) {
  const url = new URL("http://localhost:4321/api/monitoring/flows");
  return flowsRoute({
    url,
    locals: { auth },
    request: new Request(url),
  } as never);
}

beforeEach(() => {
  mockList.mockReset().mockResolvedValue([association()]);
});

describe("GET /api/monitoring/flows", () => {
  it("401s an unauthenticated caller", async () => {
    expect((await call(anon)).status).toBe(401);
  });

  it("scopes members to their groups and strips the group field", async () => {
    const res = await call(authed(["member"]));
    expect(res.status).toBe(200);
    expect(mockList).toHaveBeenCalledWith(["g1"]);
    const body = await res.json();
    expect(body.flows).toHaveLength(1);
    expect(body.flows[0].collection_id).toBe("sentinel-2");
    expect(body.flows[0]).not.toHaveProperty("connectionGroupId");
  });

  it("admins see all groups (null scope)", async () => {
    await call(authed(["admin"]));
    expect(mockList).toHaveBeenCalledWith(null);
  });
});
