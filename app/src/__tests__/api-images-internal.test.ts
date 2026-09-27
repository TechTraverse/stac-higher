// @vitest-environment node
/**
 * C-3: the read-only policy route and the internal admission probe
 * (container-images spec §9.1, §12). The probe is the seam K-6's Kyverno
 * policy calls; it answers "could this digest LAUNCH", the same statuses
 * and window the pipeline enforces (spec §4.3, §8.4).
 *
 * Controller ruling (F3, security): /api/internal/images/approved FAILS
 * CLOSED. When INTERNAL_API_TOKEN is unset or empty, the route answers 404
 * (as if it did not exist) — never a silent "no token required". When it is
 * set, a request needs the header X-Internal-Token to match, compared in
 * constant time; missing/wrong/mismatched-length is 401.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/images/storage", () => ({
  isDigestLaunchable: vi.fn(),
  getImageForGate: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import type { AuthContext } from "@/lib/auth/types";
import { resetImagePolicyCache } from "@/lib/images/policy";
import { isDigestLaunchable } from "@/lib/images/storage";
import { GET as policyRoute } from "@/pages/api/processes/image-policy";
import { GET as approvedRoute } from "@/pages/api/internal/images/approved";

const DIGEST = "sha256:" + "c".repeat(64);
const TOKEN = "s3cret-token";
const member: AuthContext = {
  authenticated: true,
  mode: "bypass",
  identity: { sub: "user-1", email: null, name: null, groups: ["earth-observation"], roles: ["member"] },
};
const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  { auth = anon, search = "", headers = {} }: { auth?: AuthContext; search?: string; headers?: Record<string, string> } = {},
) {
  const url = new URL(`http://localhost:4321/api/x${search}`);
  return handler({ url, locals: { auth }, params: {}, request: new Request(url, { headers }) } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  resetImagePolicyCache();
});
afterEach(() => {
  vi.unstubAllEnvs();
  resetImagePolicyCache();
});

describe("GET /api/processes/image-policy", () => {
  it("serves the policy to members without the scanner's resource limits", async () => {
    const res = await call(policyRoute, { auth: member });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.allowed_registries).toContain("ghcr.io");
    expect(body.exception_max_days).toBe(90);
    expect(body.scan_window_days).toBe(30);
    expect(body).not.toHaveProperty("scan_limits");
  });

  it("is 401 for anonymous and 503 when the policy is unreadable", async () => {
    expect((await call(policyRoute)).status).toBe(401);
    vi.stubEnv("PROCESS_IMAGE_POLICY_FILE", "/nonexistent/image-policy.json");
    resetImagePolicyCache();
    const res = await call(policyRoute, { auth: member });
    expect(res.status).toBe(503);
    expect((await res.json()).code).toBe("image_policy_unavailable");
  });
});

describe("GET /api/internal/images/approved", () => {
  const HEADERS = { "X-Internal-Token": TOKEN };

  beforeEach(() => {
    vi.stubEnv("INTERNAL_API_TOKEN", TOKEN);
  });

  it("answers whether a digest could launch, with the policy's window", async () => {
    vi.mocked(isDigestLaunchable).mockResolvedValue(true);
    const res = await call(approvedRoute, { search: `?digest=${DIGEST}`, headers: HEADERS });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ approved: true });
    expect(isDigestLaunchable).toHaveBeenCalledWith(DIGEST, 30);
  });

  it("needs no user session (only the internal token), and refuses a malformed digest", async () => {
    vi.mocked(isDigestLaunchable).mockResolvedValue(false);
    expect(await (await call(approvedRoute, { search: `?digest=${DIGEST}`, headers: HEADERS })).json()).toEqual({
      approved: false,
    });
    expect((await call(approvedRoute, { search: "?digest=sha256:ABC", headers: HEADERS })).status).toBe(400);
    expect((await call(approvedRoute, { headers: HEADERS })).status).toBe(400);
    expect(isDigestLaunchable).toHaveBeenCalledTimes(1);
  });

  it("is 404, as if the route did not exist, when no INTERNAL_API_TOKEN is configured", async () => {
    vi.stubEnv("INTERNAL_API_TOKEN", "");
    vi.mocked(isDigestLaunchable).mockResolvedValue(true);
    expect((await call(approvedRoute, { search: `?digest=${DIGEST}` })).status).toBe(404);
    expect((await call(approvedRoute, { search: `?digest=${DIGEST}`, headers: HEADERS })).status).toBe(404);
    expect(isDigestLaunchable).not.toHaveBeenCalled();
  });

  it("requires the shared token when the deployment sets one (401 on missing or wrong)", async () => {
    vi.mocked(isDigestLaunchable).mockResolvedValue(true);
    expect((await call(approvedRoute, { search: `?digest=${DIGEST}` })).status).toBe(401);
    expect(
      (await call(approvedRoute, { search: `?digest=${DIGEST}`, headers: { "X-Internal-Token": "wrong" } })).status,
    ).toBe(401);
    const ok = await call(approvedRoute, { search: `?digest=${DIGEST}`, headers: HEADERS });
    expect(ok.status).toBe(200);
  });

  it("rejects a token of a different length without throwing (constant-time compare)", async () => {
    vi.mocked(isDigestLaunchable).mockResolvedValue(true);
    const res = await call(approvedRoute, {
      search: `?digest=${DIGEST}`,
      headers: { "X-Internal-Token": "short" },
    });
    expect(res.status).toBe(401);
  });

  it("fails closed (503) without a policy", async () => {
    vi.stubEnv("PROCESS_IMAGE_POLICY_FILE", "/nonexistent/image-policy.json");
    resetImagePolicyCache();
    const res = await call(approvedRoute, { search: `?digest=${DIGEST}`, headers: HEADERS });
    expect(res.status).toBe(503);
    expect(isDigestLaunchable).not.toHaveBeenCalled();
  });
});
