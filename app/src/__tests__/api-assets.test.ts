// @vitest-environment node
// (asset access route — offline presigning, no MinIO needed)
import { describe, it, expect } from "vitest";
import { GET as assetRoute } from "@/pages/api/assets/[collection]/[item]/[asset]";
import type { AuthContext, CanonicalRole } from "@/lib/auth/types";

function authed(roles: CanonicalRole[] = ["member"]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups: ["g"], roles },
  };
}
const anonymous: AuthContext = { authenticated: false, mode: "oidc", identity: null };

function call(auth: AuthContext, params: Record<string, string>) {
  const url = new URL("http://localhost:4321/api/assets/x/y/z");
  return assetRoute({ url, locals: { auth }, request: new Request(url), params } as never);
}

const params = { collection: "sentinel-2", item: "S2A_001", asset: "B04.tif" };

describe("GET /api/assets/{collection}/{item}/{asset}", () => {
  it("403s an unauthenticated caller (done-when: unauthorized users get 403)", async () => {
    const res = await call(anonymous, params);
    expect(res.status).toBe(403);
  });

  it("302s an authenticated caller to a presigned canonical URL", async () => {
    const res = await call(authed(), params);
    expect(res.status).toBe(302);
    const location = res.headers.get("Location") ?? "";
    expect(location).toContain("/stac-higher/assets/sentinel-2/S2A_001/B04.tif");
    expect(location).toContain("X-Amz-Signature=");
    // signed, per-request URL must never be cached by a shared cache
    expect(res.headers.get("Cache-Control")).toContain("no-store");
  });

  it("400s a path-traversal asset name", async () => {
    const res = await call(authed(), { ...params, asset: ".." });
    expect(res.status).toBe(400);
  });

  it("404s when a path segment is missing", async () => {
    const res = await call(authed(), { collection: "c", item: "i", asset: "" });
    expect(res.status).toBe(404);
  });
});
