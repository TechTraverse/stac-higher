// @vitest-environment node
/**
 * /api/alerts/unread + /api/alerts/read (M2-C): the per-user read watermark.
 * Storage is mocked; under test are auth, admin scoping, and the shapes.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/alerts/storage", () => ({
  countUnreadAlerts: vi.fn(),
  markAlertsRead: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { countUnreadAlerts, markAlertsRead } from "@/lib/alerts/storage";
import { GET as unreadRoute } from "@/pages/api/alerts/unread";
import { POST as readRoute } from "@/pages/api/alerts/read";

const mockCount = vi.mocked(countUnreadAlerts);
const mockMark = vi.mocked(markAlertsRead);

const EO = "earth-observation";

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}

const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(handler: RouteHandler, auth: AuthContext, method = "GET") {
  const url = new URL("http://localhost:4321/api/alerts/unread");
  return handler({
    url,
    locals: { auth },
    request: new Request(url, { method }),
  } as never);
}

beforeEach(() => {
  mockCount.mockReset().mockResolvedValue(3);
  mockMark.mockReset().mockResolvedValue("2026-08-19T00:00:00.000Z");
});

describe("GET /api/alerts/unread", () => {
  it("401s an unauthenticated caller", async () => {
    expect((await call(unreadRoute, anon)).status).toBe(401);
  });

  it("counts for the caller, scoped to their groups", async () => {
    const res = await call(unreadRoute, authed(["member"]));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ unread: 3 });
    expect(mockCount).toHaveBeenCalledWith("user-1", [EO]);
  });

  it("admins count across all groups (null scope)", async () => {
    await call(unreadRoute, authed(["admin"]));
    expect(mockCount).toHaveBeenCalledWith("user-1", null);
  });
});

describe("POST /api/alerts/read", () => {
  it("401s an unauthenticated caller", async () => {
    expect((await call(readRoute, anon, "POST")).status).toBe(401);
  });

  it("advances the caller's watermark (member+, not operator-gated)", async () => {
    const res = await call(readRoute, authed(["member"]), "POST");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ last_read_at: "2026-08-19T00:00:00.000Z" });
    expect(mockMark).toHaveBeenCalledWith("user-1");
  });
});
