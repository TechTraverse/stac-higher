// @vitest-environment node
/** GET /api/collections/[id]/settings/impact (M2-F): the dry-run counts. */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));

import type { AuthContext } from "@/lib/auth/types";
import { query } from "@/lib/db/connection";
import { GET as impactRoute } from "@/pages/api/collections/[id]/settings/impact";

const mockQuery = vi.mocked(query);

const member: AuthContext = {
  authenticated: true,
  mode: "bypass",
  identity: { sub: "u1", email: null, name: null, groups: ["g1"], roles: ["member"] },
};
const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };

function call(auth: AuthContext, search = "") {
  const url = new URL(
    `http://localhost:4321/api/collections/sentinel-2/settings/impact${search}`,
  );
  return impactRoute({
    url,
    locals: { auth },
    request: new Request(url),
    params: { id: "sentinel-2" },
  } as never);
}

beforeEach(() => {
  mockQuery.mockReset();
});

describe("GET /api/collections/[id]/settings/impact", () => {
  it("401s an unauthenticated caller", async () => {
    expect((await call(anon)).status).toBe(401);
  });

  it("counts total and already-expired items for a retention window", async () => {
    mockQuery
      .mockResolvedValueOnce({ rows: [{ count: "40" }], rowCount: 1 } as never)
      .mockResolvedValueOnce({ rows: [{ count: "12" }], rowCount: 1 } as never);
    const res = await call(member, "?retention_days=14");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ total_items: 40, expired_items: 12 });
    expect(mockQuery.mock.calls[1][1]).toEqual(["sentinel-2", 14]);
  });

  it("archived counts everything", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [{ count: "7" }], rowCount: 1 } as never);
    const res = await call(member, "?archived=true");
    expect(await res.json()).toEqual({ total_items: 7, expired_items: 7 });
  });

  it("400s a non-positive retention_days", async () => {
    expect((await call(member, "?retention_days=0")).status).toBe(400);
  });

  // --- count cap (W-2) --------------------------------------------------- //

  it("counts the items beyond the newest N for a count cap", async () => {
    mockQuery
      .mockResolvedValueOnce({ rows: [{ count: "5" }], rowCount: 1 } as never)
      .mockResolvedValueOnce({ rows: [{ count: "3" }], rowCount: 1 } as never);
    const res = await call(member, "?retention_max_items=2");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ total_items: 5, expired_items: 3 });
    const [sql, params] = mockQuery.mock.calls[1];
    expect(sql).toMatch(/ORDER BY datetime DESC, id DESC\s+OFFSET \$2/);
    expect(sql).not.toMatch(/make_interval/);
    expect(params).toEqual(["sentinel-2", 2]);
  });

  it("answers with the DISTINCT union when both rules are given", async () => {
    // What the dialog shows must equal what the sweep deletes: an item that is
    // both old AND beyond the cap counts once.
    mockQuery
      .mockResolvedValueOnce({ rows: [{ count: "40" }], rowCount: 1 } as never)
      .mockResolvedValueOnce({ rows: [{ count: "15" }], rowCount: 1 } as never);
    const res = await call(member, "?retention_days=14&retention_max_items=30");
    expect(await res.json()).toEqual({ total_items: 40, expired_items: 15 });
    const [sql, params] = mockQuery.mock.calls[1];
    expect(sql).toMatch(/count\(DISTINCT id\)/);
    expect(sql).toMatch(/make_interval/);
    expect(sql).toMatch(/OFFSET/);
    expect(params).toEqual(["sentinel-2", 14, 30]);
  });

  it("400s a non-positive retention_max_items", async () => {
    expect((await call(member, "?retention_max_items=0")).status).toBe(400);
    expect((await call(member, "?retention_max_items=1.5")).status).toBe(400);
  });

  it("degrades to null counts when pgstac is absent", async () => {
    mockQuery.mockRejectedValue(new Error('relation "pgstac.items" does not exist'));
    const res = await call(member, "?retention_days=14");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ total_items: null, expired_items: null });
  });
});
