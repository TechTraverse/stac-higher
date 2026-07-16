// @vitest-environment node
// (server route — presigning is offline, so no network / no MinIO needed)
import { describe, it, expect } from "vitest";
import { POST as uploadsRoute } from "@/pages/api/uploads/index";
import { matchGatedRoute } from "@/lib/authz/permissions";
import type { AuthContext, CanonicalRole } from "@/lib/auth/types";

function authed(roles: CanonicalRole[]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups: ["g"], roles },
  };
}
const anonymous: AuthContext = { authenticated: false, mode: "oidc", identity: null };

function call(auth: AuthContext, body: unknown) {
  const url = new URL("http://localhost:4321/api/uploads");
  const request = new Request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return uploadsRoute({ url, locals: { auth }, request, params: {} } as never);
}

const validBody = {
  collection: "sentinel-2",
  item: "S2A_001",
  files: [{ filename: "B04.tif", contentType: "image/tiff" }],
};

describe("POST /api/uploads", () => {
  it("is gated as an operator+ create in the policy table", () => {
    expect(matchGatedRoute("POST", "/api/uploads")).toEqual({
      action: "create",
      resourceType: "upload",
      resourceId: null,
    });
  });

  it("401s an anonymous caller", async () => {
    const res = await call(anonymous, validBody);
    expect(res.status).toBe(401);
  });

  it("403s an authenticated member without the operator role", async () => {
    const res = await call(authed(["member"]), validBody);
    expect(res.status).toBe(403);
  });

  it("400s a malformed body", async () => {
    const res = await call(authed(["operator"]), { collection: "c" });
    expect(res.status).toBe(400);
  });

  it("400s a path-traversal collection", async () => {
    const res = await call(authed(["operator"]), { ...validBody, collection: ".." });
    expect(res.status).toBe(400);
  });

  it("returns a presigned PUT URL and the asset href for an operator", async () => {
    const res = await call(authed(["operator"]), validBody);
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      uploads: { filename: string; key: string; url: string; href: string }[];
    };
    expect(body.uploads).toHaveLength(1);
    const up = body.uploads[0];
    expect(up.key).toBe("assets/sentinel-2/S2A_001/B04.tif");
    expect(up.href).toBe("/api/assets/sentinel-2/S2A_001/B04.tif");
    expect(up.url).toContain("X-Amz-Signature=");
  });
});
