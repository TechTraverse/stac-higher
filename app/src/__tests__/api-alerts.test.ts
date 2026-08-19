// @vitest-environment node
/**
 * /api/alerts routes (M2-B): list scoping + the ack/resolve transitions.
 * Storage is mocked; what's under test is auth, group scoping, param
 * validation, and the state-conflict mapping.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/alerts/storage", () => ({
  listAlerts: vi.fn(),
  getAlert: vi.fn(),
  ackAlert: vi.fn(),
  resolveAlert: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import {
  ackAlert,
  getAlert,
  listAlerts,
  resolveAlert,
  type ApiAlert,
} from "@/lib/alerts/storage";
import { GET as listRoute } from "@/pages/api/alerts/index";
import { POST as ackRoute } from "@/pages/api/alerts/[id]/ack";
import { POST as resolveRoute } from "@/pages/api/alerts/[id]/resolve";

const mockList = vi.mocked(listAlerts);
const mockGet = vi.mocked(getAlert);
const mockAck = vi.mocked(ackAlert);
const mockResolve = vi.mocked(resolveAlert);

const ALERT_ID = "3a9f1c2e-0000-4000-8000-0000000000e1";
const EO = "earth-observation";

function alert(overrides: Partial<ApiAlert> = {}): ApiAlert {
  return {
    id: ALERT_ID,
    source: "health",
    kind: "connection_error",
    connection_id: "3a9f1c2e-0000-4000-8000-000000000001",
    association_id: null,
    channel_id: null,
    state: "firing",
    message: "connection 'src' failing health checks: boom",
    first_seen: "2026-08-18T00:00:00.000Z",
    last_seen: "2026-08-18T01:00:00.000Z",
    acknowledged_at: null,
    acknowledged_by: null,
    resolved_at: null,
    group_id: EO,
    connection_name: "src",
    collection_id: null,
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
  path: string,
  method = "GET",
) {
  const url = new URL(`http://localhost:4321${path}`);
  return handler({
    url,
    locals: { auth },
    request: new Request(url, { method }),
    params: { id: ALERT_ID },
  } as never);
}

beforeEach(() => {
  mockList.mockReset().mockResolvedValue([alert()]);
  mockGet.mockReset().mockResolvedValue(alert());
  mockAck.mockReset().mockResolvedValue(alert({ state: "acknowledged" }));
  mockResolve.mockReset().mockResolvedValue(alert({ state: "resolved" }));
});

describe("GET /api/alerts", () => {
  it("401s an unauthenticated caller", async () => {
    const res = await call(listRoute, anon, "/api/alerts");
    expect(res.status).toBe(401);
  });

  it("scopes members to their groups", async () => {
    const res = await call(listRoute, authed(["member"]), "/api/alerts");
    expect(res.status).toBe(200);
    expect(mockList).toHaveBeenCalledWith([EO], { state: undefined, limit: undefined });
  });

  it("admins see all groups (null scope)", async () => {
    await call(listRoute, authed(["admin"]), "/api/alerts?state=open&limit=20");
    expect(mockList).toHaveBeenCalledWith(null, { state: "open", limit: 20 });
  });

  it("400s a bad state or limit", async () => {
    expect(
      (await call(listRoute, authed(["member"]), "/api/alerts?state=bogus")).status,
    ).toBe(400);
    expect(
      (await call(listRoute, authed(["member"]), "/api/alerts?limit=0")).status,
    ).toBe(400);
    expect(
      (await call(listRoute, authed(["member"]), "/api/alerts?limit=999")).status,
    ).toBe(400);
  });
});

describe("POST /api/alerts/[id]/ack", () => {
  const path = `/api/alerts/${ALERT_ID}/ack`;

  it("401s anonymous and 403s a member (defense in depth under the guard)", async () => {
    expect((await call(ackRoute, anon, path, "POST")).status).toBe(401);
    expect((await call(ackRoute, authed(["member"]), path, "POST")).status).toBe(403);
  });

  it("404s an operator outside the alert's group (no id leak)", async () => {
    mockGet.mockResolvedValue(alert({ group_id: "other-group" }));
    const res = await call(ackRoute, authed(["operator"]), path, "POST");
    expect(res.status).toBe(404);
    expect(mockAck).not.toHaveBeenCalled();
  });

  it("acknowledges for an in-group operator, stamping the actor", async () => {
    const res = await call(ackRoute, authed(["operator"]), path, "POST");
    expect(res.status).toBe(200);
    expect(mockAck).toHaveBeenCalledWith(ALERT_ID, "user-1");
    const body = await res.json();
    expect(body.state).toBe("acknowledged");
  });

  it("409s when the alert is not firing", async () => {
    mockGet.mockResolvedValue(alert({ state: "resolved" }));
    mockAck.mockResolvedValue(null);
    const res = await call(ackRoute, authed(["admin"]), path, "POST");
    expect(res.status).toBe(409);
  });

  it("admin-only when the derived group is gone", async () => {
    mockGet.mockResolvedValue(alert({ group_id: null }));
    expect((await call(ackRoute, authed(["operator"]), path, "POST")).status).toBe(404);
    expect((await call(ackRoute, authed(["admin"]), path, "POST")).status).toBe(200);
  });
});

describe("POST /api/alerts/[id]/resolve", () => {
  const path = `/api/alerts/${ALERT_ID}/resolve`;

  it("resolves an open alert for an in-group operator", async () => {
    mockGet.mockResolvedValue(alert({ state: "acknowledged" }));
    const res = await call(resolveRoute, authed(["operator"]), path, "POST");
    expect(res.status).toBe(200);
    expect(mockResolve).toHaveBeenCalledWith(ALERT_ID);
  });

  it("409s an already-resolved alert", async () => {
    mockGet.mockResolvedValue(alert({ state: "resolved" }));
    mockResolve.mockResolvedValue(null);
    const res = await call(resolveRoute, authed(["admin"]), path, "POST");
    expect(res.status).toBe(409);
  });
});
