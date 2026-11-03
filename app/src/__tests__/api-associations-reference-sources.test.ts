// @vitest-environment node
/**
 * GET /api/associations/reference-sources (Z-8a): the cube sink's source
 * picker. Auth, the group filter handed to storage, and the per-collection
 * refusal PUT /cube-sink would give.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/cubes/storage", () => ({ listReferenceSourceCollections: vi.fn() }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { matchGatedRoute } from "@/lib/authz/permissions";
import { listReferenceSourceCollections } from "@/lib/cubes/storage";
import { GET } from "@/pages/api/associations/reference-sources";

function authed(roles: CanonicalRole[], groups = ["earth-observation"]): AuthContext {
  return { authenticated: true, mode: "bypass", identity: { sub: "user-1", email: null, name: null, groups, roles } };
}
const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };

function call(auth: AuthContext) {
  const url = new URL("http://localhost:4321/api/associations/reference-sources");
  return (GET as unknown as (ctx: never) => Promise<Response>)({
    url, locals: { auth }, request: new Request(url), params: {},
  } as never);
}

const assoc = (id: string, protocol: "s3" | "sftp", anonymous: boolean) => ({
  association_id: id, connection_id: `c-${id}`, protocol, anonymous,
});

beforeEach(() => {
  vi.mocked(listReferenceSourceCollections).mockReset().mockResolvedValue([]);
});

describe("GET /api/associations/reference-sources", () => {
  it("401s anonymous callers without listing", async () => {
    const res = await call(anon);
    expect(res.status).toBe(401);
    expect((await res.json()).code).toBe("unauthenticated");
    expect(listReferenceSourceCollections).not.toHaveBeenCalled();
  });

  it("a member lists within their groups", async () => {
    const res = await call(authed(["member"]));
    expect(res.status).toBe(200);
    expect(listReferenceSourceCollections).toHaveBeenCalledWith(["earth-observation"]);
  });

  it("an admin lists everything", async () => {
    await call(authed(["admin"]));
    expect(listReferenceSourceCollections).toHaveBeenCalledWith(null);
  });

  it("marks each collection with the refusal PUT would give", async () => {
    vi.mocked(listReferenceSourceCollections).mockResolvedValue([
      { collection_id: "open", title: "Open", associations: [assoc("1", "s3", true)] },
      { collection_id: "signed", title: null, associations: [assoc("2", "s3", false)] },
      { collection_id: "legacy", title: "Legacy", associations: [assoc("3", "s3", true), assoc("4", "sftp", false)] },
    ]);
    const body = await (await call(authed(["member"]))).json();
    expect(body.collections.map((c: { refusal: string | null }) => c.refusal)).toEqual([
      null, "signed_source_unsupported", "unsupported_source_protocol",
    ]);
    expect(body.collections[2]).toMatchObject({
      collection_id: "legacy", title: "Legacy",
      associations: [
        { protocol: "s3", anonymous: true },
        { protocol: "sftp", anonymous: false },
      ],
    });
  });

  it("500s with the message when the query throws", async () => {
    vi.mocked(listReferenceSourceCollections).mockRejectedValue(new Error("db down"));
    const res = await call(authed(["member"]));
    expect(res.status).toBe(500);
    expect((await res.json()).error).toBe("db down");
  });

  it("is not a gated route", () => {
    // A GET: no role gate, no audit row. The group filter lives in the SQL.
    expect(matchGatedRoute("GET", "/api/associations/reference-sources")).toBeNull();
  });
});
