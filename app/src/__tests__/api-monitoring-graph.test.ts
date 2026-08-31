// @vitest-environment node
/**
 * /api/monitoring/graph (M5-E, P9-E): auth and the member+ scoping that keeps
 * one group's wiring out of another's picture.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/graph/storage", () => ({ loadGraph: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { loadGraph } from "@/lib/graph/storage";
import { GET as graphRoute } from "@/pages/api/monitoring/graph";

const EO = "earth-observation";

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}

const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };

function call(auth: AuthContext) {
  const url = new URL("http://localhost:4321/api/monitoring/graph");
  return (graphRoute as unknown as (ctx: never) => Promise<Response>)({
    url,
    locals: { auth },
    request: new Request(url),
    params: {},
  } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(loadGraph).mockResolvedValue({ nodes: [], edges: [] });
});

describe("GET /api/monitoring/graph", () => {
  it("401s anonymously", async () => {
    expect((await call(anon)).status).toBe(401);
  });

  it("scopes a member to their own groups", async () => {
    await call(authed(["member"]));
    expect(loadGraph).toHaveBeenCalledWith([EO]);
  });

  it("passes null for an admin — the whole graph", async () => {
    await call(authed(["admin"], []));
    expect(loadGraph).toHaveBeenCalledWith(null);
  });

  it("returns nodes and edges together", async () => {
    vi.mocked(loadGraph).mockResolvedValue({
      nodes: [
        {
          id: "coll:a",
          type: "collection",
          label: "a",
          group_id: null,
          meta: {},
        },
      ],
      edges: [],
    });
    const body = await (await call(authed(["member"]))).json();
    expect(body.nodes).toHaveLength(1);
    expect(body.edges).toEqual([]);
  });
});
